package service

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"log"
	"net/http"
	"strings"
	"sync"
	"time"
	"unicode"

	"ai-localbase/internal/model"
)

// QueryTranslation 描述一次「检索问题 → 英文检索式」的翻译结果。
type QueryTranslation struct {
	// Original 用户原始问题
	Original string
	// English 实际用于知识库检索的问题；未翻译时等于 Original
	English string
	// Applied 表示本次确实使用了翻译后的英文问题检索
	Applied bool
	// CacheHit 表示命中翻译缓存
	CacheHit bool
	// Reason 判定与执行结果，便于调试
	Reason string
	// ElapsedMs 翻译耗时（毫秒）
	ElapsedMs int64
}

// 翻译结果原因
const (
	queryTranslationReasonEmpty      = "empty_query"
	queryTranslationReasonDisabled   = "disabled"
	queryTranslationReasonNotNeeded  = "already_english"
	queryTranslationReasonTooLong    = "query_too_long"
	queryTranslationReasonTranslated = "translated"
	queryTranslationReasonCacheHit   = "cache_hit"
	queryTranslationReasonFailed     = "translation_failed"
	queryTranslationReasonUnusable   = "translation_unusable"
)

// 翻译模式
const (
	// QueryTranslationModeLLM 复用对话大模型翻译（兼容模式，CPU 上较慢）
	QueryTranslationModeLLM = "llm"
	// QueryTranslationModeMT 调用本地机翻服务翻译（推荐，百毫秒级）
	QueryTranslationModeMT = "mt"
)

const (
	queryTranslationCacheCapacity  = 512
	queryTranslationOutputMaxRunes = 600
	queryTranslationDefaultTimeout = 20 * time.Second
	// normalizeChatConfig 会把 <=0 的温度改写为 0.7，这里取一个极小正数近似贪心解码
	queryTranslationTemperature = 0.01
)

// QueryTranslator 把非英文检索问题翻译为英文，供英文文献知识库检索使用。
type QueryTranslator interface {
	TranslateToEnglish(ctx context.Context, query string) QueryTranslation
}

// QueryTranslationConfig 翻译器配置。
type QueryTranslationConfig struct {
	// Mode 取 llm 或 mt，空值按 llm 处理
	Mode           string
	Enabled        bool
	Provider       string
	BaseURL        string
	APIKey         string
	Model          string
	TimeoutSeconds int
}

func (c QueryTranslationConfig) timeout() time.Duration {
	timeout := time.Duration(c.TimeoutSeconds) * time.Second
	if timeout <= 0 {
		timeout = queryTranslationDefaultTimeout
	}
	return timeout
}

// IsMTMode 判断是否使用机翻服务。
func (c QueryTranslationConfig) IsMTMode() bool {
	return strings.EqualFold(strings.TrimSpace(c.Mode), QueryTranslationModeMT)
}

// ── 通用流程：判断 → 缓存 → 翻译 → 校验 → 回退 ─────────────────────────────

type translationCache struct {
	mu     sync.RWMutex
	values map[string]string
	order  []string
}

func newTranslationCache() *translationCache {
	return &translationCache{values: map[string]string{}}
}

func (c *translationCache) get(key string) (string, bool) {
	if c == nil {
		return "", false
	}
	c.mu.RLock()
	defer c.mu.RUnlock()
	value, ok := c.values[key]
	return value, ok
}

func (c *translationCache) put(key, value string) {
	if c == nil {
		return
	}
	c.mu.Lock()
	defer c.mu.Unlock()
	if c.values == nil {
		c.values = map[string]string{}
	}
	if _, exists := c.values[key]; !exists {
		c.order = append(c.order, key)
	}
	c.values[key] = value
	for len(c.order) > queryTranslationCacheCapacity {
		oldest := c.order[0]
		c.order = c.order[1:]
		delete(c.values, oldest)
	}
}

type translateFunc func(ctx context.Context, query string) (string, error)

// runQueryTranslation 执行所有翻译后端共用的流程：
// 空问题/未启用/无需翻译直接透传；命中缓存即时返回；
// 翻译失败或结果不可用时回退原文，保证检索链路不中断。
func runQueryTranslation(
	ctx context.Context,
	query string,
	enabled bool,
	maxRunes int,
	cache *translationCache,
	do translateFunc,
) QueryTranslation {
	result := QueryTranslation{Original: query, English: query}

	trimmed := strings.TrimSpace(query)
	if trimmed == "" {
		result.Reason = queryTranslationReasonEmpty
		return result
	}
	if !enabled || do == nil {
		result.Reason = queryTranslationReasonDisabled
		return result
	}
	// 判断机制：只有出现中日韩文字的检索问题才需要翻译，纯英文直接透传
	if !containsCJKText(trimmed) {
		result.Reason = queryTranslationReasonNotNeeded
		return result
	}
	if maxRunes > 0 && len([]rune(trimmed)) > maxRunes {
		log.Printf("query translation skipped: query exceeds %d runes", maxRunes)
		result.Reason = queryTranslationReasonTooLong
		return result
	}
	if cached, ok := cache.get(trimmed); ok {
		result.English = cached
		result.Applied = true
		result.CacheHit = true
		result.Reason = queryTranslationReasonCacheHit
		return result
	}

	startedAt := time.Now()
	translated, err := do(ctx, trimmed)
	result.ElapsedMs = time.Since(startedAt).Milliseconds()
	if err != nil {
		log.Printf("query translation failed, falling back to original query: %v", err)
		result.Reason = queryTranslationReasonFailed
		return result
	}
	if !isUsableEnglishQuery(translated, trimmed) {
		log.Printf("query translation unusable, falling back to original query: %q", translated)
		result.Reason = queryTranslationReasonUnusable
		return result
	}

	cache.put(trimmed, translated)
	result.English = translated
	result.Applied = true
	result.Reason = queryTranslationReasonTranslated
	return result
}

// ── 机翻服务实现（推荐）────────────────────────────────────────────────────

// MTQueryTranslator 调用本地机翻服务（scripts/mt_service.py）翻译检索问题。
// 机翻模型很小（OPUS-MT 约 77M 参数），CPU 上单句约 100~400ms，远快于大模型。
type MTQueryTranslator struct {
	baseURL  string
	apiKey   string
	client   *http.Client
	timeout  time.Duration
	enabled  bool
	maxRunes int
	cache    *translationCache
}

// NewMTQueryTranslator 创建机翻翻译器；BaseURL 形如 http://host.docker.internal:8090
func NewMTQueryTranslator(cfg QueryTranslationConfig) *MTQueryTranslator {
	return &MTQueryTranslator{
		baseURL:  strings.TrimSpace(cfg.BaseURL),
		apiKey:   strings.TrimSpace(cfg.APIKey),
		client:   &http.Client{},
		timeout:  cfg.timeout(),
		enabled:  cfg.Enabled,
		maxRunes: queryTranslationOutputMaxRunes,
		cache:    newTranslationCache(),
	}
}

// TranslateToEnglish 判断是否需要翻译并调用机翻服务。
func (t *MTQueryTranslator) TranslateToEnglish(ctx context.Context, query string) QueryTranslation {
	if t == nil || t.client == nil {
		return QueryTranslation{Original: query, English: query, Reason: queryTranslationReasonDisabled}
	}
	enabled := t.enabled && strings.TrimSpace(t.baseURL) != ""
	return runQueryTranslation(ctx, query, enabled, t.maxRunes, t.cache, t.translate)
}

type mtTranslateRequest struct {
	Text string `json:"text"`
}

type mtTranslateResponse struct {
	Translation string  `json:"translation"`
	CacheHit    bool    `json:"cacheHit"`
	ElapsedMs   float64 `json:"elapsedMs"`
}

func (t *MTQueryTranslator) translate(ctx context.Context, query string) (string, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	runCtx, cancel := context.WithTimeout(ctx, t.timeout)
	defer cancel()

	payload, err := json.Marshal(mtTranslateRequest{Text: query})
	if err != nil {
		return "", err
	}

	request, err := http.NewRequestWithContext(runCtx, http.MethodPost, mtTranslateEndpoint(t.baseURL), bytes.NewReader(payload))
	if err != nil {
		return "", err
	}
	request.Header.Set("Content-Type", "application/json")
	if t.apiKey != "" {
		request.Header.Set("Authorization", "Bearer "+t.apiKey)
	}

	response, err := t.client.Do(request)
	if err != nil {
		return "", fmt.Errorf("call mt service: %w", err)
	}
	defer response.Body.Close()

	body, err := io.ReadAll(io.LimitReader(response.Body, 1<<20))
	if err != nil {
		return "", fmt.Errorf("read mt response: %w", err)
	}
	if response.StatusCode != http.StatusOK {
		return "", fmt.Errorf("mt service status %d: %s", response.StatusCode, strings.TrimSpace(string(body)))
	}

	var parsed mtTranslateResponse
	if err := json.Unmarshal(body, &parsed); err != nil {
		return "", fmt.Errorf("decode mt response: %w", err)
	}
	if strings.TrimSpace(parsed.Translation) == "" {
		return "", fmt.Errorf("mt service returned empty translation")
	}
	return parsed.Translation, nil
}

// mtTranslateEndpoint 拼接机翻接口地址，允许 BaseURL 已带 /translate。
func mtTranslateEndpoint(baseURL string) string {
	base := strings.TrimRight(strings.TrimSpace(baseURL), "/")
	if strings.HasSuffix(base, "/translate") {
		return base
	}
	return base + "/translate"
}

// ── LLM 实现（兼容保留）────────────────────────────────────────────────────

// LLMQueryTranslator 基于 LLM 的问题翻译器。
// 复用对话模型虽然省配置，但 CPU 上单次需数秒，建议改用 MTQueryTranslator。
type LLMQueryTranslator struct {
	llmSvc     *LLMService
	chatConfig func() model.ChatModelConfig
	enabled    bool
	timeout    time.Duration
	maxRunes   int
	cache      *translationCache

	providerOverride string
	baseURLOverride  string
	apiKeyOverride   string
	modelOverride    string
}

// NewLLMQueryTranslator 创建 LLM 翻译器。
func NewLLMQueryTranslator(llmSvc *LLMService, cfg QueryTranslationConfig) *LLMQueryTranslator {
	return &LLMQueryTranslator{
		llmSvc:           llmSvc,
		enabled:          cfg.Enabled,
		timeout:          cfg.timeout(),
		maxRunes:         queryTranslationOutputMaxRunes,
		cache:            newTranslationCache(),
		providerOverride: strings.TrimSpace(cfg.Provider),
		baseURLOverride:  strings.TrimSpace(cfg.BaseURL),
		apiKeyOverride:   strings.TrimSpace(cfg.APIKey),
		modelOverride:    strings.TrimSpace(cfg.Model),
	}
}

// SetChatConfigProvider 注入兜底 Chat 配置（未单独指定翻译模型时复用）。
func (t *LLMQueryTranslator) SetChatConfigProvider(provider func() model.ChatModelConfig) {
	if t == nil {
		return
	}
	t.chatConfig = provider
}

// TranslateToEnglish 判断是否需要翻译并执行 LLM 翻译。
// 纯英文（拉丁字母）问题直接透传，避免不必要的模型调用。
func (t *LLMQueryTranslator) TranslateToEnglish(ctx context.Context, query string) QueryTranslation {
	if t == nil || t.llmSvc == nil {
		return QueryTranslation{Original: query, English: query, Reason: queryTranslationReasonDisabled}
	}
	return runQueryTranslation(ctx, query, t.enabled, t.maxRunes, t.cache, t.translate)
}

func (t *LLMQueryTranslator) translate(ctx context.Context, query string) (string, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	runCtx, cancel := context.WithTimeout(ctx, t.timeout)
	defer cancel()

	cfg := t.resolveConfig()
	cfg.Temperature = queryTranslationTemperature

	request := model.ChatCompletionRequest{
		Messages: []model.ChatMessage{{Role: "user", Content: buildQueryTranslationPrompt(query)}},
		Config:   cfg,
	}
	resp, err := t.llmSvc.ChatContext(runCtx, modelRuntimePriorityLow, request)
	if err != nil {
		return "", err
	}
	if len(resp.Choices) == 0 {
		return "", fmt.Errorf("empty translation response")
	}
	return sanitizeTranslatedQuery(resp.Choices[0].Message.Content), nil
}

func (t *LLMQueryTranslator) resolveConfig() model.ChatModelConfig {
	cfg := model.ChatModelConfig{}
	if t.chatConfig != nil {
		cfg = t.chatConfig()
	}
	if t.providerOverride != "" {
		cfg.Provider = t.providerOverride
	}
	if t.baseURLOverride != "" {
		cfg.BaseURL = t.baseURLOverride
	}
	if t.apiKeyOverride != "" {
		cfg.APIKey = t.apiKeyOverride
	}
	if t.modelOverride != "" {
		cfg.Model = t.modelOverride
	}
	return cfg
}

// NewQueryTranslator 按配置创建翻译器：mt 走本地机翻服务，其余走 LLM。
func NewQueryTranslator(llmSvc *LLMService, cfg QueryTranslationConfig) QueryTranslator {
	if cfg.IsMTMode() {
		return NewMTQueryTranslator(cfg)
	}
	return NewLLMQueryTranslator(llmSvc, cfg)
}

// containsCJKText 判断文本是否包含汉字/假名/谚文。
func containsCJKText(text string) bool {
	for _, r := range text {
		if isCJKTextRune(r) {
			return true
		}
	}
	return false
}

func isCJKTextRune(r rune) bool {
	return unicode.Is(unicode.Han, r) ||
		unicode.Is(unicode.Hiragana, r) ||
		unicode.Is(unicode.Katakana, r) ||
		unicode.Is(unicode.Hangul, r)
}

// buildQueryTranslationPrompt 生成翻译指令：要求单行、术语保真、无多余输出。
func buildQueryTranslationPrompt(query string) string {
	return strings.Join([]string{
		"你是学术文献检索翻译器。把下面的问题翻译成简洁的英文检索查询，用于检索英文文献数据库。",
		"要求：",
		"1. 保留专业术语、缩写、基因名、药物名、数字与单位；",
		"2. 只输出一行英文查询，不要解释、不要引号、不要编号、不要 Markdown；",
		"3. 问题若已是英文，原样输出。",
		"问题：" + query,
		"英文查询：",
	}, "\n")
}

// sanitizeTranslatedQuery 清理模型输出，只保留一行纯英文查询。
func sanitizeTranslatedQuery(raw string) string {
	text := strings.TrimSpace(raw)
	if text == "" {
		return ""
	}

	for _, line := range strings.Split(text, "\n") {
		if trimmed := strings.TrimSpace(line); trimmed != "" {
			text = trimmed
			break
		}
	}

	for _, prefix := range []string{
		"英文查询：", "英文查询:", "英文：", "英文:",
		"翻译结果：", "翻译结果:", "翻译：", "翻译:",
		"Translation:", "Translated query:", "English query:", "Query:",
	} {
		if strings.HasPrefix(text, prefix) {
			text = strings.TrimSpace(strings.TrimPrefix(text, prefix))
		}
	}

	text = strings.Trim(text, "\"'“”‘’「」『』`")
	text = strings.TrimSpace(text)

	if runes := []rune(text); len(runes) > queryTranslationOutputMaxRunes {
		text = strings.TrimSpace(string(runes[:queryTranslationOutputMaxRunes]))
	}
	return text
}

// isUsableEnglishQuery 校验翻译结果：非空、不再含中文、且与原文不同。
func isUsableEnglishQuery(translated, original string) bool {
	trimmed := strings.TrimSpace(translated)
	if trimmed == "" {
		return false
	}
	if containsCJKText(trimmed) {
		return false
	}
	if strings.EqualFold(trimmed, strings.TrimSpace(original)) {
		return false
	}
	return true
}

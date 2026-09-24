package service

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"hash/fnv"
	"io"
	"log"
	"net"
	"net/http"
	"sort"
	"strings"
	"sync"
	"time"
	"unicode"
	"unicode/utf8"

	"ai-localbase/internal/model"
	"ai-localbase/internal/util"
)

const (
	defaultChunkSize    = 450
	defaultChunkOverlap = 60
	defaultTopK         = 5
	embeddingBatchSize  = 32
)

type RagService struct {
	client *http.Client
	cache  *lruEmbeddingCache
	qdrant *QdrantService
}

// QueryRewriteResult 查询重写结果
// RewrittenQueries 包含原始 query 和重写后的变体
// OriginalQuery 保留原始输入
// RewrittenQueries 不保证顺序，但会去重与去空
type QueryRewriteResult struct {
	OriginalQuery    string
	RewrittenQueries []string
}

// QueryRewriter 查询重写器接口
// Rewrite 接收原始 query 与最近对话历史，返回重写结果
type QueryRewriter interface {
	Rewrite(ctx context.Context, query string, conversationHistory []string) (QueryRewriteResult, error)
}

// LLMQueryRewriter 基于 LLM 的查询重写器
// maxVariants 最大变体数，默认 3
type LLMQueryRewriter struct {
	llmSvc      *LLMService
	maxVariants int
	chatConfig  func() model.ChatModelConfig
}

// NewLLMQueryRewriter 创建 LLM 查询重写器
func NewLLMQueryRewriter(llmSvc *LLMService, maxVariants int) *LLMQueryRewriter {
	if maxVariants <= 0 {
		maxVariants = 3
	}
	return &LLMQueryRewriter{llmSvc: llmSvc, maxVariants: maxVariants}
}

// SetChatConfigProvider 注入 Chat 配置提供函数
func (r *LLMQueryRewriter) SetChatConfigProvider(provider func() model.ChatModelConfig) {
	if r == nil {
		return
	}
	r.chatConfig = provider
}

func (r *LLMQueryRewriter) SetMaxVariants(maxVariants int) {
	if r == nil {
		return
	}
	if maxVariants < 1 {
		maxVariants = 1
	}
	if maxVariants > 5 {
		maxVariants = 5
	}
	r.maxVariants = maxVariants
}

// Rewrite 使用 LLM 将原始 query 重写为多个变体
func (r *LLMQueryRewriter) Rewrite(ctx context.Context, query string, conversationHistory []string) (QueryRewriteResult, error) {
	result := QueryRewriteResult{OriginalQuery: query}
	if r == nil || r.llmSvc == nil {
		return result, fmt.Errorf("llm query rewriter is nil")
	}
	trimmedQuery := strings.TrimSpace(query)
	if trimmedQuery == "" {
		return result, fmt.Errorf("empty query")
	}

	maxVariants := r.maxVariants
	if maxVariants <= 0 {
		maxVariants = 3
	}

	prompt := buildQueryRewritePrompt(trimmedQuery, conversationHistory, maxVariants)
	request := model.ChatCompletionRequest{
		Messages: []model.ChatMessage{{Role: "user", Content: prompt}},
	}
	if r.chatConfig != nil {
		request.Config = r.chatConfig()
	}

	resp, err := r.llmSvc.Chat(request)
	if err != nil {
		return result, fmt.Errorf("llm rewrite query: %w", err)
	}
	if len(resp.Choices) == 0 {
		return result, fmt.Errorf("llm rewrite empty response")
	}

	lines := parseQueryRewriteLines(resp.Choices[0].Message.Content)
	unique := make([]string, 0, len(lines))
	seen := make(map[string]struct{}, len(lines))
	for _, item := range lines {
		clean := strings.TrimSpace(item)
		if clean == "" {
			continue
		}
		key := strings.ToLower(clean)
		if _, exists := seen[key]; exists {
			continue
		}
		seen[key] = struct{}{}
		unique = append(unique, clean)
		if len(unique) >= maxVariants {
			break
		}
	}

	result.RewrittenQueries = unique
	return result, nil
}

type DocumentChunk struct {
	ID              string
	KnowledgeBaseID string
	DocumentID      string
	DocumentName    string
	Text            string
	Index           int
	Kind            string
}

// SparseVector 稀疏向量（词项索引 -> 权重）
type SparseVector struct {
	Indices []uint32
	Values  []float32
}

// EncodeSparseTexts calls a BGE-M3 lexical-weight service. The service
// contract is POST /sparse-embeddings with {model,input} and
// {data:[{indices:[],values:[]}]} response.
func (s *RagService) EncodeSparseTexts(ctx context.Context, baseURL, modelName, apiKey string, texts []string) ([]SparseVector, error) {
	if strings.TrimSpace(baseURL) == "" {
		return nil, fmt.Errorf("sparse encoder endpoint is not configured")
	}
	payload, err := json.Marshal(map[string]any{"model": modelName, "input": texts})
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, strings.TrimRight(baseURL, "/")+"/sparse-embeddings", bytes.NewReader(payload))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	if strings.TrimSpace(apiKey) != "" {
		req.Header.Set("Authorization", "Bearer "+strings.TrimSpace(apiKey))
	}
	resp, err := s.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	if resp.StatusCode >= http.StatusBadRequest {
		return nil, fmt.Errorf("sparse encoder returned http %d", resp.StatusCode)
	}
	var result struct {
		Data []struct {
			Indices []uint32  `json:"indices"`
			Values  []float32 `json:"values"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, err
	}
	output := make([]SparseVector, 0, len(result.Data))
	for _, item := range result.Data {
		output = append(output, SparseVector{Indices: item.Indices, Values: item.Values})
	}
	return output, nil
}

type RetrievedChunk struct {
	DocumentChunk
	Score             float64
	RawScore          float64
	RetrievalChannels []string
	DenseRank         int
	SparseRank        int
}

type EmbeddingDimensionMismatchError struct {
	BatchItem int
	Expected  int
	Actual    int
	Cached    bool
}

func (e *EmbeddingDimensionMismatchError) Error() string {
	location := fmt.Sprintf(" at batch item %d", e.BatchItem)
	if e.Cached {
		location = " in cached embedding"
	}
	return fmt.Sprintf(
		"embedding dimension mismatch%s: QDRANT_VECTOR_SIZE=%d, model returned %d; update QDRANT_VECTOR_SIZE, use a new QDRANT_COLLECTION_PREFIX, and rebuild the index",
		location,
		e.Expected,
		e.Actual,
	)
}

type openAIEmbeddingRequest struct {
	Model string   `json:"model"`
	Input []string `json:"input"`
}

type openAIEmbeddingResponse struct {
	Data []struct {
		Embedding []float64 `json:"embedding"`
		Index     int       `json:"index"`
	} `json:"data"`
	Error *struct {
		Message string `json:"message"`
	} `json:"error,omitempty"`
}

// Ollama native /api/embed
type ollamaEmbedRequest struct {
	Model string   `json:"model"`
	Input []string `json:"input"`
}

type ollamaEmbedResponse struct {
	Embeddings [][]float64 `json:"embeddings"`
	Error      string      `json:"error,omitempty"`
}

func NewRagService() *RagService {
	transport := &http.Transport{
		Proxy:                 http.ProxyFromEnvironment,
		DialContext:           (&net.Dialer{Timeout: 5 * time.Second, KeepAlive: 30 * time.Second}).DialContext,
		ForceAttemptHTTP2:     true,
		MaxIdleConns:          16,
		MaxIdleConnsPerHost:   4,
		IdleConnTimeout:       60 * time.Second,
		TLSHandshakeTimeout:   5 * time.Second,
		ExpectContinueTimeout: 1 * time.Second,
		ResponseHeaderTimeout: 45 * time.Second,
	}
	return &RagService{
		client: &http.Client{
			Timeout:   60 * time.Second,
			Transport: transport,
		},
		cache: newLRUEmbeddingCache(2048),
	}
}

// SetQdrantService 注入 Qdrant 服务
func (s *RagService) SetQdrantService(qdrant *QdrantService) {
	if s == nil {
		return
	}
	s.qdrant = qdrant
}

func (s *RagService) ChunkText(text string) []string {
	cfg := util.DefaultSemanticChunkConfig()
	cfg.MaxChunkSize = defaultChunkSize
	cfg.OverlapSize = defaultChunkOverlap
	return util.ChunkText(text, util.ChunkStrategySemantic, cfg)
}

// SemanticChunkByEmbeddingConfig 基于 embedding 语义相似度的分块参数。
type SemanticChunkByEmbeddingConfig struct {
	TargetSize          int     // 目标块长度（rune 数）
	MaxSize             int     // 单块硬上限
	MinSize             int     // 低于该长度的块会被合并
	SimilarityThreshold float64 // 相邻句子相似度低于该值视为语义跳变点
}

// DefaultSemanticChunkByEmbeddingConfig 基于目标长度推导默认参数：
// 上限上浮 25%，下限为目标的一半，语义跳变阈值 0.72。
func DefaultSemanticChunkByEmbeddingConfig(target int) SemanticChunkByEmbeddingConfig {
	if target <= 0 {
		target = defaultChunkSize
	}
	return SemanticChunkByEmbeddingConfig{
		TargetSize:          target,
		MaxSize:             target + target/4,
		MinSize:             target / 2,
		SimilarityThreshold: 0.72,
	}
}

type semanticSentenceUnit struct {
	text   string
	para   int
	length int
	vector []float64
}

// ChunkTextSemanticByEmbedding 基于 embedding 语义相似度切分文本。
//
// 规则：
//  1. 先按段落/句子边界切成原子句子（不切断句子）；
//  2. 计算相邻句子的 embedding 余弦相似度；
//  3. 在「段落边界」「语义跳变点（相似度低于阈值）」或「长度超上限」处切分，
//     使每块长度围绕 TargetSize 浮动；
//  4. 过短的块按语义相似度合并到相邻块（优先相似度更高的一侧）。
func (s *RagService) ChunkTextSemanticByEmbedding(
	ctx context.Context,
	text string,
	cfg SemanticChunkByEmbeddingConfig,
	embeddingCfg model.EmbeddingModelConfig,
	vectorSize int,
) ([]string, error) {
	if cfg.TargetSize <= 0 {
		cfg = DefaultSemanticChunkByEmbeddingConfig(defaultChunkSize)
	}
	if cfg.MaxSize < cfg.TargetSize {
		cfg.MaxSize = cfg.TargetSize
	}
	if cfg.MinSize <= 0 || cfg.MinSize > cfg.TargetSize {
		cfg.MinSize = cfg.TargetSize / 2
	}

	paragraphSentences := util.SplitTextIntoParagraphSentences(text)
	if len(paragraphSentences) == 0 {
		return nil, nil
	}

	units := make([]semanticSentenceUnit, 0)
	for paraIndex, sentences := range paragraphSentences {
		for _, sentence := range sentences {
			units = append(units, semanticSentenceUnit{
				text:   sentence,
				para:   paraIndex,
				length: util.RuneCount(sentence),
			})
		}
	}
	if len(units) == 0 {
		return nil, nil
	}
	if len(units) == 1 {
		return splitOversizeSemanticText(units[0].text, cfg.MaxSize), nil
	}

	texts := make([]string, len(units))
	for i := range units {
		texts[i] = units[i].text
	}
	vectors, err := s.EmbedTexts(ctx, embeddingCfg, texts, vectorSize)
	if err != nil {
		return nil, fmt.Errorf("embed sentences for semantic chunking: %w", err)
	}
	if len(vectors) != len(units) {
		return nil, fmt.Errorf("embedding count mismatch: got %d, want %d", len(vectors), len(units))
	}
	for i := range units {
		units[i].vector = vectors[i]
	}

	// similarities[i] = 句子 i 与句子 i+1 的语义相似度
	similarities := make([]float64, len(units)-1)
	for i := 0; i+1 < len(units); i++ {
		similarities[i] = float64(cosineSimilarity(
			float64ToFloat32(units[i].vector),
			float64ToFloat32(units[i+1].vector),
		))
	}

	groups := make([][]int, 0, len(units)/2+1)
	current := make([]int, 0, 8)
	currentLength := 0

	flush := func() {
		if len(current) == 0 {
			return
		}
		group := make([]int, len(current))
		copy(group, current)
		groups = append(groups, group)
		current = current[:0]
		currentLength = 0
	}

	for i := range units {
		if len(current) > 0 {
			last := current[len(current)-1]
			wouldExceed := currentLength+1+units[i].length > cfg.MaxSize
			paragraphBreak := units[i].para != units[last].para
			reachedTarget := currentLength >= cfg.TargetSize
			semanticGap := similarities[i-1] < cfg.SimilarityThreshold

			if wouldExceed || (paragraphBreak && currentLength >= cfg.MinSize) || (reachedTarget && semanticGap) {
				flush()
			}
		}
		if len(current) > 0 {
			currentLength += 1 + units[i].length
		} else {
			currentLength = units[i].length
		}
		current = append(current, i)
	}
	flush()

	groups = mergeShortSemanticGroups(groups, units, similarities, cfg)

	chunks := make([]string, 0, len(groups))
	for _, group := range groups {
		parts := make([]string, 0, len(group))
		for _, index := range group {
			parts = append(parts, units[index].text)
		}
		joined := strings.TrimSpace(strings.Join(parts, " "))
		if joined == "" {
			continue
		}
		// 单个句子若本身超过硬上限（例如无句末标点的长段落），按窗口兜底切分。
		if len(group) == 1 && util.RuneCount(joined) > cfg.MaxSize {
			chunks = append(chunks, splitOversizeSemanticText(joined, cfg.MaxSize)...)
			continue
		}
		chunks = append(chunks, joined)
	}
	return chunks, nil
}

// semanticGroupLength 计算一组句子拼接后的字符长度（句子间以空格连接）。
func semanticGroupLength(group []int, units []semanticSentenceUnit) int {
	total := 0
	for i, index := range group {
		if i > 0 {
			total++
		}
		total += units[index].length
	}
	return total
}

// mergeShortSemanticGroups 把过短的块按语义相似度合并到相邻块：
// 优先合并到相似度更高的一侧；若该侧合并后会超过上限，则尝试另一侧。
func mergeShortSemanticGroups(
	groups [][]int,
	units []semanticSentenceUnit,
	similarities []float64,
	cfg SemanticChunkByEmbeddingConfig,
) [][]int {
	if len(groups) <= 1 {
		return groups
	}

	maxRounds := len(groups) + 1
	for round := 0; round < maxRounds; round++ {
		changed := false
		for i := 0; i < len(groups) && len(groups) > 1; i++ {
			currentLength := semanticGroupLength(groups[i], units)
			if currentLength >= cfg.MinSize {
				continue
			}

			simPrev, simNext := -1.0, -1.0
			prevLength, nextLength := 0, 0
			if i > 0 {
				simPrev = similarities[groups[i-1][len(groups[i-1])-1]]
				prevLength = semanticGroupLength(groups[i-1], units)
			}
			if i+1 < len(groups) {
				simNext = similarities[groups[i][len(groups[i])-1]]
				nextLength = semanticGroupLength(groups[i+1], units)
			}

			canMergePrev := i > 0 && prevLength+1+currentLength <= cfg.MaxSize
			canMergeNext := i+1 < len(groups) && currentLength+1+nextLength <= cfg.MaxSize

			switch {
			case canMergePrev && canMergeNext:
				if simPrev >= simNext {
					groups[i-1] = append(groups[i-1], groups[i]...)
				} else {
					groups[i+1] = append(groups[i], groups[i+1]...)
				}
				groups = append(groups[:i], groups[i+1:]...)
				changed = true
			case canMergePrev:
				groups[i-1] = append(groups[i-1], groups[i]...)
				groups = append(groups[:i], groups[i+1:]...)
				changed = true
			case canMergeNext:
				groups[i+1] = append(groups[i], groups[i+1]...)
				groups = append(groups[:i], groups[i+1:]...)
				changed = true
			}
		}
		if !changed {
			break
		}
	}
	return groups
}

// splitOversizeSemanticText 在单句本身就超过上限时按窗口强制切分。
func splitOversizeSemanticText(text string, maxSize int) []string {
	if maxSize <= 0 {
		return []string{text}
	}
	runes := []rune(text)
	if len(runes) <= maxSize {
		return []string{text}
	}
	parts := make([]string, 0, len(runes)/maxSize+1)
	for start := 0; start < len(runes); start += maxSize {
		end := start + maxSize
		if end > len(runes) {
			end = len(runes)
		}
		parts = append(parts, strings.TrimSpace(string(runes[start:end])))
	}
	return parts
}

// BuildDocumentChunksWithParts 把已切好的文本片段组装为文档 chunk，并把结构化摘要块前置。
func (s *RagService) BuildDocumentChunksWithParts(document model.Document, parts []string, text string) []DocumentChunk {
	chunks := make([]DocumentChunk, 0, len(parts))
	nextIndex := 0
	for index, part := range parts {
		chunks = append(chunks, DocumentChunk{
			ID:              fmt.Sprintf("%s-chunk-%d", document.ID, index),
			KnowledgeBaseID: document.KnowledgeBaseID,
			DocumentID:      document.ID,
			DocumentName:    document.Name,
			Text:            part,
			Index:           nextIndex,
			Kind:            classifyDocumentChunkKind(part),
		})
		nextIndex++
	}

	summaryChunks := buildStructuredSummaryChunks(document, text, nextIndex)
	chunks = append(summaryFirstChunks(summaryChunks), chunks...)
	return chunks
}

// BuildDocumentChunks 使用规则（段落/标点边界）分块。
func (s *RagService) BuildDocumentChunks(document model.Document, text string) []DocumentChunk {
	return s.BuildDocumentChunksWithParts(document, s.ChunkText(text), text)
}

// BuildDocumentChunksSemantic 使用 embedding 语义相似度分块；
// 失败时回退规则分块，保证索引流程不会因 embedding 异常而中断。
func (s *RagService) BuildDocumentChunksSemantic(
	ctx context.Context,
	document model.Document,
	text string,
	embeddingCfg model.EmbeddingModelConfig,
	vectorSize int,
) []DocumentChunk {
	cfg := DefaultSemanticChunkByEmbeddingConfig(defaultChunkSize)
	parts, err := s.ChunkTextSemanticByEmbedding(ctx, text, cfg, embeddingCfg, vectorSize)
	if err != nil {
		log.Printf("semantic chunking failed for %q, fallback to rule-based chunking: %v", document.Name, err)
		return s.BuildDocumentChunks(document, text)
	}
	if len(parts) == 0 {
		return s.BuildDocumentChunks(document, text)
	}
	return s.BuildDocumentChunksWithParts(document, parts, text)
}

func buildStructuredSummaryChunks(document model.Document, text string, startIndex int) []DocumentChunk {
	blocks := extractStructuredSummaryBlocks(text)
	if len(blocks) == 0 {
		return nil
	}
	chunks := make([]DocumentChunk, 0, len(blocks))
	for i, block := range blocks {
		chunks = append(chunks, DocumentChunk{
			ID:              fmt.Sprintf("%s-summary-%d", document.ID, i),
			KnowledgeBaseID: document.KnowledgeBaseID,
			DocumentID:      document.ID,
			DocumentName:    document.Name,
			Text:            block,
			Index:           startIndex + i,
			Kind:            "structured_summary",
		})
	}
	return chunks
}

func extractStructuredSummaryBlocks(text string) []string {
	lines := strings.Split(strings.ReplaceAll(text, "\r\n", "\n"), "\n")
	blocks := make([]string, 0)
	current := make([]string, 0)
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if !strings.HasPrefix(trimmed, "统计摘要：") {
			if len(current) > 0 {
				blocks = append(blocks, strings.Join(current, "\n"))
				current = current[:0]
			}
			continue
		}
		current = append(current, trimmed)
	}
	if len(current) > 0 {
		blocks = append(blocks, strings.Join(current, "\n"))
	}
	return blocks
}

func classifyDocumentChunkKind(text string) string {
	trimmed := strings.TrimSpace(text)
	if trimmed == "" {
		return "text"
	}
	if strings.HasPrefix(trimmed, "统计摘要：") {
		return "structured_summary"
	}
	if strings.Contains(trimmed, "第") && strings.Contains(trimmed, "行：") {
		return "structured_row"
	}
	return "text"
}

func summaryFirstChunks(chunks []DocumentChunk) []DocumentChunk {
	if len(chunks) == 0 {
		return nil
	}
	out := make([]DocumentChunk, len(chunks))
	copy(out, chunks)
	return out
}

func (s *RagService) EmbedTexts(ctx context.Context, cfg model.EmbeddingModelConfig, texts []string, vectorSize int) ([][]float64, error) {
	trimmed := make([]string, 0, len(texts))
	for _, text := range texts {
		value := strings.TrimSpace(text)
		if value != "" {
			trimmed = append(trimmed, value)
		}
	}
	if len(trimmed) == 0 {
		return nil, nil
	}
	if vectorSize <= 0 {
		return nil, fmt.Errorf("embedding vector size must be greater than zero")
	}

	all := make([][]float64, 0, len(trimmed))
	for i := 0; i < len(trimmed); i += embeddingBatchSize {
		end := i + embeddingBatchSize
		if end > len(trimmed) {
			end = len(trimmed)
		}
		batch := trimmed[i:end]

		// Check cache first; only call the configured embedding service for misses.
		batchVectors := make([][]float64, len(batch))
		uncachedIdx := make([]int, 0, len(batch))
		uncachedTexts := make([]string, 0, len(batch))
		for j, text := range batch {
			key := embeddingCacheKey(cfg.Provider, cfg.BaseURL, cfg.Model, text)
			if cached := s.cache.Get(key); cached != nil {
				if len(cached) != vectorSize {
					return nil, &EmbeddingDimensionMismatchError{
						BatchItem: i + j,
						Expected:  vectorSize,
						Actual:    len(cached),
						Cached:    true,
					}
				}
				batchVectors[j] = cached
				continue
			}
			uncachedIdx = append(uncachedIdx, j)
			uncachedTexts = append(uncachedTexts, text)
		}

		if len(uncachedTexts) > 0 {
			embeddings, err := s.requestEmbeddings(ctx, cfg, uncachedTexts)
			if err != nil {
				return nil, fmt.Errorf("embedding request failed: %w", err)
			}
			if len(embeddings) != len(uncachedTexts) {
				return nil, fmt.Errorf("embedding response count mismatch: expected %d, got %d", len(uncachedTexts), len(embeddings))
			}
			for k, vector := range embeddings {
				if len(vector) != vectorSize {
					return nil, &EmbeddingDimensionMismatchError{
						BatchItem: i + uncachedIdx[k],
						Expected:  vectorSize,
						Actual:    len(vector),
					}
				}
				idx := uncachedIdx[k]
				batchVectors[idx] = vector
				key := embeddingCacheKey(cfg.Provider, cfg.BaseURL, cfg.Model, batch[idx])
				s.cache.Set(key, vector)
			}
		}

		all = append(all, batchVectors...)
	}
	return all, nil
}

func (s *RagService) BuildContext(chunks []RetrievedChunk) (string, []map[string]string) {
	if len(chunks) == 0 {
		return "", nil
	}

	lines := make([]string, 0, len(chunks))
	sources := make([]map[string]string, 0, len(chunks))
	for _, chunk := range chunks {
		lines = append(lines, fmt.Sprintf("[%s#%d] %s", chunk.DocumentName, chunk.Index+1, chunk.Text))
		sources = append(sources, map[string]string{
			"knowledgeBaseId": chunk.KnowledgeBaseID,
			"documentId":      chunk.DocumentID,
			"documentName":    chunk.DocumentName,
			"chunkId":         chunk.ID,
			"chunkIndex":      fmt.Sprintf("%d", chunk.Index+1),
			"chunkKind":       chunk.Kind,
			"score":           fmt.Sprintf("%.4f", chunk.Score),
			"snippet":         truncateRunes(strings.TrimSpace(chunk.Text), 220),
		})
	}

	return strings.Join(lines, "\n\n"), sources
}

func buildQueryRewritePrompt(query string, history []string, maxVariants int) string {
	if maxVariants <= 0 {
		maxVariants = 3
	}
	trimmedHistory := make([]string, 0, len(history))
	for _, item := range history {
		clean := strings.TrimSpace(item)
		if clean != "" {
			trimmedHistory = append(trimmedHistory, clean)
		}
	}
	historyText := "无"
	if len(trimmedHistory) > 0 {
		historyText = strings.Join(trimmedHistory, "\n")
	}
	return fmt.Sprintf(
		"你是一个搜索优化专家。请将以下问题改写为%d个不同角度的搜索查询，每行一个，不要编号。\n对话历史：%s\n原始问题：%s\n改写后的查询：",
		maxVariants,
		historyText,
		query,
	)
}

func parseQueryRewriteLines(content string) []string {
	if strings.TrimSpace(content) == "" {
		return nil
	}
	lines := strings.Split(strings.ReplaceAll(content, "\r\n", "\n"), "\n")
	cleaned := make([]string, 0, len(lines))
	for _, line := range lines {
		trimmed := strings.TrimSpace(line)
		if trimmed == "" {
			continue
		}
		trimmed = strings.TrimLeft(trimmed, "-•*\t ")
		trimmed = strings.TrimSpace(trimmed)
		if trimmed == "" {
			continue
		}
		cleaned = append(cleaned, trimmed)
	}
	return cleaned
}

// MultiQuerySearch 对多个 query 并行检索并融合结果
// deduplication: 相同 DocumentID+ChunkID 的结果只保留最高分
func (r *RagService) MultiQuerySearch(
	ctx context.Context,
	queries []string,
	collectionName string,
	topK int,
	threshold float32,
	embeddingConfig model.EmbeddingModelConfig,
) ([]RetrievedChunk, error) {
	if r == nil {
		return nil, fmt.Errorf("rag service is nil")
	}
	trimmed := make([]string, 0, len(queries))
	seen := make(map[string]struct{}, len(queries))
	for _, q := range queries {
		clean := strings.TrimSpace(q)
		if clean == "" {
			continue
		}
		key := strings.ToLower(clean)
		if _, exists := seen[key]; exists {
			continue
		}
		seen[key] = struct{}{}
		trimmed = append(trimmed, clean)
	}
	if len(trimmed) == 0 {
		return nil, nil
	}
	if topK <= 0 {
		topK = defaultTopK
	}

	if r.qdrant == nil || !r.qdrant.IsEnabled() {
		return nil, nil
	}

	sem := make(chan struct{}, 3)
	var wg sync.WaitGroup
	resultChan := make(chan []RetrievedChunk, len(trimmed))
	errChan := make(chan error, len(trimmed))

	for _, query := range trimmed {
		wg.Add(1)
		go func(q string) {
			defer wg.Done()
			select {
			case sem <- struct{}{}:
				defer func() { <-sem }()
			case <-ctx.Done():
				errChan <- ctx.Err()
				return
			}

			vectors, err := r.EmbedTexts(ctx, embeddingConfig, []string{q}, r.qdrant.vectorSize)
			if err != nil {
				errChan <- fmt.Errorf("embed query: %w", err)
				return
			}
			if len(vectors) == 0 {
				errChan <- fmt.Errorf("embed query: embedding api returned no vectors")
				return
			}

			filter := map[string]any{}
			items, err := r.qdrant.Search(ctx, collectionName, vectors[0], topK, filter)
			if err != nil {
				errChan <- err
				return
			}

			results := make([]RetrievedChunk, 0, len(items))
			for _, item := range items {
				if threshold > 0 && item.Score < float64(threshold) {
					continue
				}
				chunkID := payloadString(item.Payload, "chunk_id", item.ID)
				text := payloadString(item.Payload, "text", "")
				if strings.TrimSpace(text) == "" {
					continue
				}
				results = append(results, RetrievedChunk{
					DocumentChunk: DocumentChunk{
						ID:              chunkID,
						KnowledgeBaseID: payloadString(item.Payload, "knowledge_base_id", collectionName),
						DocumentID:      payloadString(item.Payload, "document_id", ""),
						DocumentName:    payloadString(item.Payload, "document_name", "未知文档"),
						Text:            text,
						Index:           payloadInt(item.Payload, "chunk_index"),
					},
					Score:    item.Score,
					RawScore: item.Score,
				})
			}
			resultChan <- results
		}(query)
	}

	wg.Wait()
	close(resultChan)
	close(errChan)
	for err := range errChan {
		if err != nil {
			return nil, err
		}
	}

	dedup := make(map[string]RetrievedChunk)
	for batch := range resultChan {
		for _, item := range batch {
			key := item.DocumentID + "#" + item.ID
			existing, ok := dedup[key]
			if !ok || item.Score > existing.Score {
				dedup[key] = item
			}
		}
	}

	merged := make([]RetrievedChunk, 0, len(dedup))
	for _, item := range dedup {
		merged = append(merged, item)
	}

	sort.Slice(merged, func(i, j int) bool {
		if merged[i].Score == merged[j].Score {
			if merged[i].DocumentID == merged[j].DocumentID {
				return merged[i].Index < merged[j].Index
			}
			return merged[i].DocumentID < merged[j].DocumentID
		}
		return merged[i].Score > merged[j].Score
	})

	if len(merged) > topK {
		return merged[:topK], nil
	}
	return merged, nil
}

// SearchHybrid 使用 Qdrant 混合检索（dense + sparse）
func (s *RagService) SearchHybrid(ctx context.Context, qdrant *QdrantService, knowledgeBaseID string, denseVector []float64, sparseVector SparseVector, topK int, filter map[string]any) ([]SearchResult, error) {
	return s.SearchHybridThreeWay(ctx, qdrant, knowledgeBaseID, denseVector, sparseVector, sparseVector, topK, filter)
}

func (s *RagService) SearchHybridThreeWay(ctx context.Context, qdrant *QdrantService, knowledgeBaseID string, denseVector []float64, bm25Vector SparseVector, sparseVector SparseVector, topK int, filter map[string]any) ([]SearchResult, error) {
	if qdrant == nil || !qdrant.IsEnabled() || len(denseVector) == 0 {
		return nil, nil
	}

	return qdrant.SearchHybrid(ctx, HybridSearchParams{
		CollectionName: knowledgeBaseID,
		DenseVector:    float64ToFloat32(denseVector),
		BM25Vector:     bm25Vector,
		SparseVector:   sparseVector,
		TopK:           topK,
		Filter:         filter,
	})
}

func (s *RagService) requestEmbeddings(ctx context.Context, cfg model.EmbeddingModelConfig, texts []string) ([][]float64, error) {
	baseURL := strings.TrimRight(strings.TrimSpace(cfg.BaseURL), "/")
	modelName := strings.TrimSpace(cfg.Model)
	if baseURL == "" || modelName == "" {
		return nil, fmt.Errorf("embedding config is incomplete")
	}

	embedCtx := ctx
	if embedCtx == nil {
		embedCtx = context.Background()
	}
	if _, hasDeadline := embedCtx.Deadline(); !hasDeadline {
		var cancel context.CancelFunc
		embedCtx, cancel = context.WithTimeout(embedCtx, 120*time.Second)
		defer cancel()
	}

	provider := strings.TrimSpace(cfg.Provider)
	if provider == "ollama" {
		var embeddings [][]float64
		err := sharedEmbeddingRuntimeScheduler.run(embedCtx, modelRuntimePriorityHigh, func(runCtx context.Context) error {
			var callErr error
			embeddings, callErr = s.requestOllamaEmbeddings(runCtx, cfg, baseURL, modelName, texts)
			return callErr
		})
		if err != nil {
			return nil, err
		}
		return embeddings, nil
	}
	return s.requestOpenAIEmbeddings(embedCtx, cfg, baseURL, modelName, texts)
}

func (s *RagService) requestOllamaEmbeddings(ctx context.Context, cfg model.EmbeddingModelConfig, baseURL, modelName string, texts []string) ([][]float64, error) {
	payload, err := json.Marshal(ollamaEmbedRequest{
		Model: modelName,
		Input: texts,
	})
	if err != nil {
		return nil, fmt.Errorf("marshal embedding request: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, baseURL+"/api/embed", bytes.NewReader(payload))
	if err != nil {
		return nil, fmt.Errorf("create embedding request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")

	resp, err := s.client.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("call embeddings api: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read embeddings response: %w", err)
	}

	var ollamaResp ollamaEmbedResponse
	if err := json.Unmarshal(body, &ollamaResp); err != nil {
		return nil, fmt.Errorf("invalid embeddings response: %w", err)
	}
	if resp.StatusCode >= http.StatusBadRequest {
		if strings.TrimSpace(ollamaResp.Error) != "" {
			return nil, fmt.Errorf("embeddings api error: %s", ollamaResp.Error)
		}
		return nil, fmt.Errorf("embeddings api error: http %d", resp.StatusCode)
	}

	if len(ollamaResp.Embeddings) == 0 {
		return nil, fmt.Errorf("embeddings api returned empty embeddings")
	}
	for _, vec := range ollamaResp.Embeddings {
		if len(vec) == 0 {
			return nil, fmt.Errorf("embeddings api returned empty vector")
		}
	}
	return ollamaResp.Embeddings, nil
}

func (s *RagService) requestOpenAIEmbeddings(ctx context.Context, cfg model.EmbeddingModelConfig, baseURL, modelName string, texts []string) ([][]float64, error) {
	payload, err := json.Marshal(openAIEmbeddingRequest{
		Model: modelName,
		Input: texts,
	})
	if err != nil {
		return nil, fmt.Errorf("marshal embedding request: %w", err)
	}

	httpReq, err := http.NewRequestWithContext(ctx, http.MethodPost, baseURL+"/embeddings", bytes.NewReader(payload))
	if err != nil {
		return nil, fmt.Errorf("create embedding request: %w", err)
	}
	httpReq.Header.Set("Content-Type", "application/json")
	if strings.TrimSpace(cfg.APIKey) != "" {
		httpReq.Header.Set("Authorization", "Bearer "+strings.TrimSpace(cfg.APIKey))
	}

	resp, err := s.client.Do(httpReq)
	if err != nil {
		return nil, fmt.Errorf("call embeddings api: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, fmt.Errorf("read embeddings response: %w", err)
	}

	var embeddingResp openAIEmbeddingResponse
	if err := json.Unmarshal(body, &embeddingResp); err != nil {
		return nil, fmt.Errorf("invalid embeddings response: %w", err)
	}
	if resp.StatusCode >= http.StatusBadRequest {
		if embeddingResp.Error != nil && strings.TrimSpace(embeddingResp.Error.Message) != "" {
			return nil, fmt.Errorf("embeddings api error: %s", embeddingResp.Error.Message)
		}
		return nil, fmt.Errorf("embeddings api error: http %d", resp.StatusCode)
	}

	vectors := make([][]float64, len(embeddingResp.Data))
	for _, item := range embeddingResp.Data {
		if item.Index >= 0 && item.Index < len(vectors) {
			vectors[item.Index] = item.Embedding
		}
	}
	for _, vector := range vectors {
		if len(vector) == 0 {
			return nil, fmt.Errorf("embeddings api returned empty vector")
		}
	}
	return vectors, nil
}

func normalizeChunkText(text string) string {
	text = strings.ReplaceAll(text, "\r\n", "\n")
	text = strings.ReplaceAll(text, "\r", "\n")
	lines := strings.Split(text, "\n")
	cleanedLines := make([]string, 0, len(lines))
	for _, line := range lines {
		line = strings.TrimSpace(line)
		if line == "" {
			continue
		}
		cleanedLines = append(cleanedLines, line)
	}
	return strings.TrimSpace(strings.Join(cleanedLines, "\n"))
}

func float64ToFloat32(input []float64) []float32 {
	if len(input) == 0 {
		return nil
	}
	output := make([]float32, len(input))
	for i, value := range input {
		output[i] = float32(value)
	}
	return output
}

func tokenize(text string) []string {
	fields := strings.FieldsFunc(strings.ToLower(text), func(r rune) bool {
		return !(r == '_' || r == '-' || (r >= '0' && r <= '9') || (r >= 'a' && r <= 'z') || r > 127)
	})
	result := make([]string, 0, len(fields))
	for _, field := range fields {
		if strings.TrimSpace(field) == "" {
			continue
		}
		result = append(result, field)
	}
	if len(result) == 0 && utf8.ValidString(text) {
		result = append(result, text)
	}
	return result
}

// BuildSparseVector 从文本构建 BM25 风格的稀疏向量
// 使用 TF-IDF 近似：对文本分词后，计算每个词的 TF 权重
// 不引入外部依赖，仅用标准库实现
func BuildSparseVector(text string) SparseVector {
	tokens := splitSparseTokens(text)
	if len(tokens) == 0 {
		return SparseVector{}
	}

	counts := make(map[uint32]int)
	for _, token := range tokens {
		h := fnv.New32a()
		_, _ = h.Write([]byte(token))
		idx := h.Sum32()
		counts[idx]++
	}

	indices := make([]uint32, 0, len(counts))
	for idx := range counts {
		indices = append(indices, idx)
	}
	sort.Slice(indices, func(i, j int) bool { return indices[i] < indices[j] })

	values := make([]float32, 0, len(indices))
	total := float32(len(tokens))
	for _, idx := range indices {
		tf := float32(counts[idx]) / total
		values = append(values, tf)
	}

	return SparseVector{Indices: indices, Values: values}
}

func splitSparseTokens(text string) []string {
	text = strings.TrimSpace(text)
	if text == "" {
		return nil
	}

	var tokens []string
	var current []rune
	flush := func() {
		if len(current) == 0 {
			return
		}
		tokens = append(tokens, strings.ToLower(string(current)))
		current = current[:0]
	}

	for _, r := range text {
		if isCJK(r) {
			flush()
			tokens = append(tokens, string(r))
			continue
		}
		if unicode.IsLetter(r) || unicode.IsDigit(r) || r == '_' || r == '-' {
			current = append(current, r)
			continue
		}
		flush()
	}
	flush()

	return tokens
}

func isCJK(r rune) bool {
	return r >= 0x4e00 && r <= 0x9fff
}

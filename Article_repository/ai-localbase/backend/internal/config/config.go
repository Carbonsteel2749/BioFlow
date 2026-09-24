package config

import (
	"os"
	"strconv"
	"strings"

	"ai-localbase/internal/model"
)

func LoadServerConfig() model.ServerConfig {
	return model.ServerConfig{
		Port:                           getEnv("PORT", "8080"),
		UploadDir:                      getEnv("UPLOAD_DIR", "data/uploads"),
		MaxUploadBytes:                 getEnvAsInt64("MAX_UPLOAD_BYTES", 25*1024*1024),
		StateFile:                      getEnv("STATE_FILE", "data/app-state.json"),
		ChatHistoryFile:                getEnv("CHAT_HISTORY_FILE", "data/chat-history.db"),
		QdrantURL:                      getEnv("QDRANT_URL", "http://localhost:6333"),
		QdrantAPIKey:                   getEnv("QDRANT_API_KEY", ""),
		QdrantCollectionPrefix:         getEnv("QDRANT_COLLECTION_PREFIX", "kb_"),
		QdrantVectorSize:               getEnvAsInt("QDRANT_VECTOR_SIZE", 768),
		QdrantDistance:                 getEnv("QDRANT_DISTANCE", "Cosine"),
		QdrantTimeoutSeconds:           getEnvAsInt("QDRANT_TIMEOUT_SECONDS", 5),
		CrossEncoderURL:                getEnv("CROSS_ENCODER_URL", ""),
		CrossEncoderModel:              getEnv("CROSS_ENCODER_MODEL", "BAAI/bge-reranker-v2-m3"),
		CrossEncoderAPIKey:             getEnv("CROSS_ENCODER_API_KEY", ""),
		SparseEncoderURL:               getEnv("SPARSE_ENCODER_URL", ""),
		SparseEncoderModel:             getEnv("SPARSE_ENCODER_MODEL", "BAAI/bge-m3"),
		SparseEncoderAPIKey:            getEnv("SPARSE_ENCODER_API_KEY", ""),
		EnableHybridSearch:             getEnvAsBool("ENABLE_HYBRID_SEARCH", true),
		EnableSparseQueryTranslation:   getEnvAsBool("ENABLE_SPARSE_QUERY_TRANSLATION", false),
		EnableQueryTranslation:         getEnvAsBool("ENABLE_QUERY_TRANSLATION", true),
		QueryTranslationMode:           getEnv("QUERY_TRANSLATION_MODE", "mt"),
		QueryTranslationProvider:       getEnv("QUERY_TRANSLATION_PROVIDER", ""),
		QueryTranslationBaseURL:        getEnv("QUERY_TRANSLATION_BASE_URL", "http://127.0.0.1:8090"),
		QueryTranslationAPIKey:         getEnv("QUERY_TRANSLATION_API_KEY", ""),
		QueryTranslationModel:          getEnv("QUERY_TRANSLATION_MODEL", ""),
		QueryTranslationTimeoutSeconds: getEnvAsInt("QUERY_TRANSLATION_TIMEOUT_SECONDS", 20),
		EnableSemanticReranker:         getEnvAsBool("ENABLE_SEMANTIC_RERANKER", false),
		EnableQueryRewrite:             getEnvAsBool("ENABLE_QUERY_REWRITE", false),
		EnableSemanticCache:            getEnvAsBool("ENABLE_SEMANTIC_CACHE", false),
		EnableContextCompression:       getEnvAsBool("ENABLE_CONTEXT_COMPRESSION", false),
		OllamaBaseURL:                  getEnv("OLLAMA_BASE_URL", "http://localhost:11434"),
		EnableMCP:                      getEnvAsBool("ENABLE_MCP", false),
		EnableMCPLegacyToken:           getEnvAsBool("ENABLE_MCP_LEGACY_TOKEN", false),
		MCPBasePath:                    getEnv("MCP_BASE_PATH", "/mcp"),
		MCPRequestTimeoutSeconds:       getEnvAsInt("MCP_REQUEST_TIMEOUT_SECONDS", 15),
		MCPRequestsPerMinute:           getEnvAsInt("MCP_REQUESTS_PER_MINUTE", 120),
		RetrievalTopKDocument:          getEnvAsInt("RETRIEVAL_TOPK_DOCUMENT", 6),
		RetrievalCandidateTopKDocument: getEnvAsInt("RETRIEVAL_CANDIDATE_TOPK_DOCUMENT", 12),
		RetrievalTopKKnowledgeBase:     getEnvAsInt("RETRIEVAL_TOPK_KNOWLEDGE_BASE", 3),
		RetrievalCandidateTopKAllDocs:  getEnvAsInt("RETRIEVAL_CANDIDATE_TOPK_ALL_DOCS", 32),
		RetrievalMaxChunksPerDocument:  getEnvAsInt("RETRIEVAL_MAX_CHUNKS_PER_DOCUMENT", 2),
		RetrievalMaxContextChars:       getEnvAsInt("RETRIEVAL_MAX_CONTEXT_CHARS", 1500),
		RetrievalEnableAutoExpand:      getEnvAsBool("RETRIEVAL_ENABLE_AUTO_EXPAND", false),
		EvalKnowledgeBaseID:            getEnv("EVAL_KNOWLEDGE_BASE_ID", ""),
		EnableAuth:                     getEnvAsBool("ENABLE_AUTH", false),
		AuthUsername:                   getEnv("AUTH_USERNAME", "root"),
		AuthPassword:                   getEnv("AUTH_PASSWORD", ""),
		AuthSetupToken:                 getEnv("AUTH_SETUP_TOKEN", ""),
		AuthResetToken:                 getEnv("AUTH_RESET_TOKEN", ""),
		AuthResetPassword:              getEnv("AUTH_RESET_PASSWORD", ""),
		JWTSecret:                      getEnv("JWT_SECRET", ""),
	}
}

func getEnv(key, fallback string) string {
	if value := strings.TrimSpace(os.Getenv(key)); value != "" {
		return value
	}

	return fallback
}

func getEnvAsInt(key string, fallback int) int {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}

	parsed, err := strconv.Atoi(value)
	if err != nil {
		return fallback
	}

	return parsed
}

func getEnvAsInt64(key string, fallback int64) int64 {
	value := strings.TrimSpace(os.Getenv(key))
	if value == "" {
		return fallback
	}

	parsed, err := strconv.ParseInt(value, 10, 64)
	if err != nil {
		return fallback
	}

	return parsed
}

func getEnvAsBool(key string, fallback bool) bool {
	value := strings.TrimSpace(strings.ToLower(os.Getenv(key)))
	if value == "" {
		return fallback
	}

	switch value {
	case "true", "1", "yes", "on":
		return true
	case "false", "0", "no", "off":
		return false
	default:
		return fallback
	}
}

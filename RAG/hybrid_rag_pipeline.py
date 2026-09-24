"""Hybrid RAG pipeline: vector similarity + keyword match + vLLM answer."""

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import requests

_ROOT_DIR = Path(__file__).resolve().parents[1]
if str(_ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(_ROOT_DIR))

from syt.database import PrivateDatabaseBuilder


DEFAULT_VLLM_URL = os.getenv("VLLM_URL", "http://localhost:11434/api/generate")  # Ollama 的生成接口
DEFAULT_MODEL = os.getenv("VLLM_MODEL", "qwen3:14b")  # 使用已有的模型

_QUERY_EXPANSION_RULES = [
    (("自闭症",), ["autism", "autism spectrum disorder", "ASD"]),
    (("asd", "autism"), ["autism spectrum disorder", "ASD"]),
    (("肠道菌群", "肠菌", "菌群"), ["gut microbiota", "gut microbiome", "intestinal microbiota", "microbiome"]),
    (("微生物组",), ["microbiome", "microbiota", "gut microbiome"]),
    (("益生菌",), ["probiotic", "probiotics"]),
    (("肠脑轴",), ["gut-brain axis", "gut brain axis"]),
    (("粪菌移植",), ["fecal microbiota transplantation", "faecal microbiota transplantation", "FMT"]),
    (("短链脂肪酸",), ["short-chain fatty acids", "SCFAs"]),
    (("阿克曼菌", "akkermansia"), ["Akkermansia", "Akkermansia muciniphila"]),
    (("乳酸杆菌",), ["Lactobacillus"]),
    (("双歧杆菌",), ["Bifidobacterium"]),
]


def _call_vllm(
    messages: List[Dict[str, str]],
    model: str,
    max_tokens: int,
    temperature: float,
    stream: bool,
    timeout: int,
) -> str:
    """调用与 Ollama 兼容的 vLLM 服务"""
    VLLM_URL = os.environ.get("VLLM_URL", "http://localhost:11434/api/generate")

    system_prompt = ""
    user_prompt = ""
    for msg in messages:
        if msg["role"] == "system":
            system_prompt = msg["content"]
        elif msg["role"] == "user":
            user_prompt = msg["content"]

    # 如果 user_prompt 为空，则将 system_prompt 作为 prompt
    if not user_prompt and system_prompt:
        user_prompt = system_prompt
        system_prompt = ""

    payload = {
        "model": model,
        "prompt": user_prompt,
        "system": system_prompt,
        "temperature": temperature,
        "options": {
            "num_predict": max_tokens
        },
        "stream": stream,
    }
    headers = {"Content-Type": "application/json"}

    print(f"\n>>>>> Ollama Request Payload >>>>>\n{json.dumps(payload, indent=2, ensure_ascii=False)}\n<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<\n")

    try:
        response = requests.post(
            VLLM_URL,
            headers=headers,
            json=payload,
            stream=stream,
            timeout=timeout,
        )
        response.raise_for_status()
        print(f"\n>>>>> Ollama Raw Response (status: {response.status_code}) >>>>>")

        if stream:
            full_content = ""
            for line in response.iter_lines():
                if line:
                    try:
                        data = json.loads(line.decode('utf-8'))
                        content_part = data.get('response', '')
                        if content_part:
                            print(content_part, end="", flush=True)
                            full_content += content_part
                        if data.get('done', False):
                            break
                    except json.JSONDecodeError:
                        continue
            print("\n<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<\n")
            return full_content.strip()
        else:
            data = response.json()
            print(f"{json.dumps(data, indent=2, ensure_ascii=False)}\n<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<\n")
            return data.get("response", "").strip()

    except requests.exceptions.RequestException as e:
        print(f"❌ Ollama/vLLM 调用失败: {e}")
        if e.response is not None:
            print(f"Response Text: {e.response.text}")
        return ""


def _extract_keywords(query: str) -> Set[str]:
    try:
        import jieba
        import jieba.analyse
    except Exception:
        jieba = None

    stopwords = {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "in",
        "on",
        "at",
        "of",
        "for",
        "to",
        "with",
        "by",
        "from",
        "as",
        "about",
        "and",
        "or",
        "not",
    }

    keywords: Set[str] = set()
    if jieba:
        try:
            for word in jieba.cut(query):
                word = word.strip().lower()
                if len(word) > 1 and word not in stopwords and not word.isdigit():
                    keywords.add(word)
        except Exception:
            pass

        try:
            for kw in jieba.analyse.extract_tags(query, topK=5):
                kw = kw.strip().lower()
                if kw:
                    keywords.add(kw)
        except Exception:
            pass

    if not keywords:
        for word in re.findall(r"\b[a-zA-Z]{3,}\b", query.lower()):
            if word not in stopwords:
                keywords.add(word)

    return keywords


def _expand_query_text(query: str) -> str:
    lowered = query.lower()
    expansions: List[str] = []

    for triggers, aliases in _QUERY_EXPANSION_RULES:
        if any(trigger.lower() in lowered for trigger in triggers):
            for alias in aliases:
                alias = alias.strip()
                if alias and alias.lower() not in lowered and alias not in expansions:
                    expansions.append(alias)

    if not expansions:
        return query

    return f"{query} {' '.join(expansions)}".strip()


def _document_quality_score(text: str) -> float:
    cleaned = (text or "").strip()
    if not cleaned:
        return 0.0

    sample = cleaned[:600]
    meaningful = sum(1 for ch in sample if ch.isalnum() or ('\u4e00' <= ch <= '\u9fff'))
    ratio = meaningful / max(len(sample), 1)

    if ratio >= 0.45:
        return 1.0
    if ratio >= 0.3:
        return 0.9
    if ratio >= 0.2:
        return 0.75
    if ratio >= 0.1:
        return 0.55
    return 0.35


def _source_keyword_score(metadata: Dict, keywords: Set[str]) -> float:
    if not keywords:
        return 0.0

    source = ""
    if isinstance(metadata, dict):
        source = str(metadata.get("source") or "")

    if not source:
        return 0.0

    source_text = Path(source).stem.replace("_", " ").lower()
    matched = sum(1 for kw in keywords if kw and kw in source_text)
    if not matched:
        return 0.0

    return min(1.0, matched / len(keywords) + 0.15)


def _clean_retrieval_rows(
    ids: List[str],
    distances: List[float],
    documents: List[Optional[str]],
    metadatas: List[Optional[Dict]],
) -> List[Tuple[str, float, str, Dict]]:
    cleaned: List[Tuple[str, float, str, Dict]] = []
    for item_id, distance, document, metadata in zip(ids, distances, documents, metadatas):
        text = str(document).strip() if document is not None else ""
        if not text:
            continue
        cleaned.append(
            (
                str(item_id),
                float(distance),
                text,
                metadata if isinstance(metadata, dict) else {},
            )
        )
    return cleaned


def _keyword_scores(documents: List[str], keywords: Set[str]) -> List[float]:
    if not keywords:
        return [0.0 for _ in documents]

    scores: List[float] = []
    for doc in documents:
        if not doc:
            scores.append(0.0)
            continue
        doc_lower = doc.lower()
        matched = sum(1 for kw in keywords if kw in doc_lower)
        score = matched / len(keywords)

        early_slice = doc_lower[:200]
        if early_slice:
            early_matches = sum(1 for kw in keywords if kw in early_slice)
            score = min(1.0, score + (early_matches / len(keywords)) * 0.2)

        scores.append(score)
    return scores


def _dedupe_ranked(builder: PrivateDatabaseBuilder, ranked: List[Tuple]) -> List[Tuple]:
    try:
        return builder._dedupe_results(ranked)
    except Exception:
        return ranked


def _retrieve_hybrid(
    builder: PrivateDatabaseBuilder,
    collection,
    query: str,
    top_k: int,
    retrieve_k: int,
    keyword_weight: float,
    metadata_filter: Optional[Dict] = None,
) -> List[Tuple]:
    expanded_query = _expand_query_text(query)
    query_embedding = builder.embedding_model.encode([expanded_query], normalize_embeddings=True).tolist()
    query_args = {
        "query_embeddings": query_embedding,
        "n_results": retrieve_k,
        "include": ["documents", "metadatas", "distances"],
    }
    if metadata_filter:
        query_args["where"] = metadata_filter

    results = collection.query(**query_args)

    ids = results.get("ids", [[]])[0]
    distances = results.get("distances", [[]])[0]
    documents = results.get("documents", [[]])[0]
    metadatas = results.get("metadatas", [[]])[0]

    rows = _clean_retrieval_rows(ids, distances, documents, metadatas)
    if not rows:
        return []

    ids = [row[0] for row in rows]
    distances = [row[1] for row in rows]
    documents = [row[2] for row in rows]
    metadatas = [row[3] for row in rows]

    keywords = _extract_keywords(expanded_query)
    keyword_scores = _keyword_scores(documents, keywords)
    vector_scores = [1 - dist for dist in distances]

    combined_scores: List[float] = []
    for idx in range(len(documents)):
        source_score = _source_keyword_score(metadatas[idx], keywords)
        quality_score = _document_quality_score(documents[idx])
        lexical_score = min(1.0, keyword_scores[idx] * 0.7 + source_score * 0.3)
        combined = ((1 - keyword_weight) * vector_scores[idx] + keyword_weight * lexical_score) * quality_score
        combined_scores.append(combined)

    ranked_indices = sorted(range(len(combined_scores)), key=lambda i: combined_scores[i], reverse=True)
    ranked = [(ids[i], distances[i], documents[i], metadatas[i]) for i in ranked_indices]

    rerank_top_k = top_k
    if builder.reranker:
        rerank_pool = ranked[: max(rerank_top_k * 2, rerank_top_k)]
        pairs = [(expanded_query, doc) for _, _, doc, _ in rerank_pool if doc]
        if pairs:
            try:
                scores = builder.reranker.predict(pairs)
                ranked = [
                    (id_, dist, doc, meta, score)
                    for (id_, dist, doc, meta), score in zip(rerank_pool, scores)
                ]
                ranked = sorted(ranked, key=lambda x: x[4], reverse=True)[:rerank_top_k]
            except Exception:
                ranked = ranked[:rerank_top_k]
        else:
            ranked = ranked[:rerank_top_k]
    else:
        ranked = ranked[:rerank_top_k]

    ranked = _dedupe_ranked(builder, ranked)
    return ranked[:top_k]


def _format_context(ranked: List[Tuple], max_chars: int) -> str:
    parts: List[str] = []
    used = 0

    for item in ranked:
        if len(item) == 5:
            _, _, doc, meta, score = item
            score_text = f"score={score:.4f}"
        else:
            _, dist, doc, meta = item
            score_text = f"distance={dist:.4f}"

        source = None
        if isinstance(meta, dict):
            source = meta.get("source")
        header = f"[source: {source or 'unknown'} | {score_text}]"
        block = f"{header}\n{str(doc).strip()}"

        if used + len(block) > max_chars:
            remaining = max_chars - used
            if remaining > 120:
                parts.append(block[:remaining])
            break

        parts.append(block)
        used += len(block)

    return "\n\n".join(parts).strip()


def _build_messages(query: str, context: str) -> List[Dict[str, str]]:
    system = (
        "角色设定\n"
        "你是一位专业的生物医学研究员，擅长基于本地知识库内容进行专业、清晰的回答。\n\n"
        "任务\n"
        "请基于以下提供的【本地知识库内容】，用中文专业且清晰地回答用户的【问题】。\n\n"
        "回答要求\n"
        "1. 回答内容分为三个自然段落，段落之间空一行。第一段给出核心结论，第二段用2-3句话简要解释，第三段补充研究背景或意义。不要使用任何小标题或标签（如【核心结论】）。\n"
        "2. 语言风格：避免逐句复述原文，请用你自己的话整合信息。\n"
        "3. 信息不足时：如果提供的资料无法回答用户问题，请直接说明“根据当前知识库内容，暂无法直接回答该问题”。\n"
        "4. 禁止内容：回答中不要出现“根据资料”、“参考文献”等元描述语句，也不要列出参考文献编号或来源标记。\n\n"
        "【本地知识库内容】\n"
        "{context}\n\n"
        "【用户问题】\n"
        "{query}\n\n"
        "【你的回答】"
    )

    system_formatted = system.format(context=context, query=query)

    user = "请开始回答。"

    return [
        {"role": "system", "content": system_formatted},
        {"role": "user", "content": user},
    ]

def _dynamic_max_tokens(query: str, base: int, cap: int) -> int:
    length = len(query.strip())
    if length >= 400:
        extra = 800
    elif length >= 200:
        extra = 500
    elif length >= 120:
        extra = 300
    else:
        extra = 0
    return min(base + extra, cap)


def main() -> None:
    parser = argparse.ArgumentParser(description="Hybrid RAG with keyword + vector retrieval")
    base_dir = Path(__file__).resolve().parent

    parser.add_argument("--db-dir", default=str(base_dir / "chroma_db"))
    parser.add_argument("--data-dir", default=str(base_dir / "sci_pdf"))
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--query", default="")
    parser.add_argument("--top-k", type=int, default=3)
    parser.add_argument("--retrieve-k", type=int, default=30)
    parser.add_argument("--max-context-chars", type=int, default=6000)
    parser.add_argument("--max-tokens", type=int, default=1500)
    parser.add_argument("--max-tokens-cap", type=int, default=2400)
    parser.add_argument("--temperature", type=float, default=0.01)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--keyword-weight", type=float, default=0.3)

    args = parser.parse_args()

    builder = PrivateDatabaseBuilder(persist_directory=args.db_dir, force_rebuild=args.rebuild)
    collection = builder.create_collection("course_documents")

    if args.rebuild or collection.count() == 0:
        collection = builder.process_directory(args.data_dir)
        if collection is None or collection.count() == 0:
            raise RuntimeError("No documents in database. Please check data directory.")

    def run_once(query: str) -> None:
        ranked = _retrieve_hybrid(
            builder,
            collection,
            query,
            top_k=args.top_k,
            retrieve_k=args.retrieve_k,
            keyword_weight=args.keyword_weight,
        )
        if not ranked:
            print("No results found.")
            return

        context_ranked = ranked[:3]
        context = _format_context(context_ranked, max_chars=args.max_context_chars)
        messages = _build_messages(query, context)
        max_tokens = _dynamic_max_tokens(query, args.max_tokens, args.max_tokens_cap)
        print("\n" + "=" * 60)
        print("Answer")
        print("=" * 60)
        answer = _call_vllm(
            messages,
            model=args.model,
            max_tokens=max_tokens,
            temperature=args.temperature,
            stream=True,
        )
        if answer:
            print()

    if args.query:
        run_once(args.query)
        return

    while True:
        query = input("\nEnter query (or 'exit'): ").strip()
        if not query or query.lower() == "exit":
            break
        run_once(query)


if __name__ == "__main__":
    main()

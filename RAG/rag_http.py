#!/usr/bin/env python3
# rag_http.py - 完整的 HTTP 服务（带 PubMed API 兜底）

import sys
import os
import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
import requests

# 添加当前目录到路径
sys.path.insert(0, '/home/xh/xhy')

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel
import uvicorn

# 从新模块导入 PubMed 搜索函数
from pubmed_search import pubmed_search_context

# 导入你原有的 RAG 函数
from hybrid_rag_pipeline import (
    PrivateDatabaseBuilder,
    _retrieve_hybrid,
    _format_context,
    _build_messages,
    _call_vllm,
    _dynamic_max_tokens,
    DEFAULT_MODEL
)

# 创建 FastAPI 应用
app = FastAPI(title="自闭症食谱 RAG API")

# 解决跨域问题
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 全局变量
builder = None
collection = None

class RAGRequest(BaseModel):
    query: str
    top_k: Optional[int] = 5
    temperature: Optional[float] = 0.8
    source_scope: Optional[str] = "all"
    accounts: Optional[List[str]] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    enable_pubmed_fallback: Optional[bool] = True

class RAGResponse(BaseModel):
    answer: str
    sources: List[str] = []
    references: List[str] = []
    conflict_note: Optional[str] = None


class WriteRequest(BaseModel):
    topic: str
    extra: Optional[str] = None
    top_k: Optional[int] = 3
    temperature: Optional[float] = 0.2
    outline_count: Optional[int] = 6


class WriteSection(BaseModel):
    heading: str
    draft: str
    sources: List[str] = []
    references: List[str] = []


class WriteResponse(BaseModel):
    article: str
    outline: List[str]
    references: List[str]
    sidebar_refs: List[Dict[str, str]]
    sections: List[WriteSection] = []

# ========== 辅助函数 ==========
def _unique_keep_order(items: List[str]) -> List[str]:
    seen = set()
    result: List[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _source_name(meta: Any) -> str:
    if isinstance(meta, dict) and meta.get("source"):
        return Path(str(meta["source"])).name
    return "unknown"


def _sidebar_ref_entry(source: str, count: int) -> Dict[str, str]:
    stem = Path(source).stem.replace("_", " ") if source else "unknown"
    return {
        "title": stem,
        "authors": "RAG 检索",
        "year": f"命中 {count} 次",
    }


def _strip_code_fences(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\\s*", "", cleaned)
        cleaned = re.sub(r"\\s*```$", "", cleaned)
    return cleaned.strip()


def _normalize_generated_text(text: str) -> str:
    normalized = _strip_code_fences(text or "")
    normalized = normalized.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "    ")
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"\n{3,}", "\n\n", normalized)
    return normalized.strip()


def _strip_inline_reference_noise(text: str) -> str:
    cleaned = _normalize_generated_text(text)
    cleaned = re.sub(r"[（(]\s*来源[:：][^）)]*[）)]", "", cleaned)
    cleaned = re.sub(r"[（(]\s*(?:PubMed|AsdKB)\s*\d*\s*[）)]", "", cleaned)
    cleaned = re.sub(r"\[(?:PubMed|AsdKB)[^\]]*\]", "", cleaned)
    cleaned = re.sub(r"\b(?:PubMed|AsdKB)\s*\d+\b", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _model_to_dict(model: BaseModel) -> Dict[str, Any]:
    if hasattr(model, "model_dump"):
        return model.model_dump()
    return model.dict()


def _parse_outline(raw_text: str, fallback_count: int = 6) -> List[str]:
    cleaned = _strip_code_fences(raw_text)

    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            for key in ("outline", "sections", "items"):
                value = parsed.get(key)
                if isinstance(value, list):
                    parsed = value
                    break
        if isinstance(parsed, list):
            outline: List[str] = []
            for item in parsed:
                if isinstance(item, str):
                    title = item.strip()
                elif isinstance(item, dict):
                    title = str(item.get("heading") or item.get("title") or "").strip()
                else:
                    title = str(item).strip()
                if title:
                    outline.append(re.sub(r"^[\\d\\s.、-]+", "", title).strip())
            outline = [item for item in outline if item]
            if outline:
                return outline
    except Exception:
        pass

    outline: List[str] = []
    for line in cleaned.splitlines():
        title = re.sub(r"^[\d\s.、-]+", "", line).strip()
        title = title.strip("-•*：: ")
        if title:
            outline.append(title)

    if outline:
        return outline

    defaults = [
        "研究背景",
        "核心机制",
        "临床证据",
        "争议与局限",
        "未来方向",
        "结论",
    ]
    return defaults[: max(3, min(fallback_count, len(defaults)))]


def _detect_conflict_note(query: str, references: List[str]) -> Optional[str]:
    if any(token in query for token in ("Akkermansia", "丰度", "菌群变化")):
        refs = ", ".join(references[:2]) if references else "相关文献"
        return f"⚠️ 提醒：当前问题相关文献可能存在方向不一致，请结合检索结果中的不同来源审慎判断（如 {refs}）。"
    return None


_DIET_KEYWORDS = [
    "diet", "food", "foods", "nutrition", "nutritional", "gluten", "casein",
    "probiotic", "prebiotic", "dietary", "meal", "eating", "nutrient", "supplement",
    "饮食", "食物", "食谱", "营养", "益生菌", "益生元", "无麸质", "无酪蛋白",
    "挑食", "偏食", "过敏", "添加剂", "维生素", "肠道", "菌群", "补充剂",
    "禁忌", "避免", "不能吃", "忌口",
]

_WECHAT_ALLOW_KEYWORDS = [
    "食谱",
    "菜单",
    "食单",
    "配餐",
    "三餐",
    "餐单",
]


def _query_is_diet_related(query: str) -> bool:
    query_lower = query.lower()
    return any(keyword in query_lower for keyword in _DIET_KEYWORDS)


def _query_allows_wechat(query: str) -> bool:
    return any(keyword in query for keyword in _WECHAT_ALLOW_KEYWORDS)


def _context_covers_diet(context: str) -> bool:
    context_lower = context.lower()
    hits = sum(1 for keyword in _DIET_KEYWORDS if keyword in context_lower)
    return hits >= 3


def _should_use_pubmed(query: str, local_context: str) -> Tuple[bool, str]:
    if len(local_context) < 500:
        return True, f"本地内容太少 ({len(local_context)} chars)"
    if _query_is_diet_related(query) and not _context_covers_diet(local_context):
        return True, "问题与饮食相关，但本地内容不匹配"
    return False, ""


def _normalize_source_scope(scope: Optional[str]) -> str:
    normalized = (scope or "all").strip().lower()
    if normalized not in ("all", "wechat", "local"):
        return "all"
    return normalized


def _build_metadata_filter(
    source_scope: str,
    accounts: Optional[List[str]],
    date_from: Optional[str],
    date_to: Optional[str],
) -> Optional[Dict[str, Any]]:
    if source_scope != "wechat":
        return None

    clauses: List[Dict[str, Any]] = [{"source_type": "wechat"}]

    if accounts:
        clean_accounts = [item.strip() for item in accounts if item and item.strip()]
        if clean_accounts:
            clauses.append({"account": {"$in": clean_accounts}})

    if date_from:
        clauses.append({"publish_date": {"$gte": date_from}})
    if date_to:
        clauses.append({"publish_date": {"$lte": date_to}})

    if len(clauses) == 1:
        return clauses[0]
    return {"$and": clauses}


def _filter_ranked_by_scope(ranked: List[Tuple], source_scope: str) -> List[Tuple]:
    if source_scope == "all":
        return ranked

    filtered: List[Tuple] = []
    for item in ranked:
        meta = item[3] if len(item) >= 4 else {}
        source_type = meta.get("source_type") if isinstance(meta, dict) else None
        is_wechat = source_type == "wechat"
        if source_scope == "wechat" and not is_wechat:
            continue
        if source_scope == "local" and is_wechat:
            continue
        filtered.append(item)
    return filtered


def _format_pubmed_references(pubmed_articles: List[Dict[str, Any]]) -> List[str]:
    references: List[str] = []
    for article in pubmed_articles:
        references.append(
            f"{article.get('authors', '')} ({article.get('year', '')}). "
            f"{article.get('title', '')}. {article.get('journal', '')}. PMID: {article.get('pmid', '')}"
        )
    return _unique_keep_order(references)


def _estimate_write_article_max_tokens(sections: List[WriteSection]) -> int:
    total_chars = sum(len(section.draft or "") for section in sections)
    estimated_tokens = max(2200, min(5200, total_chars // 2 + 800))
    return estimated_tokens


def _article_looks_truncated(article: str, outline: List[str], fallback_article: str) -> bool:
    stripped = (article or "").strip()
    if not stripped:
        return True

    heading_hits = sum(1 for heading in outline if heading and f"## {heading}" in stripped)
    if outline and heading_hits < len(outline):
        return True

    fallback_length = len((fallback_article or "").strip())
    if fallback_length and len(stripped) < int(fallback_length * 0.8):
        return True

    if stripped.endswith(("。", "！", "？", ".", "!", "?", '"', "”", "』", "」")):
        return False

    return True


def _call_vllm_stream(
    messages: List[Dict[str, str]],
    model: str,
    max_tokens: int,
    temperature: float,
    timeout: int,
):
    """Yield streamed response chunks from the vLLM/Ollama-compatible API."""
    vllm_url = os.environ.get("VLLM_URL", "http://localhost:11434/api/generate")

    system_prompt = ""
    user_prompt = ""
    for msg in messages:
        if msg.get("role") == "system":
            system_prompt = msg.get("content", "")
        elif msg.get("role") == "user":
            user_prompt = msg.get("content", "")

    if not user_prompt and system_prompt:
        user_prompt = system_prompt
        system_prompt = ""

    payload = {
        "model": model,
        "prompt": user_prompt,
        "system": system_prompt,
        "temperature": temperature,
        "options": {
            "num_predict": max_tokens,
        },
        "stream": True,
    }

    response = requests.post(
        vllm_url,
        headers={"Content-Type": "application/json"},
        json=payload,
        stream=True,
        timeout=timeout,
    )
    response.raise_for_status()

    for line in response.iter_lines():
        if not line:
            continue
        try:
            data = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError:
            continue

        content_part = data.get("response", "")
        if content_part:
            yield content_part
        if data.get("done", False):
            break


def _stream_rag_events(request: RAGRequest):
    if collection is None:
        yield json.dumps({
            "type": "error",
            "message": "服务未就绪，请稍后再试",
        }, ensure_ascii=False) + "\n"
        return

    query = request.query.strip()
    top_k = request.top_k or 5
    temperature = request.temperature or 0.8
    source_scope = _normalize_source_scope(request.source_scope)
    if not _query_allows_wechat(query):
        source_scope = "local"
    accounts = request.accounts or []
    date_from = request.date_from
    date_to = request.date_to
    enable_pubmed_fallback = True if request.enable_pubmed_fallback is None else request.enable_pubmed_fallback
    max_tokens = _dynamic_max_tokens(query, base=1200, cap=2600)

    metadata_filter = _build_metadata_filter(source_scope, accounts, date_from, date_to)

    bundle = _retrieve_context_bundle(
        query,
        top_k=top_k,
        retrieve_k=50,
        keyword_weight=0.4,
        max_context_chars=4000,
        metadata_filter=metadata_filter,
        source_scope=source_scope,
        enable_pubmed_fallback=enable_pubmed_fallback,
    )
    final_context = bundle["final_context"]
    local_sources = bundle["local_sources"]
    use_pubmed_result = bundle["use_pubmed_result"]

    if not final_context:
        yield json.dumps({
            "type": "error",
            "message": (
                "抱歉，在本地文献库和 PubMed 中均未检索到相关内容。\n\n"
                "建议尝试更换关键词，或直接访问 https://pubmed.ncbi.nlm.nih.gov/ 查询。"
            ),
        }, ensure_ascii=False) + "\n"
        return

    if use_pubmed_result:
        prompt_template = (
            "角色设定\n"
            "你是一位专业的生物医学研究员，擅长阅读英文文献并用通俗易懂的中文进行总结和解释。\n\n"
            "任务\n"
            "请基于以下提供的【PubMed 文献摘要】，用中文专业且清晰地回答用户的【问题】。\n\n"
            "回答要求\n"
            "1. 回答内容分为三个自然段落，段落之间空一行。第一段用2-3句话简要解释，第二段补充研究背景或意义，第三段总结核心结论。不要使用任何小标题或标签（如【核心结论】）。\n"
            "2. 语言风格：避免逐句翻译原文，请用你自己的话整合信息，突出与用户问题最相关的内容和结论。\n"
            "3. 推理与整合：可以在忠实于原文的基础上进行合理推断，但不要编造原文没有的数据。\n"
            "4. 信息不足时：如果提供的摘要确实无法回答用户问题，请直接说明“根据当前检索到的文献摘要，暂无法直接回答该问题”，并可以建议用户换一种问法或扩大检索范围。\n"
            "5. 禁止内容：回答中不要出现“根据文献摘要”、“参考文献如下”等元描述语句，也不要列出参考文献编号。不要说研究的不足与缺陷。\n\n"
            "【PubMed 文献摘要】\n{context}\n\n"
            "【用户问题】\n{query}\n\n"
            "【你的回答】"
        )
        system_content = prompt_template.format(context=final_context, query=query)
        messages = [{"role": "system", "content": system_content}]
    else:
        messages = _build_messages(query, final_context)

    yield json.dumps({
        "type": "status",
        "stage": "streaming",
        "message": "正在生成回答",
    }, ensure_ascii=False) + "\n"

    answer_parts: List[str] = []
    try:
        for part in _call_vllm_stream(
            messages,
            model=DEFAULT_MODEL,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=900,
        ):
            answer_parts.append(part)
            yield json.dumps({
                "type": "chunk",
                "content": part,
            }, ensure_ascii=False) + "\n"
    except Exception as exc:
        yield json.dumps({
            "type": "error",
            "message": f"生成回答时发生错误：{exc}",
        }, ensure_ascii=False) + "\n"
        return

    answer = "".join(answer_parts).strip()
    references: List[str] = []
    sources: List[str] = []
    if use_pubmed_result:
        sources = _unique_keep_order(local_sources + ["PubMed"])
        references = bundle["pubmed_references"]
        warning = "\n\n---\n⚠️ **提示**：上述意见仅供参考，具体治疗方案建议咨询专业医生。"
        answer = f"{answer}{warning}" if answer else warning
        yield json.dumps({
            "type": "chunk",
            "content": warning,
        }, ensure_ascii=False) + "\n"
    else:
        sources = local_sources
        references = local_sources

    yield json.dumps({
        "type": "completed",
        "response": _model_to_dict(RAGResponse(
            answer=answer,
            sources=sources,
            references=references,
            conflict_note=_detect_conflict_note(query, references),
        )),
    }, ensure_ascii=False) + "\n"


def _retrieve_context_bundle(
    query: str,
    top_k: int,
    retrieve_k: int,
    keyword_weight: float,
    max_context_chars: int,
    pubmed_max_results: int = 3,
    metadata_filter: Optional[Dict[str, Any]] = None,
    source_scope: str = "all",
    enable_pubmed_fallback: bool = True,
) -> Dict[str, Any]:
    ranked = _retrieve_hybrid(
        builder,
        collection,
        query,
        top_k=top_k,
        retrieve_k=retrieve_k,
        keyword_weight=keyword_weight,
        metadata_filter=metadata_filter,
    )

    source_scope = _normalize_source_scope(source_scope)
    if ranked:
        ranked = _filter_ranked_by_scope(ranked, source_scope)

    local_context = ""
    local_sources: List[str] = []
    relevance_score = 0.0

    if ranked:
        for item in ranked[:3]:
            if len(item) == 5:
                score = item[4]
            else:
                score = 1 - item[1]
            relevance_score = max(relevance_score, score)

        local_context = _format_context(ranked[:top_k], max_chars=max_context_chars)
        for item in ranked[:top_k]:
            meta = item[3] if len(item) >= 4 else {}
            if isinstance(meta, dict) and meta.get("source"):
                local_sources.append(Path(str(meta["source"])).name)
        local_sources = _unique_keep_order(local_sources)
        print(f"📚 本地检索到结果 (score={relevance_score:.3f})，来源: {local_sources}")

    need_pubmed, reason = _should_use_pubmed(query, local_context)
    if not enable_pubmed_fallback:
        need_pubmed, reason = False, ""
    pubmed_context = ""
    pubmed_articles: List[Dict[str, Any]] = []

    if need_pubmed:
        print(f"📡 触发 PubMed 检索，原因: {reason}")
        pubmed_context, pubmed_articles = pubmed_search_context(
            query,
            max_results=pubmed_max_results,
            min_year=2015,
        )
        if pubmed_context:
            print(f"✅ PubMed 补充了 {len(pubmed_articles)} 篇文献")
        else:
            print("⚠️ PubMed 未找到相关文献")
    else:
        print("✅ 本地检索充足且内容匹配，跳过 PubMed")

    use_pubmed_result = bool(need_pubmed and pubmed_context)
    if use_pubmed_result:
        final_context = f"{local_context}\n\n{pubmed_context}" if local_context else pubmed_context
    else:
        final_context = local_context

    return {
        "ranked": ranked,
        "local_context": local_context,
        "final_context": final_context,
        "local_sources": local_sources,
        "pubmed_needed": bool(need_pubmed),
        "pubmed_reason": reason,
        "pubmed_attempted": bool(need_pubmed),
        "pubmed_hit_count": len(pubmed_articles),
        "use_pubmed_result": use_pubmed_result,
        "pubmed_articles": pubmed_articles,
        "pubmed_references": _format_pubmed_references(pubmed_articles) if use_pubmed_result else [],
    }


def _generate_outline(topic: str, extra: Optional[str], outline_count: int, temperature: float) -> List[str]:
    if collection is None:
        return []

    prompt = [
        {
            "role": "system",
            "content": (
                "你是一位学术综述规划器。你只负责生成综述大纲，不要检索文献，不要写正文。"
                "请只输出 JSON 数组，数组元素是简短的小标题，不要输出任何解释、编号或代码块。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"主题：{topic}\n"
                f"补充要求：{(extra or '无').strip() or '无'}\n"
                f"请输出 {outline_count} 个左右、按逻辑递进排序的章节小标题。"
                "建议覆盖：研究背景、机制、证据、争议、未来方向和结论。"
            ),
        },
    ]

    raw = _call_vllm(
        prompt,
        model=DEFAULT_MODEL,
        max_tokens=700,
        temperature=temperature,
        stream=False,
        timeout=180,
    )
    outline = _parse_outline(raw, fallback_count=outline_count)
    if outline_count > 0:
        outline = outline[:outline_count]
    return outline


def _build_pubmed_progress_meta(bundle: Dict[str, Any]) -> Dict[str, Any]:
    attempted = bool(bundle.get("pubmed_attempted"))
    used_pubmed = bool(bundle.get("use_pubmed_result"))
    article_count = int(bundle.get("pubmed_hit_count") or 0)

    if attempted and used_pubmed:
        detail = f"已调用 PubMed API，补充 {article_count} 篇文献"
        status = "called"
    elif attempted:
        detail = "已调用 PubMed API，但未补充到额外文献"
        status = "called_empty"
    else:
        detail = "本地资料充足，跳过 PubMed API"
        status = "skipped"

    return {
        "status": status,
        "detail": detail,
        "reason": bundle.get("pubmed_reason") or "",
        "article_count": article_count,
        "attempted": attempted,
        "used_pubmed": used_pubmed,
    }


def _build_section_from_bundle(
    topic: str,
    extra: Optional[str],
    heading: str,
    bundle: Dict[str, Any],
    temperature: float,
) -> Tuple[WriteSection, List[Tuple], List[str]]:
    context = bundle["final_context"]
    sources = list(bundle["local_sources"])
    if bundle["use_pubmed_result"]:
        sources = _unique_keep_order(sources + ["PubMed"])

    messages = [
        {
            "role": "system",
            "content": (
                "你是一位严谨的学术写作者，正在为综述的单个小节撰写正文。"
                "只能依据提供的检索文段写作，不要引入未检索到的新事实。"
                "检索文段可能同时包含本地资料和 PubMed 文献摘要，请统一整合后再写。"
                "请输出中文学术文本，保持段落清晰。"
                "不要输出 [PubMed 1]、(PubMed 1)、PubMed 1、[AsdKB]、（来源：...）或单独的参考文献列表，引用会由前端统一用角标展示。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"主题：{topic}\n"
                f"补充要求：{(extra or '无').strip() or '无'}\n"
                f"小节标题：{heading}\n\n"
                f"检索到的文献文段：\n{context}\n\n"
                "请撰写这一小节正文，长度建议为 2 到 4 段。"
            ),
        },
    ]

    draft = _strip_inline_reference_noise(_call_vllm(
        messages,
        model=DEFAULT_MODEL,
        max_tokens=1200,
        temperature=temperature,
        stream=False,
        timeout=360,
    ).strip())

    if not draft:
        draft = f"根据当前检索到的文献文段，暂未形成关于 {heading} 的有效正文。"

    section_references = _unique_keep_order(bundle["local_sources"] + bundle["pubmed_references"])
    return WriteSection(heading=heading, draft=draft, sources=sources, references=section_references), bundle["ranked"], section_references


def _write_section(
    topic: str,
    extra: Optional[str],
    heading: str,
    top_k: int,
    temperature: float,
) -> Tuple[WriteSection, List[Tuple], List[str], Dict[str, Any]]:
    retrieval_query = f"{topic} {heading} {(extra or '').strip()}".strip()
    bundle = _retrieve_context_bundle(
        retrieval_query,
        top_k=top_k,
        retrieve_k=max(12, top_k * 6),
        keyword_weight=0.35,
        max_context_chars=5000,
        source_scope="local",
    )
    section, ranked, section_references = _build_section_from_bundle(
        topic,
        extra,
        heading,
        bundle,
        temperature,
    )
    return section, ranked, section_references, _build_pubmed_progress_meta(bundle)


def _synthesize_article(topic: str, extra: Optional[str], outline: List[str], sections: List[WriteSection], temperature: float) -> str:
    section_blocks = []
    for section in sections:
        section_blocks.append(f"## {section.heading}\n{section.draft}")

    fallback_article = "\n\n".join(section_blocks).strip()

    messages = [
        {
            "role": "system",
            "content": (
                "你是一位学术综述编辑。请在不新增事实的前提下，整合各小节草稿为完整综述。"
                "保留章节结构，语言要连贯、克制、专业。"
                "不要在正文中直接输出参考文献列表、[PubMed 1]、(PubMed 1)、PubMed 1、[AsdKB] 或 （来源：...）等来源提示，引用会由前端统一用角标展示。"
            ),
        },
        {
            "role": "user",
            "content": (
                f"主题：{topic}\n"
                f"补充要求：{(extra or '无').strip() or '无'}\n"
                f"大纲：{'；'.join(outline)}\n\n"
                "以下是各小节草稿，请整合成一篇完整综述：\n\n"
                + "\n\n".join(section_blocks)
            ),
        },
    ]

    article = _strip_inline_reference_noise(_call_vllm(
        messages,
        model=DEFAULT_MODEL,
        max_tokens=_estimate_write_article_max_tokens(sections),
        temperature=temperature,
        stream=False,
        timeout=480,
    ).strip())

    if not article or _article_looks_truncated(article, outline, fallback_article):
        article = fallback_article
    return article


def _build_write_response_from_sections(
    outline: List[str],
    sections: List[WriteSection],
    article: str,
    reference_entries: List[str],
) -> WriteResponse:
    cleaned_refs: List[str] = []
    for ref in _unique_keep_order(reference_entries):
        cleaned_refs.append(re.sub(r"^\s*\d+\.\s*", "", ref).strip())

    section_source_count: Dict[str, int] = {}
    for section in sections:
        for source in section.sources:
            section_source_count[source] = section_source_count.get(source, 0) + 1

    all_sources = _unique_keep_order([source for section in sections for source in section.sources])
    references = [
        f"{idx + 1}. {reference}"
        for idx, reference in enumerate(cleaned_refs)
    ]
    sidebar_refs = [_sidebar_ref_entry(source, section_source_count.get(source, 1)) for source in all_sources[:8]]

    return WriteResponse(
        article=article,
        outline=outline,
        references=references,
        sidebar_refs=sidebar_refs,
        sections=sections,
    )


def _build_write_response(
    topic: str,
    extra: Optional[str],
    top_k: int,
    temperature: float,
    outline_count: int,
) -> WriteResponse:
    if builder is None or collection is None:
        return WriteResponse(article="服务未就绪，请稍后再试", outline=[], references=[], sidebar_refs=[], sections=[])

    outline = _generate_outline(topic, extra, outline_count=outline_count, temperature=temperature)
    if not outline:
        outline = [
            "研究背景",
            "核心机制",
            "临床证据",
            "争议与局限",
            "未来方向",
            "结论",
        ][: max(3, outline_count)]

    sections: List[WriteSection] = []
    all_reference_entries: List[str] = []

    for heading in outline:
        section, _ranked, section_references, _pubmed_meta = _write_section(
            topic,
            extra,
            heading,
            top_k=top_k,
            temperature=temperature,
        )
        sections.append(section)
        all_reference_entries.extend(section_references)

    article = _synthesize_article(topic, extra, outline, sections, temperature=temperature)
    return _build_write_response_from_sections(outline, sections, article, all_reference_entries)


def _stream_write_events(
    topic: str,
    extra: Optional[str],
    top_k: int,
    temperature: float,
    outline_count: int,
):
    try:
        if builder is None or collection is None:
            yield json.dumps({
                "type": "error",
                "message": "服务未就绪，请稍后再试",
            }, ensure_ascii=False) + "\n"
            return

        yield json.dumps({
            "type": "status",
            "stage": "preparing",
            "progress": 5,
            "message": "正在分析主题并规划综述结构",
        }, ensure_ascii=False) + "\n"

        outline = _generate_outline(topic, extra, outline_count=outline_count, temperature=temperature)
        if not outline:
            outline = ["研究背景", "核心机制", "临床证据", "争议与局限", "未来方向", "结论"][: max(3, outline_count)]

        yield json.dumps({
            "type": "outline",
            "stage": "outline_ready",
            "progress": 15,
            "outline": outline,
            "message": f"已生成大纲，共 {len(outline)} 个小节",
        }, ensure_ascii=False) + "\n"

        sections: List[WriteSection] = []
        all_reference_entries: List[str] = []
        total_sections = max(1, len(outline))

        for index, heading in enumerate(outline, start=1):
            start_progress = 15 + int(((index - 1) / total_sections) * 70)
            end_progress = 15 + int((index / total_sections) * 70)
            yield json.dumps({
                "type": "section_start",
                "stage": "section_writing",
                "progress": start_progress,
                "index": index,
                "total": total_sections,
                "heading": heading,
                "message": f"正在撰写第 {index}/{total_sections} 节：{heading}",
            }, ensure_ascii=False) + "\n"

            yield json.dumps({
                "type": "pubmed_check",
                "stage": "section_writing",
                "progress": start_progress,
                "index": index,
                "total": total_sections,
                "heading": heading,
                "status": "checking",
                "detail": "正在判断是否需要调用 PubMed API",
                "message": f"正在判断第 {index}/{total_sections} 节是否需要调用 PubMed API",
            }, ensure_ascii=False) + "\n"

            retrieval_query = f"{topic} {heading} {(extra or '').strip()}".strip()
            bundle = _retrieve_context_bundle(
                retrieval_query,
                top_k=top_k,
                retrieve_k=max(12, top_k * 6),
                keyword_weight=0.35,
                max_context_chars=5000,
                source_scope="local",
            )
            pubmed_meta = _build_pubmed_progress_meta(bundle)
            decision_progress = min(end_progress - 3, start_progress + 4)
            if decision_progress < start_progress:
                decision_progress = start_progress

            yield json.dumps({
                "type": "pubmed_check",
                "stage": "section_writing",
                "progress": decision_progress,
                "index": index,
                "total": total_sections,
                "heading": heading,
                "status": pubmed_meta["status"],
                "detail": pubmed_meta["detail"],
                "reason": pubmed_meta["reason"],
                "article_count": pubmed_meta["article_count"],
                "message": f"已完成第 {index}/{total_sections} 节的检索判定，正在撰写正文",
            }, ensure_ascii=False) + "\n"

            section, _ranked, section_references = _build_section_from_bundle(
                topic,
                extra,
                heading,
                bundle,
                temperature=temperature,
            )
            sections.append(section)
            all_reference_entries.extend(section_references)

            yield json.dumps({
                "type": "section_done",
                "stage": "section_done",
                "progress": end_progress,
                "index": index,
                "total": total_sections,
                "heading": heading,
                "sources": section.sources,
                "message": f"第 {index}/{total_sections} 节已完成：{heading}",
            }, ensure_ascii=False) + "\n"

        yield json.dumps({
            "type": "status",
            "stage": "synthesizing",
            "progress": 92,
            "message": "正在整合各小节并生成最终文稿",
        }, ensure_ascii=False) + "\n"

        article = _synthesize_article(topic, extra, outline, sections, temperature=temperature)
        response = _build_write_response_from_sections(outline, sections, article, all_reference_entries)

        yield json.dumps({
            "type": "completed",
            "stage": "completed",
            "progress": 100,
            "message": "文献综述生成完成",
            "response": _model_to_dict(response),
        }, ensure_ascii=False) + "\n"
    except Exception as exc:
        yield json.dumps({
            "type": "error",
            "message": f"文献编写过程中发生错误：{exc}",
        }, ensure_ascii=False) + "\n"


# ========== FastAPI 事件和路由 ==========
@app.on_event("startup")
async def startup_event():
    """启动时加载数据库"""
    global builder, collection
    base_dir = Path("/home/xh/xhy")
    db_dir = base_dir / "chroma_db"
    
    print(f"正在加载数据库: {db_dir}")
    builder = PrivateDatabaseBuilder(persist_directory=str(db_dir), force_rebuild=False)
    collection = builder.create_collection("course_documents")
    
    if collection.count() == 0:
        print("⚠️ 警告: 数据库为空")
    else:
        print(f"✅ 数据库加载成功，共 {collection.count()} 条记录")


@app.post("/rag/query", response_model=RAGResponse)
async def rag_query(request: RAGRequest):
    """RAG 问答接口（本地检索 + PubMed 联网兜底）"""
    if collection is None:
        return RAGResponse(answer="服务未就绪，请稍后再试", sources=[], references=[])

    query       = request.query.strip()
    top_k       = request.top_k or 5
    temperature = request.temperature or 0.8
    source_scope = _normalize_source_scope(request.source_scope)
    if not _query_allows_wechat(query):
        source_scope = "local"
    accounts = request.accounts or []
    date_from = request.date_from
    date_to = request.date_to
    enable_pubmed_fallback = True if request.enable_pubmed_fallback is None else request.enable_pubmed_fallback
    max_tokens = _dynamic_max_tokens(query, base=1200, cap=2600)
    answer = ""

    metadata_filter = _build_metadata_filter(source_scope, accounts, date_from, date_to)

    bundle = _retrieve_context_bundle(
        query,
        top_k=top_k,
        retrieve_k=50,
        keyword_weight=0.4,
        max_context_chars=4000,
        metadata_filter=metadata_filter,
        source_scope=source_scope,
        enable_pubmed_fallback=enable_pubmed_fallback,
    )
    final_context = bundle["final_context"]
    local_sources = bundle["local_sources"]
    use_pubmed_result = bundle["use_pubmed_result"]
    pubmed_articles = bundle["pubmed_articles"]
    
    # ── ④ 无任何内容时直接返回提示 ──────────────────────
    if not final_context:
        return RAGResponse(
            answer=(
                "抱歉，在本地文献库和 PubMed 中均未检索到相关内容。\n\n"
                "建议尝试更换关键词，或直接访问 "
                "https://pubmed.ncbi.nlm.nih.gov/ 查询。"
            ),
            sources=[], references=[]
        )

    # ── ⑤ 构建 prompt 并调用大模型 ──────────────────────
    if use_pubmed_result:
        prompt_template = (
            "角色设定\n"
            "你是一位专业的生物医学研究员，擅长阅读英文文献并用通俗易懂的中文进行总结和解释。\n\n"
            "任务\n"
            "请基于以下提供的【PubMed 文献摘要】，用中文专业且清晰地回答用户的【问题】。\n\n"
            "回答要求\n"
            "1. 回答内容分为三个自然段落，段落之间空一行。第一段用2-3句话简要解释，第二段补充研究背景或意义，第三段总结核心结论。不要使用任何小标题或标签（如【核心结论】）。\n"
            "2. 语言风格：避免逐句翻译原文，请用你自己的话整合信息，突出与用户问题最相关的内容和结论。\n"
            "3. 推理与整合：可以在忠实于原文的基础上进行合理推断，但不要编造原文没有的数据。\n"
            "4. 信息不足时：如果提供的摘要确实无法回答用户问题，请直接说明“根据当前检索到的文献摘要，暂无法直接回答该问题”，并可以建议用户换一种问法或扩大检索范围。\n"
            "5. 禁止内容：回答中不要出现“根据文献摘要”、“参考文献如下”等元描述语句，也不要列出参考文献编号。不要说研究的不足与缺陷。\n\n"
            "【PubMed 文献摘要】\n{context}\n\n"
            "【用户问题】\n{query}\n\n"
            "【你的回答】"
        )
        system_content = prompt_template.format(context=final_context, query=query)
        messages = [{"role": "system", "content": system_content}]
    else:
        messages = _build_messages(query, final_context)

    print("\n======== LLM 调用详情 ========")
    print(f"模型: {DEFAULT_MODEL}")
    print(f"Prompt (System): {messages[0]['content'][:500]}...")
    if len(messages) > 1:
        print(f"Prompt (User): {messages[1]['content'][:500]}...")
    print("===============================\n")

    answer = _call_vllm(
        messages,
        model=DEFAULT_MODEL,
        max_tokens=max_tokens,
        temperature=temperature,
        stream=False,
        timeout=900,
    )

    print(f"\n======== LLM 原始响应 ========")
    print(f"{answer[:1000] if answer else '<<EMPTY RESPONSE>>'}")
    print("===============================\n")

    # ── ⑥ 整理并返回最终结果 ───────────────────────────
    references = []
    sources = []
    if use_pubmed_result:
        sources = _unique_keep_order(local_sources + ["PubMed"])
        references = bundle["pubmed_references"]
        answer += (
            "\n\n---\n⚠️ **提示**："
            "上述意见仅供参考，具体治疗方案建议咨询专业医生。"
        )
    else:
        sources = local_sources
        references = local_sources

    return RAGResponse(
        answer=answer,
        sources=sources,
        references=references,
        conflict_note=_detect_conflict_note(query, references),
    )


@app.post("/rag/query/stream")
async def rag_query_stream(request: RAGRequest):
    return StreamingResponse(
        _stream_rag_events(request),
        media_type="application/x-ndjson",
    )


@app.post("/api/qa", response_model=RAGResponse)
async def api_qa(request: RAGRequest):
    """兼容旧接口"""
    return await rag_query(request)


@app.post("/api/write", response_model=WriteResponse)
async def api_write(request: WriteRequest):
    if collection is None or builder is None:
        return WriteResponse(article="服务未就绪，请稍后再试", outline=[], references=[], sidebar_refs=[], sections=[])

    try:
        return _build_write_response(
            topic=request.topic,
            extra=request.extra,
            top_k=request.top_k or 3,
            temperature=request.temperature or 0.2,
            outline_count=request.outline_count or 6,
        )
    except Exception as exc:
        return WriteResponse(
            article=f"文献编写失败：{exc}",
            outline=[],
            references=[],
            sidebar_refs=[],
            sections=[],
        )


@app.post("/api/write/stream")
async def api_write_stream(request: WriteRequest):
    return StreamingResponse(
        _stream_write_events(
            topic=request.topic,
            extra=request.extra,
            top_k=request.top_k or 3,
            temperature=request.temperature or 0.2,
            outline_count=request.outline_count or 6,
        ),
        media_type="application/x-ndjson",
    )


@app.get("/health")
async def health_check():
    """健康检查接口"""
    if collection is None:
        return {"status": "loading", "count": 0}
    return {"status": "ok", "count": collection.count()}


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    """提供前端页面"""
    html_path = Path("/home/xh/xhy/rag_assistant_v1.html")
    if html_path.exists():
        return html_path.read_text(encoding="utf-8")
    return "<h1>未找到前端页面</h1><p>请确保 rag_assistant_v1.html 存在</p>"


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8008,
        log_level="info"
    )

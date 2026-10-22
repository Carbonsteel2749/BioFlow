#!/usr/bin/env python3
"""
RAG 端到端评估：反向生成问答对 → 全链路检索 → LLM 评分 → 跨窗口对比

思路
----
从向量库里随机抽 N 个子块（每块来自不同父文档），让大模型**反向**为每个块生成
qa_per_chunk 个问答对（答案必须能在该块内找到），从而得到一组**自带标准答案**的
测试集。然后对每道题跑真实的检索链路，用同一套上下文按不同「窗口」组装后让大模型
作答，最后用 LLM-as-judge 打分。

指标
----
检索侧
  · Recall@k（父块级）：黄金父块是否出现在前 k 条检索结果中
  · Recall@k（文档级）：黄金文档是否出现在前 k 条中（更宽松）
  · MRR               ：首个命中的倒数排名
  · 引用命中率        ：返回的引用来源里是否包含黄金来源（top1 / 任意）
生成侧（LLM-as-judge）
  · LLM 评分（0-10）  ：答案与参考答案的事实一致性
  · 忠实度 grounded   ：答案陈述能否在**该窗口实际提供的上下文**中找到支撑（防编造）

跨窗口对比
----------
「窗口」= 喂给大模型的上下文预算（取前几块 / 最多多少字符）。
同一次检索结果可复用于所有窗口，因此窗口对比的额外成本只有「按窗口数重跑一次作答」。
若要对比**不同分块大小**，请用不同 chunk size 各建一个知识库，然后分别跑本脚本
（--kb 指定），再对比两次报告。

用法
----
  # 先小跑验证（约 3 分钟）
  python3 scripts/eval_rag.py --samples 2 --windows 小=1/1200,中=2/2400

  # 正式评估（50 块 → 100 题），默认窗口 小/中/大
  python3 scripts/eval_rag.py

  # 只算检索指标，跳过大模型作答与评分（出题仍需大模型，但首次之后走缓存）
  python3 scripts/eval_rag.py --samples 50 --retrieval-only

  # 断点续跑（默认就带缓存，重复运行不会重复烧算力）
  python3 scripts/eval_rag.py --samples 50

环境变量
--------
  API_URL     默认 http://127.0.0.1:8080
  QDRANT_URL  默认 http://127.0.0.1:6333
  OLLAMA_URL  默认 http://127.0.0.1:11434
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import random
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Iterable

# ---------------------------------------------------------------------------
# 基础配置
# ---------------------------------------------------------------------------

API_URL = os.environ.get("API_URL", "http://127.0.0.1:8080").rstrip("/")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://127.0.0.1:6333").rstrip("/")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")

DEFAULT_MODEL = "qwen:7b"
DEFAULT_SAMPLES = 50
DEFAULT_QA_PER_CHUNK = 2
DEFAULT_WINDOWS = "小=1/1200,中=2/2400,大=3/5000"

# 太短的块出不了好题；同时排除结构化表格块
MIN_CHUNK_CHARS = 200
QA_SOURCE_CHARS = 1500  # 出题时喂给模型的块文本上限（过长只会拖慢 prompt 处理）
# 出题输出上限：2 个问答对大约 300~450 token，给足余量即可
QA_NUM_PREDICT = 600

CACHE_ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "outputs", "eval")


# ---------------------------------------------------------------------------
# HTTP 工具
# ---------------------------------------------------------------------------


def http_json(url: str, payload: dict | None = None, timeout: int = 600, retries: int = 2) -> Any:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Content-Type": "application/json"}
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                body = resp.read().decode("utf-8", errors="replace")
            return json.loads(body) if body.strip() else {}
        except Exception as exc:  # noqa: BLE001 - 评估脚本需要容错重试
            last_error = exc
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"请求失败 {url}: {last_error}")


def ollama_chat(model: str, messages: list[dict], *, num_predict: int = 512, num_ctx: int = 8192,
                temperature: float = 0.2, timeout: int = 900) -> str:
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "options": {"temperature": temperature, "num_predict": num_predict, "num_ctx": num_ctx},
    }
    result = http_json(f"{OLLAMA_URL}/api/chat", payload, timeout=timeout, retries=1)
    return (result.get("message") or {}).get("content", "")


def extract_json(text: str) -> Any:
    """从模型输出里抠出 JSON（容忍 ```json 包裹与前后废话）。"""
    if not text:
        raise ValueError("空输出")
    cleaned = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)\s*```", cleaned, re.S)
    if fence:
        cleaned = fence.group(1).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start >= 0 and end > start:
        return json.loads(cleaned[start:end + 1])
    raise ValueError(f"无法解析 JSON: {text[:200]}")


# ---------------------------------------------------------------------------
# 缓存（断点续跑）
# ---------------------------------------------------------------------------


class Cache:
    def __init__(self, path: str, enabled: bool = True):
        self.path = path
        self.enabled = enabled
        self.data: dict[str, Any] = {}
        if enabled and os.path.exists(path):
            with open(path, "r", encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        self.data[entry["k"]] = entry["v"]
                    except Exception:  # noqa: BLE001
                        continue
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._fh = open(path, "a", encoding="utf-8") if enabled else None

    @staticmethod
    def key(*parts: Any) -> str:
        raw = "\u0000".join(str(p) for p in parts)
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Any | None:
        return self.data.get(key)

    def put(self, key: str, value: Any) -> None:
        self.data[key] = value
        if self._fh:
            self._fh.write(json.dumps({"k": key, "v": value}, ensure_ascii=False) + "\n")
            self._fh.flush()

    def close(self) -> None:
        if self._fh:
            self._fh.close()


# ---------------------------------------------------------------------------
# 步骤 1：抽样
# ---------------------------------------------------------------------------


@dataclass
class GoldChunk:
    chunk_id: str
    parent_id: str
    document_id: str
    document_name: str
    child_text: str
    parent_text: str

    @property
    def source_text(self) -> str:
        """出题用的文本：优先父块（上下文完整，答案更可检索）。"""
        return (self.parent_text or self.child_text).strip()


def resolve_kb(kb_id: str | None) -> tuple[str, str]:
    listing = http_json(f"{API_URL}/api/knowledge-bases", timeout=60)
    items = listing.get("items", [])
    if not items:
        raise SystemExit("没有找到任何知识库")
    if kb_id:
        for kb in items:
            if kb["id"] == kb_id:
                return kb["id"], kb.get("name", "")
        raise SystemExit(f"找不到知识库 {kb_id}")
    return items[0]["id"], items[0].get("name", "")


def scroll_all(collection: str, page: int = 500) -> list[dict]:
    points: list[dict] = []
    offset = None
    while True:
        body: dict[str, Any] = {"limit": page, "with_payload": True, "with_vector": False}
        if offset is not None:
            body["offset"] = offset
        result = http_json(f"{QDRANT_URL}/collections/{collection}/points/scroll", body, timeout=120)
        batch = (result.get("result") or {}).get("points", [])
        points.extend(batch)
        offset = (result.get("result") or {}).get("next_page_offset")
        if not batch or offset is None:
            break
    return points


def sample_chunks(kb_id: str, samples: int, seed: int) -> list[GoldChunk]:
    collection = f"kb_{kb_id}"
    points = scroll_all(collection)
    rng = random.Random(seed)

    # 每个父块只取一个代表子块，避免出题高度重复
    by_parent: dict[str, list[dict]] = {}
    for point in points:
        payload = point.get("payload") or {}
        if (payload.get("chunk_kind") or "text") != "text":
            continue
        text = (payload.get("text") or "").strip()
        if len(text) < MIN_CHUNK_CHARS:
            continue
        key = payload.get("parent_id") or payload.get("chunk_id") or ""
        if not key:
            continue
        by_parent.setdefault(key, []).append(payload)

    keys = sorted(by_parent.keys())
    rng.shuffle(keys)
    picked: list[GoldChunk] = []
    for key in keys:
        if len(picked) >= samples:
            break
        payload = rng.choice(by_parent[key])
        picked.append(
            GoldChunk(
                chunk_id=payload.get("chunk_id", ""),
                parent_id=payload.get("parent_id", ""),
                document_id=payload.get("document_id", ""),
                document_name=payload.get("document_name", ""),
                child_text=(payload.get("text") or "").strip(),
                parent_text=(payload.get("parent_text") or "").strip(),
            )
        )

    print(f"  扫描到 {len(points)} 个点 / {len(keys)} 个父块，抽取 {len(picked)} 个（每父块取 1 个子块）")
    return picked


# ---------------------------------------------------------------------------
# 步骤 2：反向生成问答对
# ---------------------------------------------------------------------------

QA_PROMPT_TEMPLATE = """你是生物医学文献评测集的出题人。下面是一段文献正文。

请基于这段正文出 {n} 个问题，要求：
1. 每个问题都能仅凭这段正文回答；
2. 答案是正文里明确写出的事实，不要推理、不要补充外部知识；
3. 问题要具体（带关键实体、机制、指标或人群），不要问“本文讲了什么”；
4. 答案用一句话概括，不超过 60 字。

严格按下面格式输出，每题两行，不要任何解释、不要 Markdown 标记：
{fmt}

正文：
{text}
"""


# 小模型用行式格式（Q:/A: 或 问题：/答案：）比 JSON 可靠得多，
# 因此优先按行解析，只有在模型确实吐了 JSON 时才走 JSON 分支。
QA_QUESTION_RE = re.compile(r"^\s*(?:\d+\s*[.、)]\s*)?(?:Q|Question|问题)\s*[:：]\s*(.*)$", re.I)
QA_ANSWER_RE = re.compile(r"^\s*(?:\d+\s*[.、)]\s*)?(?:A|Answer|答案)\s*[:：]\s*(.*)$", re.I)


def build_qa_prompt(qa_per_chunk: int, text: str) -> str:
    """第 1 题中文、第 2 题英文（用于检验中→英机翻检索链路）。"""
    lines: list[str] = []
    for index in range(1, qa_per_chunk + 1):
        lang = "中文" if index == 1 else "英文"
        lines.append(f"{index}. 问题：<第 {index} 个问题，用{lang}>")
        lines.append("   答案：<该问题的答案>")
    return QA_PROMPT_TEMPLATE.format(n=qa_per_chunk, fmt="\n".join(lines), text=text)


def parse_qa_output(raw: str, qa_per_chunk: int) -> list[dict]:
    """解析出题结果。优先行式格式，失败再退回 JSON。"""
    pairs: list[tuple[str, str]] = []
    question = ""
    answer = ""
    in_answer = False

    def flush() -> None:
        nonlocal question, answer, in_answer
        if question and answer:
            pairs.append((question, answer))
        question = ""
        answer = ""
        in_answer = False

    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        matched_question = QA_QUESTION_RE.match(line)
        if matched_question:
            flush()
            question = matched_question.group(1).strip()
            continue
        matched_answer = QA_ANSWER_RE.match(line)
        if matched_answer:
            answer = matched_answer.group(1).strip()
            in_answer = True
            continue
        # 续行：答案优先（答案可能折行），否则归到问题上
        if in_answer and answer:
            answer = f"{answer} {stripped}".strip()
        elif question:
            question = f"{question} {stripped}".strip()
    flush()

    if not pairs:
        try:
            parsed = extract_json(raw)
            for item in parsed.get("items", []):
                item_question = str(item.get("question", "")).strip()
                item_answer = str(item.get("answer", "")).strip()
                if item_question and item_answer:
                    pairs.append((item_question, item_answer))
        except Exception:  # noqa: BLE001
            return []

    cleaned: list[dict] = []
    for item_question, item_answer in pairs[:qa_per_chunk]:
        lang = "zh" if re.search(r"[\u4e00-\u9fff]", item_question) else "en"
        cleaned.append({"question": item_question, "answer": item_answer, "lang": lang})
    return cleaned


def generate_qa(model: str, cache: Cache, chunk: GoldChunk, qa_per_chunk: int) -> list[dict]:
    source = chunk.source_text[:QA_SOURCE_CHARS]
    key = Cache.key("qa_v2", model, qa_per_chunk, source)
    cached = cache.get(key)
    if cached is not None:
        return cached

    prompt = build_qa_prompt(qa_per_chunk, source)
    for attempt in range(2):
        raw = ollama_chat(
            model,
            [{"role": "user", "content": prompt}],
            num_predict=QA_NUM_PREDICT,
            num_ctx=4096,
            temperature=0.2 if attempt == 0 else 0.5,
        )
        pairs = parse_qa_output(raw, qa_per_chunk)
        if pairs:
            cache.put(key, pairs)
            return pairs
    return []


# ---------------------------------------------------------------------------
# 步骤 3：检索（真实全链路）
# ---------------------------------------------------------------------------


def retrieve(kb_id: str, question: str, timeout: int = 300) -> dict:
    return http_json(
        f"{API_URL}/api/knowledge-bases/{kb_id}/retrieval/debug",
        {"query": question},
        timeout=timeout,
    )


@dataclass
class RetrievedItem:
    rank: int
    chunk_id: str
    parent_id: str
    document_id: str
    document_name: str
    score: float
    text: str


def parse_retrieved(response: dict) -> list[RetrievedItem]:
    items = response.get("items") or []
    sources = response.get("sources") or []
    retrieved: list[RetrievedItem] = []
    for idx, item in enumerate(items):
        source = sources[idx] if idx < len(sources) else {}
        retrieved.append(
            RetrievedItem(
                rank=idx + 1,
                chunk_id=source.get("chunkId") or item.get("id") or "",
                parent_id=source.get("parentId") or "",
                document_id=source.get("documentId") or item.get("documentId") or "",
                document_name=item.get("documentName") or source.get("documentName") or "?",
                score=float(item.get("score") or 0.0),
                text=(item.get("text") or "").strip(),
            )
        )
    return retrieved


# ---------------------------------------------------------------------------
# 步骤 4：按窗口组装上下文并作答
# ---------------------------------------------------------------------------

ANSWER_SYSTEM = (
    "你是一个严谨的科研助手。只能依据给定的检索上下文回答问题："
    "上下文里没有的信息绝对不要编造；确实找不到答案时，明确回答“未检索到相关内容”。"
    "回答语言必须与提问语言一致（中文提问用中文回答）。"
    "引用信息时使用 [文档名#序号] 的格式。"
)


def assemble_context(items: list[RetrievedItem], max_chunks: int, max_chars: int) -> tuple[str, int, int]:
    parts: list[str] = []
    used_chars = 0
    used_chunks = 0
    for item in items[:max_chunks]:
        if not item.text:
            continue
        block = f"[{item.document_name}#{item.rank}] {item.text}"
        if used_chars + len(block) + 2 > max_chars:
            remain = max_chars - used_chars - 2
            if remain > 200 and parts:
                parts.append(block[:remain])
                used_chars += remain + 2
                used_chunks += 1
            break
        parts.append(block)
        used_chars += len(block) + 2
        used_chunks += 1
    return "\n\n".join(parts), used_chunks, used_chars


def answer_question(model: str, cache: Cache, question: str, context: str) -> str:
    if not context.strip():
        return "未检索到相关内容。"
    key = Cache.key("ans_v1", model, question, context)
    cached = cache.get(key)
    if cached is not None:
        return cached
    raw = ollama_chat(
        model,
        [
            {"role": "system", "content": ANSWER_SYSTEM},
            {"role": "user", "content": f"检索上下文：\n{context}\n\n问题：{question}"},
        ],
        num_predict=500,
        num_ctx=8192,
    )
    text = raw.strip()
    cache.put(key, text)
    return text


# ---------------------------------------------------------------------------
# 步骤 5：LLM-as-judge（一次调用评审同一题的所有窗口答案）
# ---------------------------------------------------------------------------

JUDGE_PROMPT = """你是严格的评测专家。下面是一道题、参考答案，以及同一个检索系统在不同
“上下文窗口”配置下生成的若干候选答案。

请对每个候选答案完成两件事：

一、评分（0-10），与参考答案比较：
  10  = 与参考答案事实一致且完整
  7-9 = 主要事实正确，略有缺失
  4-6 = 部分正确或有明显遗漏/含糊
  1-3 = 基本错误或与问题无关
  0   = 明确回答“未检索到相关内容”，或完全答非所问

二、忠实度 grounded：答案中的事实性陈述，是否都能在**该窗口实际提供的上下文**中找到支撑？
  若出现上下文中没有的具体事实（数字、名称、机制），该项为 false。

只输出 JSON，不要解释：
{{"results":[{{"key":"窗口名","score":0,"grounded":true,"reason":"简短理由"}}]}}

问题：{question}

参考答案：{reference}

{blocks}
"""


def judge_question(model: str, cache: Cache, question: str, reference: str,
                   window_answers: list[tuple[str, str, str]]) -> dict[str, dict]:
    """window_answers: [(窗口名, 上下文, 答案)]；returns {窗口名: {score, grounded, reason}}"""
    blocks = []
    for name, context, answer in window_answers:
        blocks.append(
            f"=== 候选答案 [{name}] ===\n"
            f"该窗口提供的上下文（截断展示）：\n{context[:800]}\n\n"
            f"候选答案：\n{answer}\n"
        )
    prompt = JUDGE_PROMPT.format(question=question, reference=reference, blocks="\n".join(blocks))
    key = Cache.key("judge_v1", model, prompt)
    cached = cache.get(key)
    if cached is not None:
        return cached

    for attempt in range(2):
        raw = ollama_chat(
            model,
            [{"role": "user", "content": prompt}],
            num_predict=800,
            num_ctx=8192,
            temperature=0.1 + 0.2 * attempt,
        )
        try:
            parsed = extract_json(raw)
            results: dict[str, dict] = {}
            for item in parsed.get("results", []):
                name = str(item.get("key", "")).strip()
                if not name:
                    continue
                try:
                    score = float(item.get("score", 0))
                except (TypeError, ValueError):
                    score = 0.0
                results[name] = {
                    "score": max(0.0, min(10.0, score)),
                    "grounded": bool(item.get("grounded", False)),
                    "reason": str(item.get("reason", ""))[:200],
                }
            if results:
                cache.put(key, results)
                return results
        except Exception:  # noqa: BLE001
            continue
    return {}


# ---------------------------------------------------------------------------
# 指标
# ---------------------------------------------------------------------------


def first_hit_rank(items: list[RetrievedItem], gold: GoldChunk, level: str) -> int:
    for item in items:
        if level == "parent" and gold.parent_id and item.parent_id == gold.parent_id:
            return item.rank
        if level == "document" and gold.document_id and item.document_id == gold.document_id:
            return item.rank
    return 0


@dataclass
class WindowStats:
    name: str
    max_chunks: int
    max_chars: int
    scores: list[float] = field(default_factory=list)
    grounded: list[bool] = field(default_factory=list)
    cite_hit: list[bool] = field(default_factory=list)
    used_chunks: list[int] = field(default_factory=list)
    used_chars: list[int] = field(default_factory=list)

    def add(self, score: float | None, grounded: bool | None, cite: bool, chunks: int, chars: int) -> None:
        if score is not None:
            self.scores.append(score)
        if grounded is not None:
            self.grounded.append(grounded)
        self.cite_hit.append(cite)
        self.used_chunks.append(chunks)
        self.used_chars.append(chars)

    @property
    def avg_score(self) -> float:
        return sum(self.scores) / len(self.scores) if self.scores else 0.0

    @property
    def grounded_rate(self) -> float:
        return sum(self.grounded) / len(self.grounded) if self.grounded else 0.0

    @property
    def cite_hit_rate(self) -> float:
        return sum(self.cite_hit) / len(self.cite_hit) if self.cite_hit else 0.0

    @property
    def avg_chunks(self) -> float:
        return sum(self.used_chunks) / len(self.used_chunks) if self.used_chunks else 0.0

    @property
    def avg_chars(self) -> float:
        return sum(self.used_chars) / len(self.used_chars) if self.used_chars else 0.0


def pct(value: float) -> str:
    return f"{value * 100:5.1f}%"


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def parse_windows(raw: str) -> list[tuple[str, int, int]]:
    windows: list[tuple[str, int, int]] = []
    for piece in raw.split(","):
        piece = piece.strip()
        if not piece:
            continue
        if "=" not in piece or "/" not in piece:
            raise SystemExit(f"窗口格式错误：{piece}（应为 名称=块数/字符数）")
        name, spec = piece.split("=", 1)
        chunks, chars = spec.split("/", 1)
        windows.append((name.strip(), int(chunks), int(chars)))
    if not windows:
        raise SystemExit("至少需要一个窗口")
    return windows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="RAG 端到端评估（反向生成问答对 + LLM 评分 + 跨窗口对比）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--kb", default=None, help="知识库 ID（默认取第一个）")
    parser.add_argument("--samples", type=int, default=DEFAULT_SAMPLES, help="抽样的文本块数")
    parser.add_argument("--qa-per-chunk", type=int, default=DEFAULT_QA_PER_CHUNK, help="每个块生成的问答对数")
    parser.add_argument("--windows", default=DEFAULT_WINDOWS, help="窗口定义，如 小=1/1200,中=2/2400,大=3/5000")
    parser.add_argument("--model", default=DEFAULT_MODEL, help="评测用大模型")
    parser.add_argument("--seed", type=int, default=20260924, help="抽样随机种子")
    parser.add_argument("--workers", type=int, default=1, help="并发数（Ollama 单机建议 1~2）")
    parser.add_argument("--retrieval-only", action="store_true",
                        help="跳过作答与 LLM 评分，只统计检索层指标（出题仍走缓存）")
    parser.add_argument("--limit", type=int, default=0, help="只评估前 N 道题（快速验证用）")
    parser.add_argument("--no-cache", action="store_true", help="忽略缓存重新计算")
    args = parser.parse_args()

    windows = parse_windows(args.windows)
    started = time.time()

    print("=" * 78)
    print("RAG 端到端评估")
    print("=" * 78)

    kb_id, kb_name = resolve_kb(args.kb)
    print(f"知识库 : {kb_id}  {kb_name}")
    print(f"模型   : {args.model}   抽样 {args.samples} 块 × {args.qa_per_chunk} 题")
    print(f"窗口   : " + "  ".join(f"{n}({c}块/{h}字)" for n, c, h in windows))
    print()

    # ---- 抽样 ----
    print("[1/5] 从向量库抽样 …")
    chunks = sample_chunks(kb_id, args.samples, args.seed)
    if not chunks:
        print("抽样为空，退出。")
        return 1
    print()

    cache_dir = os.path.join(CACHE_ROOT, kb_id)
    cache = Cache(os.path.join(cache_dir, "cache.jsonl"), enabled=not args.no_cache)

    # ---- 出题 ----
    print(f"[2/5] 反向生成问答对（每块 {args.qa_per_chunk} 题）…")
    qa_pairs: list[tuple[GoldChunk, dict]] = []
    gen_started = time.time()
    for index, chunk in enumerate(chunks, 1):
        pairs = generate_qa(args.model, cache, chunk, args.qa_per_chunk)
        for pair in pairs:
            qa_pairs.append((chunk, pair))
        if index % 5 == 0 or index == len(chunks):
            elapsed = time.time() - gen_started
            print(f"      进度 {index}/{len(chunks)}  已生成 {len(qa_pairs)} 题  用时 {elapsed:.0f}s")
    questions = [(c, p) for c, p in qa_pairs if p.get("question")]
    if args.limit:
        questions = questions[: args.limit]
    if not questions:
        print("     没有生成任何题目，退出。")
        return 1
    zh = sum(1 for _, p in questions if p.get("lang") == "zh")
    print(f"      共 {len(questions)} 题（中文 {zh} / 英文 {len(questions) - zh}）")
    print()

    # ---- 检索 + 作答 ----
    print("[3/5] 跑检索链路" + ("（跳过作答）" if args.retrieval_only else " 并按窗口作答") + " …")
    stats = {name: WindowStats(name, c, h) for name, c, h in windows}
    window_hits: dict[str, list[bool]] = {name: [] for name, _, _ in windows}
    parent_ranks: list[int] = []
    document_ranks: list[int] = []
    top1_cite_hits: list[bool] = []
    any_cite_hits: list[bool] = []
    lang_scores: dict[str, list[float]] = {"zh": [], "en": []}
    lang_ranks: dict[str, list[int]] = {"zh": [], "en": []}
    qa_elapsed = 0.0
    ran = 0

    for index, (gold, pair) in enumerate(questions, 1):
        question = pair["question"]
        reference = pair["answer"]
        lang = pair.get("lang", "zh")

        qa_started = time.time()
        try:
            response = retrieve(kb_id, question)
            items = parse_retrieved(response)
        except Exception as exc:  # noqa: BLE001
            print(f"      [{index}] 检索失败：{exc}")
            continue

        parent_rank = first_hit_rank(items, gold, "parent")
        document_rank = first_hit_rank(items, gold, "document")
        parent_ranks.append(parent_rank)
        document_ranks.append(document_rank)
        lang_ranks[lang].append(parent_rank)

        cites = [item.document_id for item in items]
        top1_cite_hits.append(bool(cites) and cites[0] == gold.document_id)
        any_cite_hits.append(gold.document_id in cites)

        window_answers: list[tuple[str, str, str]] = []
        for name, max_chunks, max_chars in windows:
            context, used_chunks, used_chars = assemble_context(items, max_chunks, max_chars)
            hit = parent_rank > 0 and parent_rank <= used_chunks
            window_hits[name].append(hit)
            answer = "" if args.retrieval_only else answer_question(args.model, cache, question, context)
            window_answers.append((name, context, answer))
            stats[name].add(None, None, hit, used_chunks, used_chars)

        if not args.retrieval_only:
            verdicts = judge_question(args.model, cache, question, reference, window_answers)
            for name, _, _ in windows:
                verdict = verdicts.get(name)
                if not verdict:
                    continue
                stats[name].scores.append(verdict["score"])
                stats[name].grounded.append(verdict["grounded"])
                if name == windows[-1][0]:
                    lang_scores[lang].append(verdict["score"])

        qa_elapsed += time.time() - qa_started
        ran += 1
        if index % 5 == 0 or index == len(questions):
            avg = qa_elapsed / max(ran, 1)
            remaining = avg * (len(questions) - index)
            print(f"      [{index}/{len(questions)}] 均耗时 {avg:.1f}s/题  预计剩余 {remaining / 60:.1f} 分钟")
    print()

    # ---- 报告 ----
    print("[4/5] 汇总指标")
    print()
    total = len(parent_ranks)
    if total == 0:
        print("没有成功检索的题目，无法统计。")
        return 1

    print("=" * 78)
    print("评估报告")
    print("=" * 78)
    print(f"知识库        : {kb_id}  ({kb_name})")
    print(f"抽样          : {len(chunks)} 块 → {len(questions)} 道题"
          f"（中文 {zh} / 英文 {len(questions) - zh}），有效检索 {total} 题")
    print(f"平均检索条数  : {sum(stats[windows[-1][0]].used_chunks) / max(total, 1):.1f} 块")
    print("-" * 78)

    print("【一、检索质量】（父块级 = 命中的父文档块；文档级 = 命中的来源文献）")
    max_k = max(3, max(c for _, c, _ in windows))
    for k in range(1, max_k + 1):
        parent_hit = sum(1 for rank in parent_ranks if 0 < rank <= k) / total
        doc_hit = sum(1 for rank in document_ranks if 0 < rank <= k) / total
        print(f"  Recall@{k}   父块 {pct(parent_hit)}    文档 {pct(doc_hit)}")
    mrr = sum((1.0 / rank) for rank in parent_ranks if rank > 0) / total
    print(f"  MRR（父块）  {mrr:.3f}")
    print(f"  引用命中率   首条命中黄金来源 {pct(sum(top1_cite_hits) / total)}"
          f"    引用来源中含黄金来源 {pct(sum(any_cite_hits) / total)}")
    print()

    if not args.retrieval_only:
        print("【二、跨窗口对比】（窗口 = 喂给大模型的上下文预算）")
        print(f"  {'窗口':<12}{'块数':>5}{'字符预算':>10}{'实际块数':>10}{'实际字符':>10}"
              f"{'引用命中':>10}{'LLM评分':>10}{'忠实度':>9}")
        for name, _, _ in windows:
            item = stats[name]
            print(f"  {name:<12}{item.max_chunks:>5}{item.max_chars:>10}"
                  f"{item.avg_chunks:>10.1f}{item.avg_chars:>10.0f}"
                  f"{pct(item.cite_hit_rate):>10}{item.avg_score:>10.2f}"
                  f"{pct(item.grounded_rate):>9}")
        print()

        first, last = windows[0][0], windows[-1][0]
        delta_score = stats[last].avg_score - stats[first].avg_score
        delta_cite = stats[last].cite_hit_rate - stats[first].cite_hit_rate
        delta_ground = stats[last].grounded_rate - stats[first].grounded_rate
        print("【三、窗口趋势】")
        print(f"  {first} → {last}：LLM 评分 {delta_score:+.2f}"
              f"    引用命中 {delta_cite * 100:+.1f}pp    忠实度 {delta_ground * 100:+.1f}pp")
        best = max(windows, key=lambda w: stats[w[0]].avg_score)
        print(f"  评分最高的窗口：{best[0]}（{stats[best[0]].avg_score:.2f} 分）")
        print()

        print("【四、按提问语言】（取最大窗口的评分）")
        for lang, label in (("zh", "中文提问"), ("en", "英文提问")):
            scores = lang_scores.get(lang, [])
            ranks = lang_ranks.get(lang, [])
            hit1 = sum(1 for r in ranks if 0 < r <= 1) / len(ranks) if ranks else 0
            avg = sum(scores) / len(scores) if scores else 0
            print(f"  {label}  {len(ranks):>3} 题    Recall@1 {pct(hit1)}    LLM 评分 {avg:.2f}")
        print()

    print("[5/5] 完成")
    print(f"总耗时 {time.time() - started:.0f}s（其中逐题检索+作答+评分 {qa_elapsed:.0f}s）")
    print(f"缓存目录 {os.path.abspath(cache_dir)}（重复运行会自动复用，不会重复烧算力）")
    cache.close()
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\n已中断（缓存已保留，可重跑续做）")
        sys.exit(130)

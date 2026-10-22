#!/usr/bin/env python3
"""中文 → 英文检索式机翻服务（供 ai-localbase 检索链路调用）。

设计目标：把中文检索问题在 100~400ms 内翻成英文检索式，替代大模型翻译。

特点：
  * 本地 OPUS-MT 小模型（Helsinki-NLP/opus-mt-zh-en，约 77M 参数），纯 CPU 可跑；
  * 生物医学领域词表（scripts/zh_en_glossary.tsv）先替换专业术语再机翻，
    并对机翻结果做后处理纠错，避免「益生菌 → prosthesis」这类错误；
  * 内置 LRU 缓存；
  * 只依赖 torch + transformers + fastapi + uvicorn（无需 GPU）。

启动：
    cd /home/xh/BioFLow/Article_repository
    setsid nohup /home/xh/rag_env/bin/python scripts/mt_service.py \\
        > /tmp/mt_service.log 2>&1 &

自检（不启服务，直接翻译几句）：
    /home/xh/rag_env/bin/python scripts/mt_service.py --test

环境变量：
    MT_PORT            服务端口，默认 8090
    MT_MODEL           模型名，默认 Helsinki-NLP/opus-mt-zh-en
    MT_GLOSSARY        词表路径，默认与本文件同目录的 zh_en_glossary.tsv
    MT_QUANTIZE        是否做 int8 动态量化，默认 0（部分 CPU 缺指令集会崩溃，确认支持再开）
    MT_THREADS         torch 线程数，默认 min(8, CPU 核数)
    MT_CACHE_SIZE      缓存条数，默认 1024
    HF_ENDPOINT        HuggingFace 镜像，默认 https://hf-mirror.com
"""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from collections import OrderedDict
from pathlib import Path

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import torch
from transformers import MarianMTModel, MarianTokenizer

MODEL_NAME = os.environ.get("MT_MODEL", "Helsinki-NLP/opus-mt-zh-en")
GLOSSARY_PATH = Path(os.environ.get("MT_GLOSSARY") or Path(__file__).with_name("zh_en_glossary.tsv"))
PORT = int(os.environ.get("MT_PORT", "8090"))
QUANTIZE = os.environ.get("MT_QUANTIZE", "0") not in {"0", "false", "False", ""}
THREADS = int(os.environ.get("MT_THREADS", "0")) or min(8, os.cpu_count() or 4)
CACHE_SIZE = int(os.environ.get("MT_CACHE_SIZE", "1024"))

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3040-\u30ff\uac00-\ud7af]")


# ── 词表 ────────────────────────────────────────────────────────────────────

def load_glossary(path: Path) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """返回 (pre_rules, post_rules)；pre 按中文长度降序，保证长词优先。"""
    pre: list[tuple[str, str]] = []
    post: list[tuple[str, str]] = []
    if not path.exists():
        print(f"[mt] glossary not found, skip: {path}", flush=True)
        return pre, post

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("\t")]
        if len(parts) < 2 or not parts[0] or not parts[1]:
            continue
        stage = parts[2].lower() if len(parts) > 2 else "pre"
        if stage == "post":
            post.append((parts[0], parts[1]))
        else:
            pre.append((parts[0], parts[1]))

    pre.sort(key=lambda item: len(item[0]), reverse=True)
    print(f"[mt] glossary loaded: pre={len(pre)} post={len(post)}", flush=True)
    return pre, post


PRE_RULES, POST_RULES = load_glossary(GLOSSARY_PATH)

# 追加到英文检索式末尾的词表术语上限，避免查询过长稀释向量检索
TERM_APPEND_LIMIT = int(os.environ.get("MT_TERM_LIMIT", "10"))

_TERM_STOPWORDS = {"of", "with", "in", "and", "the", "for", "on", "to", "a", "an"}


def _normalize_words(text: str) -> list[str]:
    cleaned = re.sub(r"[^a-z0-9\-\s]", " ", text.lower())
    return [word for word in cleaned.split() if word]


def _stem(word: str) -> str:
    """极简词干：去复数后缀后取前 5 个字符，用于判断术语是否已被覆盖。"""
    if len(word) > 4 and word.endswith("ies"):
        word = word[:-3] + "y"
    elif len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        word = word[:-1]
    return word[:5]


def term_already_covered(term: str, base_words: set[str], base_normalized: str) -> bool:
    """判断术语是否已出现在机翻结果里（避免重复堆叠同类术语）。"""
    normalized = " ".join(_normalize_words(term))
    if not normalized:
        return True
    if normalized in base_normalized:
        return True
    content = [word for word in normalized.split() if word not in _TERM_STOPWORDS]
    if not content:
        return True
    return all(_stem(word) in base_words for word in content)


def collect_terms(text: str) -> list[str]:
    """收集原文中出现过的词表英文术语，按出现位置排序并去重。

    注意：不能把英文预先塞回中文再机翻 —— OPUS-MT 只在中英混输时会输出乱码，
    因此词表术语改为在机翻结果之后追加，既保证术语准确，又保留机翻的句法。
    """
    hits: list[tuple[int, str]] = []
    for zh, en in PRE_RULES:
        index = text.find(zh)
        if index >= 0:
            hits.append((index, en))
    hits.sort(key=lambda item: item[0])

    terms: list[str] = []
    seen: set[str] = set()
    for _, en in hits:
        key = en.lower()
        if key in seen:
            continue
        seen.add(key)
        terms.append(en)
    return terms


def apply_post_rules(text: str) -> str:
    for src, dst in POST_RULES:
        if src.lower() in text.lower():
            text = re.sub(re.escape(src), dst, text, flags=re.IGNORECASE)
    return text


def normalize(text: str) -> str:
    """清理机翻产出的英文检索式。"""
    text = text.strip().strip("\"'“”‘’「」『』`")
    text = re.sub(r"\s+([,.!?;:)\]}])", r"\1", text)
    text = re.sub(r"([(\[{])\s+", r"\1", text)
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip().rstrip("?.。！？").strip()


# ── 模型 ────────────────────────────────────────────────────────────────────

torch.set_num_threads(THREADS)
_tokenizer = MarianTokenizer.from_pretrained(MODEL_NAME)
_model = MarianMTModel.from_pretrained(MODEL_NAME)
_model.eval()

QUANTIZED = False
if QUANTIZE:
    try:
        _model = torch.ao.quantization.quantize_dynamic(_model, {torch.nn.Linear}, dtype=torch.qint8)
        QUANTIZED = True
    except Exception as exc:  # pragma: no cover - 量化失败时回退 fp32
        print(f"[mt] dynamic quantization failed, fallback to fp32: {exc}", flush=True)

_infer_lock = threading.Lock()
_cache: "OrderedDict[str, str]" = OrderedDict()


def _cache_get(key: str) -> str | None:
    with _infer_lock:
        value = _cache.get(key)
        if value is not None:
            _cache.move_to_end(key)
        return value


def _cache_put(key: str, value: str) -> None:
    with _infer_lock:
        _cache[key] = value
        _cache.move_to_end(key)
        while len(_cache) > CACHE_SIZE:
            _cache.popitem(last=False)


def run_mt(text: str) -> str:
    """调用机翻模型（输入必须是纯中文）。"""
    batch = _tokenizer(
        [text],
        return_tensors="pt",
        padding=True,
        truncation=True,
        max_length=256,
    )
    max_new_tokens = max(32, min(192, int(batch["input_ids"].shape[1]) * 3))
    with _infer_lock:
        with torch.no_grad():
            generated = _model.generate(
                **batch,
                max_new_tokens=max_new_tokens,
                num_beams=1,
                do_sample=False,
            )
    return _tokenizer.batch_decode(generated, skip_special_tokens=True)[0]


def translate_one(text: str) -> str:
    base = normalize(run_mt(text)) if CJK_RE.search(text) else ""
    if base:
        base = normalize(apply_post_rules(base))

    terms = collect_terms(text)
    if not base:
        # 纯术语查询（例如整句都被词表覆盖）直接用词表拼检索式
        return " ".join(terms) or text.strip()

    base_words = {_stem(word) for word in _normalize_words(base)}
    base_normalized = " ".join(_normalize_words(base))
    extra: list[str] = []
    for term in terms:
        if term_already_covered(term, base_words, base_normalized):
            continue
        base_words.update(_stem(word) for word in _normalize_words(term))
        extra.append(term)
        if len(extra) >= TERM_APPEND_LIMIT:
            break

    if not extra:
        return base
    return f"{base} {' '.join(extra)}".strip()


def translate(text: str) -> tuple[str, bool]:
    """返回 (英文检索式, 是否命中缓存)。"""
    key = text.strip()
    cached = _cache_get(key)
    if cached is not None:
        return cached, True
    value = translate_one(key)
    _cache_put(key, value)
    return value, False


# ── HTTP 服务 ───────────────────────────────────────────────────────────────

try:
    from fastapi import FastAPI
    from pydantic import BaseModel

    app = FastAPI(title="zh-en MT service", docs_url=None, redoc_url=None)

    class TranslateRequest(BaseModel):
        text: str | None = None
        texts: list[str] | None = None
        target: str | None = "en"

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "model": MODEL_NAME,
            "quantized": QUANTIZED,
            "threads": THREADS,
            "preRules": len(PRE_RULES),
            "postRules": len(POST_RULES),
            "cacheSize": len(_cache),
        }

    @app.post("/translate")
    def translate_endpoint(req: TranslateRequest) -> dict:
        started = time.time()
        if req.texts:
            items = [translate(item)[0] for item in req.texts]
            return {
                "translations": items,
                "elapsedMs": round((time.time() - started) * 1000, 1),
            }
        text = (req.text or "").strip()
        if not text:
            return {"translation": "", "elapsedMs": 0}
        result, cache_hit = translate(text)
        return {
            "translation": result,
            "cacheHit": cache_hit,
            "elapsedMs": round((time.time() - started) * 1000, 1),
        }

except ImportError:  # 允许在缺少 fastapi 的环境下只用 --test
    app = None


def _self_test() -> int:
    samples = [
        "双歧杆菌对自闭症儿童肠道菌群的调节作用",
        "肠道菌群如何影响自闭症儿童的社交行为",
        "益生菌干预能否改善自闭症谱系障碍儿童的重复刻板行为",
        "短链脂肪酸在肠脑轴中的作用机制是什么",
        "粪菌移植对孤独症小鼠模型社交行为的影响",
        "How does gut microbiota affect autism",
    ]
    print(f"[mt] model={MODEL_NAME} quantized={QUANTIZED} threads={THREADS}")
    for question in samples:
        started = time.time()
        result, cache_hit = translate(question)
        print(
            "  %6.0f ms | cache=%-5s | %s\n            -> %s"
            % ((time.time() - started) * 1000, cache_hit, question, result)
        )
    return 0


def main() -> int:
    if "--test" in sys.argv:
        return _self_test()
    if app is None:
        print("[mt] fastapi/uvicorn not installed; run with --test only", file=sys.stderr)
        return 2

    import uvicorn

    print(f"[mt] serving on 0.0.0.0:{PORT} model={MODEL_NAME} quantized={QUANTIZED}", flush=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT, log_level="warning")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

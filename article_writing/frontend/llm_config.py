"""论文撰写前端的 LLM 配置：可从前端修改、持久化，并做可用性检查。

背景：此前 provider/model 硬编码为 ``ollama + qwen3:14b``，而本机 Ollama 只装了
``qwen:7b``，导致「LLM 润色 / 对话改写」静默回退模板。本模块把模型配置外置，
并在调用前显式检查模型是否存在，把失败原因返回给前端。
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

from frontend.session_store import PLATE_ROOT

CONFIG_PATH = PLATE_ROOT / "outputs" / "ui-config" / "llm.json"

ALLOWED_PROVIDERS = ("ollama", "openai", "openai_compat", "deepseek", "vllm", "fake")
DEFAULT_PROVIDER = "ollama"
DEFAULT_MODEL = "qwen:7b"
DEFAULT_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT = 300.0
DEFAULT_LANGUAGE = "en+zh"


def _env_default(name: str, fallback: str) -> str:
    return os.getenv(name, "").strip() or fallback


def default_llm_config() -> Dict[str, Any]:
    """默认配置（可用环境变量覆盖，便于部署时切换模型）。"""
    return {
        "enabled": False,
        "provider": _env_default("ARTICLE_WRITING_LLM_PROVIDER", DEFAULT_PROVIDER),
        "model": _env_default("ARTICLE_WRITING_LLM_MODEL", DEFAULT_MODEL),
        "url": _env_default("ARTICLE_WRITING_LLM_URL", DEFAULT_URL),
        "timeout": float(_env_default("ARTICLE_WRITING_LLM_TIMEOUT", str(DEFAULT_TIMEOUT))),
        "language": _env_default("ARTICLE_WRITING_LLM_LANGUAGE", DEFAULT_LANGUAGE),
    }


def _coerce(raw: Any, base: Dict[str, Any]) -> Dict[str, Any]:
    """把任意来源的配置规整成合法配置，非法字段回退到 base。"""
    data = raw if isinstance(raw, dict) else {}
    provider = str(data.get("provider") or base["provider"]).strip().lower()
    if provider not in ALLOWED_PROVIDERS:
        provider = base["provider"]

    model = str(data.get("model") or base["model"]).strip()
    url = str(data.get("url") or base["url"]).strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        url = base["url"]

    try:
        timeout = float(data.get("timeout", base["timeout"]))
    except (TypeError, ValueError):
        timeout = base["timeout"]
    timeout = min(max(timeout, 5.0), 3600.0)

    language = str(data.get("language") or base["language"]).strip() or base["language"]
    enabled = bool(data.get("enabled", base["enabled"]))

    return {
        "enabled": enabled,
        "provider": provider,
        "model": model or base["model"],
        "url": url,
        "timeout": timeout,
        "language": language,
    }


def load_llm_config() -> Dict[str, Any]:
    """读取已保存配置；文件缺失/损坏时返回默认值。"""
    base = default_llm_config()
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return base
    return _coerce(raw, base)


def save_llm_config(patch: Dict[str, Any]) -> Dict[str, Any]:
    """合并并保存配置，返回保存后的完整配置。"""
    merged = _coerce({**load_llm_config(), **(patch or {})}, default_llm_config())
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return merged


def parse_model_list(url: str, timeout: float = 3.0) -> Optional[List[str]]:
    """探测 Ollama ``/api/tags``；返回模型名列表，探测失败返回 None。"""
    endpoint = f"{(url or DEFAULT_URL).rstrip('/')}/api/tags"
    request = urllib.request.Request(endpoint, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None

    models = payload.get("models") if isinstance(payload, dict) else None
    if not isinstance(models, list):
        return None
    names: List[str] = []
    for item in models:
        if isinstance(item, dict):
            name = str(item.get("name") or item.get("model") or "").strip()
            if name:
                names.append(name)
    return names


def model_status(config: Dict[str, Any], timeout: float = 3.0) -> Dict[str, Any]:
    """检查配置里的模型是否真的可用，并给出人类可读的告警。"""
    provider = str(config.get("provider") or "")
    model = str(config.get("model") or "")
    url = str(config.get("url") or "")

    if provider != "ollama":
        # 远端 provider 无法在本地枚举模型，不做可用性断言
        return {"available_models": [], "model_available": None, "warning": ""}

    models = parse_model_list(url, timeout=timeout)
    if models is None:
        return {
            "available_models": [],
            "model_available": False,
            "warning": f"无法连接 Ollama（{url}），请确认服务已启动；LLM 功能将不可用。",
        }

    if model in models:
        return {"available_models": models, "model_available": True, "warning": ""}

    # 支持 "qwen:7b" 与 "qwen:7b-latest" 之类的等价名
    base = model.split(":")[0]
    if any(name == model or name.startswith(f"{base}:") for name in models):
        return {
            "available_models": models,
            "model_available": True,
            "warning": f"未精确匹配到 {model}，但存在同系列模型：{', '.join(models)}",
        }

    return {
        "available_models": models,
        "model_available": False,
        "warning": (
            f"模型 {model} 不在 Ollama 已安装列表中（当前：{', '.join(models) or '无'}）。"
            f"LLM 功能会失败并保留模板原文；请改用已安装模型或执行 ollama pull {model}。"
        ),
    }

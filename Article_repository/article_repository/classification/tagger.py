import json
import os
import re
from typing import Any, Dict, List, Optional

try:
    from openai import OpenAI
except ModuleNotFoundError:  # pragma: no cover - optional dependency in local env
    OpenAI = None

from .prompts import EXAMPLE_INPUT, EXAMPLE_OUTPUT, SYSTEM_PROMPT, USER_PROMPT_TEMPLATE
from .schema import ArticleTags


class QwenTagger:
    def __init__(
        self,
        base_url: str = "http://localhost:8000/v1",
        api_key: str = "EMPTY",
        model: str = "Qwen3-14B",
        temperature: float = 0.1,
        max_tokens: int = 1024,
        timeout: int = 60,
    ):
        self.base_url = os.getenv("VLLM_BASE_URL", base_url)
        self.api_key = os.getenv("VLLM_API_KEY", api_key)
        self.model = os.getenv("VLLM_MODEL", model)
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.client = None
        if OpenAI is not None:
            self.client = OpenAI(
                base_url=self.base_url,
                api_key=self.api_key,
                timeout=self.timeout,
            )
        self._server_ready = self._check_server()

    def _check_server(self) -> bool:
        if self.client is None:
            return False
        try:
            self.client.models.list()
            return True
        except Exception:
            return False

    def tag(self, title: str, abstract: Optional[str] = None) -> ArticleTags:
        if not self._server_ready:
            raise RuntimeError(
                f"Qwen-compatible service not reachable at {self.base_url}; set VLLM_BASE_URL to a running OpenAI-compatible endpoint"
            )
        abstract = abstract or ""
        user_prompt = USER_PROMPT_TEMPLATE.format(title=title, abstract=abstract)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(EXAMPLE_INPUT, ensure_ascii=False)},
            {"role": "assistant", "content": json.dumps(EXAMPLE_OUTPUT, ensure_ascii=False, indent=2)},
            {"role": "user", "content": user_prompt},
        ]
        response = self._call_model(messages)
        tags = self._parse_response(response)
        return tags

    def tag_batch(self, articles: List[Dict[str, str]], max_batch: int = 5) -> List[ArticleTags]:
        results = []
        for art in articles:
            try:
                tags = self.tag(art.get("title", ""), art.get("abstract", ""))
                results.append(tags)
            except Exception as e:
                print(f"打标签失败: {e}")
                results.append(ArticleTags())
        return results

    def _call_model(self, messages: List[Dict[str, str]]) -> str:
        try:
            resp = self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            return resp.choices[0].message.content or ""
        except Exception as e:
            raise RuntimeError(f"模型调用失败: {e}")

    def _parse_response(self, response: str) -> ArticleTags:
        try:
            json_str = self._extract_json(response)
            data = json.loads(json_str)
            return ArticleTags(**data)
        except Exception as e:
            print(f"解析响应失败，返回空标签: {e}")
            return ArticleTags()

    @staticmethod
    def _extract_json(text: str) -> str:
        text = text.strip()
        if text.startswith("{") and text.endswith("}"):
            return text
        match = re.search(r'\{[^{}]*\}', text, re.DOTALL)
        if match:
            return match.group(0)
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            return text[start:end + 1]
        return "{}"


class MockTagger:
    def _heuristic_tags(self, title: str, abstract: Optional[str] = None) -> ArticleTags:
        text = " ".join([title or "", abstract or ""]).lower()
        core_bacteria = []
        if "bifidobacter" in text or "bifidobacterium" in text:
            core_bacteria.append("双歧杆菌")
        if "lactobacillus" in text or "乳酸杆菌" in text:
            core_bacteria.append("乳酸杆菌")
        if "faecalibacterium" in text or "拟杆菌" in text:
            core_bacteria.append("拟杆菌")
        if not core_bacteria:
            core_bacteria.append("肠道菌群")

        experiment_model = []
        if "mouse" in text or "小鼠" in text:
            experiment_model.append("小鼠模型")
        if "rat" in text or "大鼠" in text:
            experiment_model.append("大鼠模型")
        if "human" in text or "临床" in text or "儿童" in text:
            experiment_model.append("临床样本")
        if not experiment_model:
            experiment_model.append("动物模型")

        intervention = []
        if "probiotic" in text or "益生菌" in text:
            intervention.append("益生菌补充")
        if "fecal microbiota transplantation" in text or "粪菌移植" in text:
            intervention.append("粪菌移植")
        if "antibiotic" in text or "抗生素" in text:
            intervention.append("抗生素处理")
        if not intervention:
            intervention.append("饮食干预")

        analysis_method = []
        if "16s" in text or "16s rrna" in text:
            analysis_method.append("16S测序")
        if "metagenomic" in text or "宏基因组" in text:
            analysis_method.append("宏基因组学")
        if "correlation" in text or "相关性" in text:
            analysis_method.append("相关性分析")
        if not analysis_method:
            analysis_method.append("差异丰度分析")

        return ArticleTags(
            core_bacteria="、".join(core_bacteria),
            experiment_model="、".join(experiment_model),
            intervention="、".join(intervention),
            analysis_method="、".join(analysis_method),
        )

    def tag(self, title: str, abstract: Optional[str] = None) -> ArticleTags:
        return self._heuristic_tags(title, abstract)

    def tag_batch(self, articles: List[Dict[str, str]], max_batch: int = 5) -> List[ArticleTags]:
        return [self.tag(a.get("title", ""), a.get("abstract", "")) for a in articles]
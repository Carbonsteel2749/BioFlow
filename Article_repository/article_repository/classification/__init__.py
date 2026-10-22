from .schema import ArticleTags
from .tagger import QwenTagger, MockTagger
from .prompts import SYSTEM_PROMPT, USER_PROMPT_TEMPLATE

__all__ = [
    "ArticleTags",
    "QwenTagger",
    "MockTagger",
    "SYSTEM_PROMPT",
    "USER_PROMPT_TEMPLATE",
]
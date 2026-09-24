"""Phase-4 PaperState consistency modes."""

from __future__ import annotations

from enum import Enum


class ConsistencyMode(str, Enum):
    """How the pipeline reacts to cross-section consistency issues."""

    warn = "warn"  # default: report + auto-harden safe fixes
    strict = "strict"  # raise when residual issues remain after harden

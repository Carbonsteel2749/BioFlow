"""ReAct trajectory models (Phase 3). Stored in draft metadata, not V1 contract fields."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


ReactActionName = Literal["search", "get", "finish"]


class ReactStep(BaseModel):
    """One Thought → Action → Observation cycle."""

    step: int
    thought: str
    action: ReactActionName
    action_input: dict[str, Any] = Field(default_factory=dict)
    observation: str = ""
    paper_ids: list[str] = Field(default_factory=list)


class ReactTrajectory(BaseModel):
    """Full Related-Work ReAct run (PaperQA-style gather, STORM-style multi-query)."""

    goal: str
    seed_query: str = ""
    max_steps: int = 8
    steps: list[ReactStep] = Field(default_factory=list)
    gathered_paper_ids: list[str] = Field(default_factory=list)
    finished: bool = False
    finish_reason: str = ""

    def add_step(self, step: ReactStep) -> None:
        self.steps.append(step)
        for paper_id in step.paper_ids:
            if paper_id and paper_id not in self.gathered_paper_ids:
                self.gathered_paper_ids.append(paper_id)

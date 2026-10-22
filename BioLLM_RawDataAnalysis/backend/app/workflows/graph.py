from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class CanvasPosition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float
    y: float


class WorkflowNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    type: str = Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")
    parameters: dict[str, Any] = Field(default_factory=dict)
    position: CanvasPosition | None = None


class WorkflowEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
    source_node: str = Field(min_length=1, max_length=128)
    source_port: str = Field(min_length=1, max_length=64)
    target_node: str = Field(min_length=1, max_length=128)
    target_port: str = Field(min_length=1, max_length=64)


class WorkflowGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: str = Field(pattern=r"^1\.0$")
    nodes: list[WorkflowNode] = Field(min_length=1, max_length=256)
    edges: list[WorkflowEdge] = Field(default_factory=list, max_length=2048)
    accepted_risks: list[str] = Field(default_factory=list, max_length=256)

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True)
class TaskContract:
    goal: str
    constraints: str
    allowed_tools: list[str]
    success_criteria: list[str]
    expected_output: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "constraints": self.constraints,
            "allowed_tools": self.allowed_tools,
            "success_criteria": self.success_criteria,
            "expected_output": self.expected_output,
        }


@dataclass(frozen=True)
class AgentRun:
    id: str
    goal: str
    status: str
    created_at: str
    updated_at: str
    final_answer: str | None = None
    error: str | None = None
    contract: TaskContract | None = None

    def to_dict(self, eval_result: EvalResult | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "goal": self.goal,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "final_answer": self.final_answer,
            "error": self.error,
        }
        if eval_result is not None:
            data["eval"] = eval_result.to_dict()
        if self.contract is not None:
            data["contract"] = self.contract.to_dict()
        return data


@dataclass(frozen=True)
class RunEvent:
    id: str
    run_id: str
    sequence: int
    event_type: str
    payload: dict[str, Any]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "run_id": self.run_id,
            "sequence": self.sequence,
            "event_type": self.event_type,
            "payload": self.payload,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    text: str
    source: str
    tags: list[str]
    importance: float
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "text": self.text,
            "source": self.source,
            "tags": self.tags,
            "importance": self.importance,
            "created_at": self.created_at,
        }


@dataclass(frozen=True)
class SkillRecord:
    id: str
    name: str
    description: str
    instructions: str
    trigger_terms: list[str]
    tool_names: list[str]
    enabled: bool
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "instructions": self.instructions,
            "trigger_terms": self.trigger_terms,
            "tool_names": self.tool_names,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_schema: dict[str, Any]
    permission_category: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
            "permission_category": self.permission_category,
        }


@dataclass(frozen=True)
class ToolCall:
    id: str
    run_id: str
    tool_name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None
    status: str
    duration_ms: int
    error: str | None
    created_at: str


@dataclass(frozen=True)
class EvalResult:
    run_id: str
    score: float
    notes: str
    passed: bool
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "score": self.score,
            "notes": self.notes,
            "passed": self.passed,
            "created_at": self.created_at,
        }

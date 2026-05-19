from __future__ import annotations

import re

from mnemosyne_core.db import Database
from mnemosyne_core.models import SkillRecord


class SkillStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def create(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        trigger_terms: list[str] | None = None,
        tool_names: list[str] | None = None,
        enabled: bool = True,
    ) -> SkillRecord:
        return self.db.add_skill(
            name=_normalize_skill_name(name),
            description=description.strip(),
            instructions=instructions.strip(),
            trigger_terms=_normalize_terms(trigger_terms or []),
            tool_names=_normalize_terms(tool_names or []),
            enabled=enabled,
        )

    def update(
        self,
        skill_id: str,
        *,
        name: str,
        description: str,
        instructions: str,
        trigger_terms: list[str],
        tool_names: list[str],
        enabled: bool,
    ) -> SkillRecord:
        return self.db.update_skill(
            skill_id,
            name=_normalize_skill_name(name),
            description=description.strip(),
            instructions=instructions.strip(),
            trigger_terms=_normalize_terms(trigger_terms),
            tool_names=_normalize_terms(tool_names),
            enabled=enabled,
        )

    def list(self, *, enabled_only: bool = False) -> list[SkillRecord]:
        return self.db.list_skills(enabled_only=enabled_only)

    def search(self, query: str, *, limit: int = 5) -> list[SkillRecord]:
        return self.db.search_skills(query, limit=limit, enabled_only=True)


def _normalize_skill_name(name: str) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_-]+", "_", name.strip().lower()).strip("_")
    if not normalized:
        raise ValueError("Skill name must contain at least one letter or number")
    if len(normalized) > 80:
        raise ValueError("Skill name must be 80 characters or fewer")
    return normalized


def _normalize_terms(terms: list[str]) -> list[str]:
    normalized: list[str] = []
    for term in terms:
        if not isinstance(term, str):
            continue
        value = term.strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized

from __future__ import annotations

from mnemosyne_core.db import Database
from mnemosyne_core.models import MemoryRecord


class MemoryStore:
    def __init__(self, db: Database) -> None:
        self.db = db

    def add(
        self,
        text: str,
        *,
        source: str,
        tags: list[str] | None = None,
        importance: float = 0.5,
    ) -> MemoryRecord:
        return self.db.add_memory(text, source=source, tags=tags, importance=importance)

    def search(self, query: str, limit: int = 5) -> list[MemoryRecord]:
        return self.db.search_memories(query, limit=limit)

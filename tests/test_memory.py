from pathlib import Path

from mnemosyne_core.db import Database
from mnemosyne_core.memory import MemoryStore


def test_sqlite_fts_memory_search_returns_relevant_records(tmp_path: Path) -> None:
    db = Database(tmp_path / "mnemosyne.db")
    db.initialize()
    memory = MemoryStore(db)
    memory.add("Solar battery research prefers LFP chemistry for safety.", source="seed")
    memory.add("Coffee tasting notes mention citrus and florals.", source="seed")

    results = memory.search("battery safety", limit=3)

    assert [record.text for record in results] == [
        "Solar battery research prefers LFP chemistry for safety."
    ]


def test_vector_memory_search_falls_back_beyond_exact_fts_terms(tmp_path: Path) -> None:
    db = Database(tmp_path / "mnemosyne.db")
    db.initialize()
    memory = MemoryStore(db)
    memory.add("Solar battery research prefers LFP chemistry for safety.", source="seed")
    memory.add("Coffee tasting notes mention citrus and florals.", source="seed")

    results = memory.search("lithium iron phosphate storage risk", limit=3)

    assert results[0].text == "Solar battery research prefers LFP chemistry for safety."

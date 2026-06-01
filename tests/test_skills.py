from pathlib import Path

from mnemosyne_core.config import Settings
from mnemosyne_core.db import Database
from mnemosyne_core.skills import SkillStore
from mnemosyne_core.tools import ToolRegistry


def test_skill_store_creates_normalizes_and_searches_skills(tmp_path: Path) -> None:
    db = Database(tmp_path / "mnemosyne.db")
    db.initialize()
    skills = SkillStore(db)

    created = skills.create(
        name="OpenClaw Model Run",
        description="Run OpenClaw model inference.",
        instructions="Use openclaw infer model run --prompt.",
        trigger_terms=["openclaw", "model run", "openclaw"],
        tool_names=["run_terminal_command"],
    )
    results = skills.search("openclaw model")

    assert created.name == "openclaw_model_run"
    assert created.trigger_terms == ["openclaw", "model run"]
    assert results[0].id == created.id


def test_vector_skill_search_finds_related_terms_without_exact_fts_match(tmp_path: Path) -> None:
    db = Database(tmp_path / "mnemosyne.db")
    db.initialize()
    skills = SkillStore(db)

    created = skills.create(
        name="OpenClaw Model Run",
        description="Run OpenClaw model inference.",
        instructions="Use openclaw infer model run --prompt.",
        trigger_terms=["openclaw"],
        tool_names=["run_terminal_command"],
    )
    results = skills.search("assistant workflow inference")

    assert results[0].id == created.id


def test_skill_search_treats_fts_operator_words_as_text(tmp_path: Path) -> None:
    db = Database(tmp_path / "mnemosyne.db")
    db.initialize()
    skills = SkillStore(db)

    created = skills.create(
        name="Indeed Job Detail Collector",
        description="Collect job URLs and details from individual listing pages.",
        instructions="Open each listing and capture its dedicated detail URL.",
        trigger_terms=["indeed", "devops"],
        tool_names=["web_search"],
    )
    results = skills.search("Indeed DevOps AND details")

    assert results[0].id == created.id


def test_tool_registry_can_create_and_list_skills(tmp_path: Path) -> None:
    db = Database(tmp_path / "mnemosyne.db")
    db.initialize()
    skills = SkillStore(db)
    registry = ToolRegistry.safe_defaults(
        Settings(database_path=str(tmp_path / "mnemosyne.db"), allowed_file_roots=[str(tmp_path)]),
        skills,
    )

    created = registry.execute(
        "create_skill",
        {
            "name": "Report Writer",
            "description": "Write compact Markdown reports.",
            "instructions": "Use headings, source links, and a short recommendation.",
            "trigger_terms": ["report", "markdown"],
            "tool_names": ["web_search"],
        },
    )
    listed = registry.execute("list_skills", {"query": "markdown"})

    assert created["skill"]["name"] == "report_writer"
    assert listed["skills"][0]["id"] == created["skill"]["id"]

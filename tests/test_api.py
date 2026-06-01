from pathlib import Path
from time import sleep

from fastapi.testclient import TestClient

from mnemosyne_core.agent import AgentRuntime
from mnemosyne_core.api import create_app
from mnemosyne_core.config import Settings
from mnemosyne_core.db import Database
from mnemosyne_core.jobs import TerminalJobManager
from mnemosyne_core.memory import MemoryStore
from mnemosyne_core.model_client import ModelRequest, ModelResponse, ToolCallRequest
from mnemosyne_core.skills import SkillStore
from mnemosyne_core.tools import ToolRegistry


class StubModelClient:
    configured = True
    requests: list[ModelRequest]

    def __init__(self) -> None:
        self.requests = []

    async def complete(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if request.tool_results:
            return ModelResponse(message=f"Answer for {request.goal}", tool_calls=[])
        return ModelResponse(
            message="Need a small calculation first.",
            tool_calls=[ToolCallRequest(name="calculator", arguments={"expression": "1 + 1"})],
        )


def build_client(tmp_path: Path) -> TestClient:
    db = Database(tmp_path / "mnemosyne.db")
    db.initialize()
    memory = MemoryStore(db)
    skills = SkillStore(db)
    memory.add("Research runs should show memory hits.", source="seed")
    registry = ToolRegistry.safe_defaults(
        Settings(
            database_path=str(tmp_path / "mnemosyne.db"),
            allowed_file_roots=[str(tmp_path)],
            terminal_enabled=True,
        ),
        skills,
    )
    jobs = TerminalJobManager(
        Settings(
            database_path=str(tmp_path / "mnemosyne.db"),
            allowed_file_roots=[str(tmp_path)],
            terminal_enabled=True,
        ),
        db,
    )
    runtime = AgentRuntime(db, memory, registry, StubModelClient(), skills, jobs)
    return TestClient(create_app(runtime, run_inline=True))


def test_post_run_creates_completed_run_and_history_survives_reopen(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    created = client.post(
        "/runs",
        json={
            "goal": "research memory",
            "constraints": "Use only local memory and safe tools.",
            "allowed_tools": ["calculator"],
            "success_criteria": ["Answer the goal", "Record an eval"],
        },
    ).json()
    run_id = created["id"]

    run = client.get(f"/runs/{run_id}").json()
    assert run["status"] == "completed"
    assert run["thread_id"]
    assert run["final_answer"] == "Answer for research memory"
    assert run["eval"]["evaluator_version"] == "local-rubric-v2"
    assert any(dimension["name"] == "success_criteria" for dimension in run["eval"]["rubric"])
    assert run["contract"]["constraints"] == "Use only local memory and safe tools."
    assert run["contract"]["allowed_tools"] == ["calculator"]
    assert client.get("/runs").json()[0]["id"] == run_id

    reopened = build_client(tmp_path)
    assert reopened.get("/runs").json()[0]["id"] == run_id


def test_threads_store_messages_and_continue_context(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    thread = client.post("/threads", json={"title": "OpenClaw setup"}).json()
    first = client.post(
        "/runs",
        json={"goal": "remember this project uses OpenClaw", "thread_id": thread["id"]},
    ).json()
    second = client.post(
        "/runs",
        json={"goal": "what did I say this project uses?", "thread_id": thread["id"]},
    ).json()
    detail = client.get(f"/threads/{thread['id']}").json()
    model_client = client.app.state.runtime.model_client

    assert first["thread_id"] == thread["id"]
    assert second["thread_id"] == thread["id"]
    assert [message["role"] for message in detail["messages"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert detail["messages"][0]["content"] == "remember this project uses OpenClaw"
    assert detail["messages"][2]["content"] == "what did I say this project uses?"
    assert detail["runs"][0]["id"] == second["id"]
    assert any(
        message.content == "remember this project uses OpenClaw"
        for message in model_client.requests[-1].conversation_messages
    )


def test_events_endpoint_returns_sse_ordered_events(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    run_id = client.post("/runs", json={"goal": "research events"}).json()["id"]

    response = client.get(f"/runs/{run_id}/events")

    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: run.created" in response.text
    assert "event: run.completed" in response.text


def test_events_json_endpoint_returns_ordered_events(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    run_id = client.post("/runs", json={"goal": "research events"}).json()["id"]

    events = client.get(f"/runs/{run_id}/events.json").json()

    assert [event["event_type"] for event in events][:3] == [
        "run.created",
        "plan.created",
        "memory.retrieved",
    ]
    assert events[3]["event_type"] == "skills.retrieved"
    assert events[-1]["event_type"] == "run.completed"
    assert [event["sequence"] for event in events] == sorted(event["sequence"] for event in events)
    assert all("payload" in event for event in events)


def test_tool_catalog_exposes_safe_tool_permissions(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    tools = client.get("/tools").json()

    assert {tool["name"] for tool in tools} >= {"calculator", "web_search", "write_text_file"}
    web_search = next(tool for tool in tools if tool["name"] == "web_search")
    write_file = next(tool for tool in tools if tool["name"] == "write_text_file")
    assert web_search["permission_category"] == "network.public_search"
    assert write_file["permission_category"] == "filesystem.write"
    assert {tool["name"] for tool in tools} >= {"create_skill", "list_skills"}


def test_direct_tool_execution_requires_confirmation_for_risky_tools(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    calculation = client.post(
        "/tools/calculator/execute",
        json={"arguments": {"expression": "2 + 3"}, "confirm_risk": False},
    ).json()
    blocked = client.post(
        "/tools/write_text_file/execute",
        json={
            "arguments": {"path": str(tmp_path / "note.md"), "text": "hello"},
            "confirm_risk": False,
        },
    )
    written = client.post(
        "/tools/write_text_file/execute",
        json={
            "arguments": {"path": str(tmp_path / "note.md"), "text": "hello"},
            "confirm_risk": True,
        },
    ).json()

    assert calculation["status"] == "completed"
    assert calculation["result"]["result"] == 5
    assert blocked.status_code == 409
    assert written["status"] == "completed"
    assert (tmp_path / "note.md").read_text() == "hello"


def test_terminal_jobs_persist_logs_and_status(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    blocked = client.post(
        "/terminal/jobs",
        json={
            "shell": "powershell",
            "command": "Write-Output hello",
            "working_directory": str(tmp_path),
            "confirm_risk": False,
        },
    )
    created = client.post(
        "/terminal/jobs",
        json={
            "shell": "powershell",
            "command": "Write-Output hello",
            "working_directory": str(tmp_path),
            "confirm_risk": True,
        },
    ).json()
    job_id = created["id"]

    assert blocked.status_code == 409
    for _ in range(30):
        current = client.get(f"/terminal/jobs/{job_id}").json()
        if current["status"] in {"completed", "failed", "cancelled"}:
            break
        sleep(0.1)
    logs = client.get(f"/terminal/jobs/{job_id}/logs").json()
    listed = client.get("/terminal/jobs").json()

    assert current["status"] == "completed"
    assert current["exit_code"] == 0
    assert any(log["stream"] == "stdout" and "hello" in log["text"] for log in logs)
    assert listed[0]["id"] == job_id


def test_memory_crud_endpoints_manage_searchable_memory(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    created = client.post(
        "/memory",
        json={
            "text": "LFP battery packs have strong thermal stability.",
            "source": "manual",
            "tags": ["battery", "safety"],
            "importance": 0.9,
        },
    ).json()
    updated = client.put(
        f"/memory/{created['id']}",
        json={
            "text": "LFP battery packs have excellent thermal stability.",
            "source": "curated",
            "tags": ["battery"],
            "importance": 0.8,
        },
    ).json()
    records = client.get("/memory", params={"query": "thermal stability"}).json()

    assert updated["source"] == "curated"
    assert records[0]["id"] == created["id"]
    assert records[0]["text"] == "LFP battery packs have excellent thermal stability."

    deleted = client.delete(f"/memory/{created['id']}")

    assert deleted.status_code == 204
    assert all(record["id"] != created["id"] for record in client.get("/memory").json())


def test_skill_crud_endpoints_manage_searchable_skills(tmp_path: Path) -> None:
    client = build_client(tmp_path)

    created = client.post(
        "/skills",
        json={
            "name": "OpenClaw Inference",
            "description": "Run OpenClaw model inference from WSL.",
            "instructions": "Use openclaw infer model run for one-shot model replies.",
            "trigger_terms": ["openclaw", "infer"],
            "tool_names": ["run_terminal_command"],
            "enabled": True,
        },
    ).json()
    updated = client.put(
        f"/skills/{created['id']}",
        json={
            "name": "OpenClaw Inference",
            "description": "Run OpenClaw model inference from WSL.",
            "instructions": "Use openclaw infer model run --prompt for model replies.",
            "trigger_terms": ["openclaw", "model run"],
            "tool_names": ["run_terminal_command"],
            "enabled": True,
        },
    ).json()
    records = client.get("/skills", params={"query": "model run"}).json()

    assert created["name"] == "openclaw_inference"
    assert updated["instructions"].endswith("--prompt for model replies.")
    assert records[0]["id"] == created["id"]

    deleted = client.delete(f"/skills/{created['id']}")

    assert deleted.status_code == 204
    assert all(skill["id"] != created["id"] for skill in client.get("/skills").json())


def test_knowledge_export_and_replace_import_round_trip(tmp_path: Path) -> None:
    source = build_client(tmp_path / "source")
    memory = source.post(
        "/memory",
        json={
            "text": "Importable memory about LFP safety.",
            "source": "curated",
            "tags": ["battery"],
            "importance": 0.85,
        },
    ).json()
    skill = source.post(
        "/skills",
        json={
            "name": "OpenClaw Backup",
            "description": "Run OpenClaw after restoring skills.",
            "instructions": "Use openclaw infer model run --prompt.",
            "trigger_terms": ["openclaw"],
            "tool_names": ["run_terminal_command"],
            "enabled": True,
        },
    ).json()

    exported = source.get("/knowledge/export").json()
    target = build_client(tmp_path / "target")
    blocked = target.post(
        "/knowledge/import",
        json={**exported, "mode": "replace", "confirm_replace": False},
    )
    imported = target.post(
        "/knowledge/import",
        json={**exported, "mode": "replace", "confirm_replace": True},
    ).json()
    memories = target.get("/memory", params={"query": "importable LFP safety"}).json()
    skills = target.get("/skills", params={"query": "restore openclaw"}).json()

    assert exported["kind"] == "mnemosyne_core_knowledge_export"
    assert memory["id"] in {record["id"] for record in exported["memories"]}
    assert skill["id"] in {record["id"] for record in exported["skills"]}
    assert blocked.status_code == 409
    assert imported["mode"] == "replace"
    assert imported["memories"]["created"] == len(exported["memories"])
    assert imported["skills"]["created"] == len(exported["skills"])
    assert memories[0]["id"] == memory["id"]
    assert skills[0]["id"] == skill["id"]


def test_knowledge_merge_import_is_idempotent_for_existing_records(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    exported = client.get("/knowledge/export").json()

    first = client.post("/knowledge/import", json={**exported, "mode": "merge"}).json()
    second = client.post("/knowledge/import", json={**exported, "mode": "merge"}).json()

    assert first["memories"]["updated"] >= 1
    assert second["memories"]["updated"] >= 1
    assert second["skills"]["created"] == 0


def test_retry_and_cancel_run_controls(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    run_id = client.post("/runs", json={"goal": "research memory"}).json()["id"]

    retry = client.post(f"/runs/{run_id}/retry").json()
    cancelled = client.post(f"/runs/{retry['id']}/cancel").json()

    assert retry["goal"] == "research memory"
    assert cancelled["status"] == "cancelled"
    events = client.get(f"/runs/{retry['id']}/events.json").json()
    event_types = [event["event_type"] for event in events]
    assert "run.cancelled" in event_types


def test_memory_endpoint_returns_records_used_by_run(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    run_id = client.post("/runs", json={"goal": "research memory"}).json()["id"]

    memories = client.get(f"/runs/{run_id}/memory").json()

    assert memories[0]["text"] == "Research runs should show memory hits."


def test_health_reports_missing_model_configuration(tmp_path: Path) -> None:
    client = build_client(tmp_path)
    client.app.state.runtime.model_client.configured = False

    health = client.get("/health").json()

    assert health["database"] == "ok"
    assert health["model"] == "missing_configuration"

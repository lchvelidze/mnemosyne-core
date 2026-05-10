from pathlib import Path

from fastapi.testclient import TestClient

from mnemosyne_core.agent import AgentRuntime
from mnemosyne_core.api import create_app
from mnemosyne_core.config import Settings
from mnemosyne_core.db import Database
from mnemosyne_core.memory import MemoryStore
from mnemosyne_core.model_client import ModelRequest, ModelResponse, ToolCallRequest
from mnemosyne_core.tools import ToolRegistry


class StubModelClient:
    configured = True

    async def complete(self, request: ModelRequest) -> ModelResponse:
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
    memory.add("Research runs should show memory hits.", source="seed")
    registry = ToolRegistry.safe_defaults(
        Settings(database_path=str(tmp_path / "mnemosyne.db"), allowed_file_roots=[str(tmp_path)])
    )
    runtime = AgentRuntime(db, memory, registry, StubModelClient())
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
    assert run["final_answer"] == "Answer for research memory"
    assert run["contract"]["constraints"] == "Use only local memory and safe tools."
    assert run["contract"]["allowed_tools"] == ["calculator"]
    assert client.get("/runs").json()[0]["id"] == run_id

    reopened = build_client(tmp_path)
    assert reopened.get("/runs").json()[0]["id"] == run_id


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

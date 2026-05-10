from pathlib import Path

import pytest

from mnemosyne_core.agent import AgentRuntime
from mnemosyne_core.config import Settings
from mnemosyne_core.db import Database
from mnemosyne_core.memory import MemoryStore
from mnemosyne_core.model_client import ModelRequest, ModelResponse, ToolCallRequest
from mnemosyne_core.models import TaskContract
from mnemosyne_core.tools import ToolRegistry


class StubModelClient:
    configured = True

    async def complete(self, request: ModelRequest) -> ModelResponse:
        assert request.goal == "research battery safety"
        assert request.memories[0].text.startswith("Solar battery research")
        if request.tool_results:
            assert request.tool_results[0]["tool_name"] == "calculator"
            return ModelResponse(
                message="Synthesized answer after tool use.",
                tool_calls=[],
            )
        return ModelResponse(
            message="I should calculate a simple check first.",
            tool_calls=[
                ToolCallRequest(name="calculator", arguments={"expression": "2 + 2"}),
            ],
        )


class ToolBlockSynthesisModelClient:
    configured = True

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if request.tool_results:
            return ModelResponse(
                message=(
                    '<tool_calls>[{"name":"web_search","arguments":{"query":"again"}}]</tool_calls>\n'
                    "## Search Summary\n\n- [Result](https://example.com) was found."
                )
            )
        return ModelResponse(
            message="",
            tool_calls=[
                ToolCallRequest(
                    name="web_search",
                    arguments={"query": "agent memory systems", "max_results": 1},
                )
            ],
        )


class LateWriteSynthesisModelClient:
    configured = True

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if not request.tool_results:
            return ModelResponse(
                message="",
                tool_calls=[
                    ToolCallRequest(
                        name="web_search",
                        arguments={"query": "agent memory benchmarks", "max_results": 1},
                    )
                ],
            )
        if not any(result["tool_name"] == "write_text_file" for result in request.tool_results):
            return ModelResponse(
                message=(
                    '<tool_call tool_name="write_text_file">\n'
                    f'  <arg name="path">{request.goal}</arg>\n'
                    "  <arg name=\"text\"># Benchmark Report\n\nWritten after research.</arg>\n"
                    '  <arg name="overwrite">true</arg>\n'
                    "</tool_call>\n"
                    "Created the benchmark report."
                ),
                tool_calls=[
                    ToolCallRequest(
                        name="write_text_file",
                        arguments={
                            "path": request.goal,
                            "text": "# Benchmark Report\n\nWritten after research.",
                            "overwrite": True,
                        },
                    )
                ],
            )
        return ModelResponse(message="Created the benchmark report from executed tool results.")


@pytest.mark.asyncio()
async def test_agent_run_records_ordered_events_memory_tool_and_eval(tmp_path: Path) -> None:
    db = Database(tmp_path / "mnemosyne.db")
    db.initialize()
    memory = MemoryStore(db)
    memory.add("Solar battery research prefers LFP chemistry for safety.", source="seed")
    registry = ToolRegistry.safe_defaults(
        Settings(database_path=str(tmp_path / "mnemosyne.db"), allowed_file_roots=[str(tmp_path)])
    )
    runtime = AgentRuntime(db, memory, registry, StubModelClient())

    run = await runtime.run_goal("research battery safety")

    events = db.list_events(run.id)
    event_types = [event.event_type for event in events]
    assert run.status == "completed"
    assert run.final_answer == "Synthesized answer after tool use."
    assert event_types == [
        "run.created",
        "plan.created",
        "memory.retrieved",
        "model.started",
        "model.completed",
        "tool.started",
        "tool.completed",
        "model.synthesis_started",
        "model.synthesis_completed",
        "eval.completed",
        "run.completed",
    ]
    assert db.get_eval(run.id).passed is True


@pytest.mark.asyncio()
async def test_agent_blocks_tools_outside_run_contract(tmp_path: Path) -> None:
    db = Database(tmp_path / "mnemosyne.db")
    db.initialize()
    memory = MemoryStore(db)
    memory.add("Solar battery research prefers LFP chemistry for safety.", source="seed")
    registry = ToolRegistry.safe_defaults(
        Settings(database_path=str(tmp_path / "mnemosyne.db"), allowed_file_roots=[str(tmp_path)])
    )
    contract = TaskContract(
        goal="research battery safety",
        constraints="Calculator is disabled for this run.",
        allowed_tools=["web_search"],
        success_criteria=["Do not execute disallowed tools"],
        expected_output="Markdown",
    )
    runtime = AgentRuntime(db, memory, registry, StubModelClient())

    run = await runtime.run_goal("research battery safety", contract)

    event_types = [event.event_type for event in db.list_events(run.id)]
    assert "tool.blocked" in event_types
    blocked = next(event for event in db.list_events(run.id) if event.event_type == "tool.blocked")
    assert blocked.payload["tool_name"] == "calculator"
    assert blocked.payload["allowed_tools"] == ["web_search"]


@pytest.mark.asyncio()
async def test_agent_strips_accidental_tool_call_blocks_from_final_answer(tmp_path: Path) -> None:
    db = Database(tmp_path / "mnemosyne.db")
    db.initialize()
    memory = MemoryStore(db)
    registry = ToolRegistry()
    defaults = registry.safe_defaults(
        Settings(
            database_path=str(tmp_path / "mnemosyne.db"),
            allowed_file_roots=[str(tmp_path)],
            terminal_enabled=False,
        )
    )
    registry.register(
        next(spec for spec in defaults.specs() if spec.name == "web_search"),
        lambda _arguments: {
            "query": "agent memory systems",
            "results": [{"title": "Result", "url": "https://example.com", "snippet": "Snippet"}],
        },
    )
    runtime = AgentRuntime(db, memory, registry, ToolBlockSynthesisModelClient())

    run = await runtime.run_goal("agent memory systems")

    assert "<tool_calls>" not in (run.final_answer or "")
    assert run.final_answer == "## Search Summary\n\n- [Result](https://example.com) was found."


@pytest.mark.asyncio()
async def test_agent_executes_tool_calls_requested_during_synthesis(tmp_path: Path) -> None:
    db = Database(tmp_path / "mnemosyne.db")
    db.initialize()
    memory = MemoryStore(db)
    registry = ToolRegistry.safe_defaults(
        Settings(database_path=str(tmp_path / "mnemosyne.db"), allowed_file_roots=[str(tmp_path)])
    )
    web_search_spec = next(spec for spec in registry.specs() if spec.name == "web_search")
    registry.register(
        web_search_spec,
        lambda _arguments: {
            "query": "agent memory benchmarks",
            "results": [{"title": "Result", "url": "https://example.com", "snippet": "Snippet"}],
        },
    )
    target = tmp_path / "myagent_data.md"
    runtime = AgentRuntime(db, memory, registry, LateWriteSynthesisModelClient())

    run = await runtime.run_goal(str(target))

    completed_tools = [
        event.payload["tool_name"]
        for event in db.list_events(run.id)
        if event.event_type == "tool.completed"
    ]
    assert completed_tools == ["web_search", "write_text_file"]
    assert target.read_text(encoding="utf-8") == "# Benchmark Report\n\nWritten after research."
    assert run.final_answer == "Created the benchmark report from executed tool results."


@pytest.mark.asyncio()
async def test_agent_can_continue_existing_run_without_duplicate_creation_event(
    tmp_path: Path,
) -> None:
    db = Database(tmp_path / "mnemosyne.db")
    db.initialize()
    memory = MemoryStore(db)
    memory.add("Solar battery research prefers LFP chemistry for safety.", source="seed")
    registry = ToolRegistry.safe_defaults(
        Settings(database_path=str(tmp_path / "mnemosyne.db"), allowed_file_roots=[str(tmp_path)])
    )
    runtime = AgentRuntime(db, memory, registry, StubModelClient())
    run = db.create_run("research battery safety")
    db.append_event(run.id, "run.created", {"goal": run.goal, "status": "running"})

    completed = await runtime.continue_run(run.id, run.goal)

    event_types = [event.event_type for event in db.list_events(run.id)]
    assert completed.status == "completed"
    assert event_types.count("run.created") == 1

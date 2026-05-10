from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel, Field

from mnemosyne_core.agent import AgentRuntime
from mnemosyne_core.db import default_contract
from mnemosyne_core.models import TaskContract


class CreateRunRequest(BaseModel):
    goal: str = Field(min_length=1)
    constraints: str | None = None
    allowed_tools: list[str] | None = None
    success_criteria: list[str] | None = None
    expected_output: str | None = None


class MemoryRequest(BaseModel):
    text: str = Field(min_length=1)
    source: str = Field(default="manual", min_length=1)
    tags: list[str] = Field(default_factory=list)
    importance: float = Field(default=0.5, ge=0, le=1)


def create_app(runtime: AgentRuntime, *, run_inline: bool = False) -> FastAPI:
    app = FastAPI(title="Mnemosyne Core", version="0.1.0")
    app.state.runtime = runtime
    app.state.run_inline = run_inline
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.post("/runs", status_code=201)
    async def create_run(payload: CreateRunRequest) -> dict:
        contract = _contract_from_payload(runtime, payload)
        if run_inline:
            run = await runtime.run_goal(payload.goal, contract)
        else:
            run = runtime.db.create_run(payload.goal, contract)
            runtime.db.append_event(
                run.id,
                "run.created",
                {
                    "goal": payload.goal,
                    "status": "running",
                    "contract": contract.to_dict(),
                },
            )
            asyncio.create_task(_continue_existing_run(runtime, run.id, payload.goal))
        return run.to_dict(runtime.db.get_eval(run.id))

    @app.get("/runs")
    def list_runs() -> list[dict]:
        return [run.to_dict(runtime.db.get_eval(run.id)) for run in runtime.db.list_runs()]

    @app.post("/runs/{run_id}/retry", status_code=201)
    async def retry_run(run_id: str) -> dict:
        original = runtime.db.get_run(run_id)
        if original is None:
            raise HTTPException(status_code=404, detail="Run not found")
        contract = original.contract or default_contract(original.goal, runtime.tools.names())
        if run_inline:
            run = await runtime.run_goal(original.goal, contract)
        else:
            run = runtime.db.create_run(original.goal, contract)
            runtime.db.append_event(
                run.id,
                "run.created",
                {
                    "goal": run.goal,
                    "status": "running",
                    "contract": run.contract.to_dict() if run.contract else None,
                    "retry_of": run_id,
                },
            )
            asyncio.create_task(_continue_existing_run(runtime, run.id, run.goal))
        return run.to_dict(runtime.db.get_eval(run.id))

    @app.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: str) -> dict:
        run = runtime.db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        cancelled = runtime.db.update_run(run_id, status="cancelled", error="Cancelled by user")
        runtime.db.append_event(run_id, "run.cancelled", {"status": "cancelled"})
        return cancelled.to_dict(runtime.db.get_eval(run_id))

    @app.get("/runs/{run_id}")
    def get_run(run_id: str) -> dict:
        run = runtime.db.get_run(run_id)
        if run is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return run.to_dict(runtime.db.get_eval(run.id))

    @app.get("/tools")
    def list_tools() -> list[dict]:
        return [tool.to_dict() for tool in runtime.tools.specs()]

    @app.get("/runs/{run_id}/events")
    async def stream_events(run_id: str) -> StreamingResponse:
        if runtime.db.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return StreamingResponse(_event_stream(runtime, run_id), media_type="text/event-stream")

    @app.get("/runs/{run_id}/events.json")
    def list_run_events(run_id: str) -> list[dict]:
        if runtime.db.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return [event.to_dict() for event in runtime.db.list_events(run_id)]

    @app.get("/runs/{run_id}/memory")
    def list_run_memory(run_id: str) -> list[dict]:
        if runtime.db.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="Run not found")
        return [record.to_dict() for record in runtime.db.list_run_memories(run_id)]

    @app.get("/memory")
    def list_memory(query: str | None = None) -> list[dict]:
        records = runtime.memory.search(query, limit=25) if query else runtime.db.list_memories()
        return [record.to_dict() for record in records]

    @app.post("/memory", status_code=201)
    def create_memory(payload: MemoryRequest) -> dict:
        return runtime.memory.add(
            payload.text,
            source=payload.source,
            tags=payload.tags,
            importance=payload.importance,
        ).to_dict()

    @app.put("/memory/{memory_id}")
    def update_memory(memory_id: str, payload: MemoryRequest) -> dict:
        if runtime.db.get_memory(memory_id) is None:
            raise HTTPException(status_code=404, detail="Memory not found")
        return runtime.db.update_memory(
            memory_id,
            text=payload.text,
            source=payload.source,
            tags=payload.tags,
            importance=payload.importance,
        ).to_dict()

    @app.delete("/memory/{memory_id}", status_code=204)
    def delete_memory(memory_id: str) -> Response:
        if not runtime.db.delete_memory(memory_id):
            raise HTTPException(status_code=404, detail="Memory not found")
        return Response(status_code=204)

    @app.get("/health")
    def health() -> dict[str, str]:
        db_status = "ok" if runtime.db.path.exists() else "missing"
        model_status = "ok" if runtime.model_client.configured else "missing_configuration"
        return {"database": db_status, "model": model_status}

    return app


async def _continue_existing_run(runtime: AgentRuntime, run_id: str, goal: str) -> None:
    await runtime.continue_run(run_id, goal)


def _contract_from_payload(runtime: AgentRuntime, payload: CreateRunRequest) -> TaskContract:
    allowed_tools = payload.allowed_tools or runtime.tools.names()
    known_tools = set(runtime.tools.names())
    unknown = sorted(set(allowed_tools) - known_tools)
    if unknown:
        raise HTTPException(status_code=400, detail=f"Unknown tool(s): {', '.join(unknown)}")
    base = default_contract(payload.goal, allowed_tools)
    return TaskContract(
        goal=payload.goal,
        constraints=payload.constraints or base.constraints,
        allowed_tools=allowed_tools,
        success_criteria=payload.success_criteria or base.success_criteria,
        expected_output=payload.expected_output or base.expected_output,
    )


async def _event_stream(runtime: AgentRuntime, run_id: str) -> AsyncIterator[str]:
    sequence = 0
    while True:
        events = runtime.db.list_events(run_id, after_sequence=sequence)
        for event in events:
            sequence = event.sequence
            yield (
                f"id: {event.sequence}\n"
                f"event: {event.event_type}\n"
                f"data: {json.dumps(event.payload)}\n\n"
            )
        run = runtime.db.get_run(run_id)
        if run and run.status in {"completed", "failed", "cancelled"} and not events:
            break
        await asyncio.sleep(0.2)

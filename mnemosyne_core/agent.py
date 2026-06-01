from __future__ import annotations

import asyncio
import re
from time import perf_counter

from mnemosyne_core.db import Database, default_contract
from mnemosyne_core.evals import score_answer
from mnemosyne_core.jobs import TerminalJobManager
from mnemosyne_core.memory import MemoryStore
from mnemosyne_core.model_client import ModelClient, ModelRequest, ModelResponse
from mnemosyne_core.models import AgentRun, TaskContract
from mnemosyne_core.skills import SkillStore
from mnemosyne_core.tools import ToolExecutionError, ToolRegistry


class AgentRuntime:
    def __init__(
        self,
        db: Database,
        memory: MemoryStore,
        tools: ToolRegistry,
        model_client: ModelClient,
        skills: SkillStore | None = None,
        jobs: TerminalJobManager | None = None,
        model_timeout_seconds: float = 120.0,
    ) -> None:
        self.db = db
        self.memory = memory
        self.tools = tools
        self.model_client = model_client
        self.skills = skills
        self.jobs = jobs
        self.model_timeout_seconds = model_timeout_seconds

    async def run_goal(
        self,
        goal: str,
        contract: TaskContract | None = None,
        *,
        thread_id: str | None = None,
    ) -> AgentRun:
        contract = contract or default_contract(goal, self.tools.names())
        thread_id = self._ensure_thread(goal, thread_id)
        run = self.db.create_run(goal, contract, thread_id=thread_id)
        self.db.append_thread_message(thread_id, role="user", content=goal, run_id=run.id)
        self.db.append_event(
            run.id,
            "run.created",
            {
                "goal": goal,
                "status": "running",
                "contract": run.contract.to_dict() if run.contract else None,
            },
        )
        return await self._execute_run(run.id, goal)

    async def continue_run(self, run_id: str, goal: str) -> AgentRun:
        run = self.db.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return await self._execute_run(run_id, goal)

    async def _execute_run(self, run_id: str, goal: str) -> AgentRun:
        try:
            run = self.db.get_run(run_id)
            if run is None:
                raise ValueError(f"Run not found: {run_id}")
            contract = self.db.get_contract(run_id)
            if contract and contract.allowed_tools:
                allowed_tools = contract.allowed_tools
            else:
                allowed_tools = self.tools.names()
            conversation_messages = (
                self.db.recent_thread_messages(run.thread_id, limit=12) if run.thread_id else []
            )
            self.db.append_event(run_id, "plan.created", build_plan(goal, allowed_tools, contract))
            memories = self.memory.search(goal, limit=5)
            skills = self.skills.search(goal, limit=5) if self.skills else []
            self.db.attach_run_memories(run_id, memories)
            self.db.append_event(
                run_id,
                "memory.retrieved",
                {"records": [memory.to_dict() for memory in memories]},
            )
            self.db.append_event(
                run_id,
                "skills.retrieved",
                {"skills": [skill.to_dict() for skill in skills]},
            )
            self.db.append_event(
                run_id,
                "model.started",
                {"model_configured": self.model_client.configured},
            )
            response = await self._complete_model(
                ModelRequest(
                    goal=goal,
                    memories=memories,
                    skills=skills,
                    tools=self.tools.specs(allowed_tools),
                    conversation_messages=conversation_messages,
                ),
            )
            self.db.append_event(
                run_id,
                "model.completed",
                {
                    "message": response.message,
                    "tool_calls": [
                        {"name": call.name, "arguments": call.arguments}
                        for call in response.tool_calls
                    ],
                },
            )
            completed_tools, tool_results = self._execute_tool_calls(
                run_id,
                response.tool_calls,
                allowed_tools,
            )
            final_answer = response.message
            if tool_results:
                self.db.append_event(
                    run_id,
                    "model.synthesis_started",
                    {"tool_result_count": len(tool_results)},
                )
                synthesis_response = await self._complete_model(
                    ModelRequest(
                        goal=goal,
                        memories=memories,
                        skills=skills,
                        tools=self.tools.specs(allowed_tools),
                        tool_results=tool_results,
                        conversation_messages=conversation_messages,
                    ),
                )
                final_answer = _clean_final_answer(synthesis_response.message or response.message)
                if synthesis_response.tool_calls:
                    self.db.append_event(
                        run_id,
                        "model.synthesis_tool_calls_detected",
                        {
                            "tool_calls": [
                                {"name": call.name, "arguments": call.arguments}
                                for call in synthesis_response.tool_calls
                            ]
                        },
                    )
                    late_count, late_results = self._execute_tool_calls(
                        run_id,
                        synthesis_response.tool_calls,
                        allowed_tools,
                    )
                    completed_tools += late_count
                    if late_results:
                        tool_results.extend(late_results)
                        self.db.append_event(
                            run_id,
                            "model.synthesis_retry_started",
                            {"tool_result_count": len(tool_results)},
                        )
                        final_response = await self._complete_model(
                            ModelRequest(
                                goal=goal,
                                memories=memories,
                                skills=skills,
                                tools=self.tools.specs(allowed_tools),
                                tool_results=tool_results,
                                conversation_messages=conversation_messages,
                            ),
                        )
                        final_answer = _clean_final_answer(
                            final_response.message or final_answer
                        )
                self.db.append_event(
                    run_id,
                    "model.synthesis_completed",
                    {"message": final_answer},
                )
            eval_score = score_answer(
                final_answer,
                goal=goal,
                success_criteria=contract.success_criteria if contract else [],
                used_memory=bool(memories),
                tool_count=completed_tools,
                tool_results=tool_results,
            )
            eval_result = self.db.add_eval(
                run_id,
                score=eval_score.score,
                notes=eval_score.notes,
                passed=eval_score.passed,
                rubric=eval_score.rubric,
                evaluator_version=eval_score.evaluator_version,
            )
            self.db.append_event(run_id, "eval.completed", eval_result.to_dict())
            run = self.db.update_run(run_id, status="completed", final_answer=final_answer)
            if run.thread_id:
                self.db.append_thread_message(
                    run.thread_id,
                    role="assistant",
                    content=final_answer,
                    run_id=run.id,
                )
            self.db.append_event(
                run_id,
                "run.completed",
                {"status": "completed", "final_answer": final_answer},
            )
            return run
        except Exception as exc:
            eval_score = score_answer(
                None,
                goal=goal,
                success_criteria=[],
                used_memory=False,
                tool_count=0,
                error=str(exc),
            )
            eval_result = self.db.add_eval(
                run_id,
                score=eval_score.score,
                notes=eval_score.notes,
                passed=eval_score.passed,
                rubric=eval_score.rubric,
                evaluator_version=eval_score.evaluator_version,
            )
            self.db.append_event(run_id, "eval.completed", eval_result.to_dict())
            failed = self.db.update_run(run_id, status="failed", error=str(exc))
            if failed.thread_id:
                self.db.append_thread_message(
                    failed.thread_id,
                    role="assistant",
                    content=f"Run failed: {exc}",
                    run_id=failed.id,
                )
            self.db.append_event(run_id, "run.failed", {"status": "failed", "error": str(exc)})
            return failed

    async def _complete_model(self, request: ModelRequest) -> ModelResponse:
        try:
            return await asyncio.wait_for(
                self.model_client.complete(request),
                timeout=self.model_timeout_seconds,
            )
        except TimeoutError as exc:
            raise TimeoutError(
                f"Model call timed out after {self.model_timeout_seconds:g}s"
            ) from exc

    def _ensure_thread(self, goal: str, thread_id: str | None) -> str:
        if thread_id is None:
            return self.db.create_thread(goal).id
        if self.db.get_thread(thread_id) is None:
            raise ValueError(f"Thread not found: {thread_id}")
        return thread_id

    def _execute_tool_calls(
        self,
        run_id: str,
        tool_calls: list,
        allowed_tools: list[str],
    ) -> tuple[int, list[dict]]:
        completed_tools = 0
        tool_results: list[dict] = []
        for tool_call in tool_calls:
            if tool_call.name not in allowed_tools:
                error = f"Tool is not allowed by this run contract: {tool_call.name}"
                self.db.record_tool_call(
                    run_id=run_id,
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    result=None,
                    status="blocked",
                    duration_ms=0,
                    error=error,
                )
                self.db.append_event(
                    run_id,
                    "tool.blocked",
                    {
                        "tool_name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "error": error,
                        "allowed_tools": allowed_tools,
                    },
                )
                continue
            self.db.append_event(
                run_id,
                "tool.started",
                {"tool_name": tool_call.name, "arguments": tool_call.arguments},
            )
            started = perf_counter()
            try:
                result = self.tools.execute(tool_call.name, tool_call.arguments)
                duration_ms = int((perf_counter() - started) * 1000)
                completed_tools += 1
                tool_results.append(
                    {
                        "tool_name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "status": "completed",
                        "result": result,
                    }
                )
                self.db.record_tool_call(
                    run_id=run_id,
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    result=result,
                    status="completed",
                    duration_ms=duration_ms,
                )
                self.db.append_event(
                    run_id,
                    "tool.completed",
                    {
                        "tool_name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "result": result,
                        "duration_ms": duration_ms,
                    },
                )
            except ToolExecutionError as exc:
                duration_ms = int((perf_counter() - started) * 1000)
                tool_results.append(
                    {
                        "tool_name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
                self.db.record_tool_call(
                    run_id=run_id,
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    result=None,
                    status="failed",
                    duration_ms=duration_ms,
                    error=str(exc),
                )
                self.db.append_event(
                    run_id,
                    "tool.failed",
                    {
                        "tool_name": tool_call.name,
                        "arguments": tool_call.arguments,
                        "error": str(exc),
                        "duration_ms": duration_ms,
                    },
                )
        return completed_tools, tool_results


def _clean_final_answer(answer: str) -> str:
    without_xml_blocks = re.sub(
        r"<tool_calls>.*?</tool_calls>",
        "",
        answer,
        flags=re.DOTALL | re.IGNORECASE,
    )
    without_json_prefix = re.sub(
        r"^\s*tool_calls\s*:\s*\[.*?\]\s*",
        "",
        without_xml_blocks,
        flags=re.DOTALL | re.IGNORECASE,
    )
    return without_json_prefix.strip()


def build_plan(
    goal: str,
    allowed_tools: list[str],
    contract: TaskContract | None = None,
) -> dict:
    success_criteria = contract.success_criteria if contract else []
    return {
        "goal": goal,
        "allowed_tools": allowed_tools,
        "steps": [
            "Review the run contract and permission boundary.",
            "Retrieve relevant memory records from local SQLite FTS.",
            "Retrieve relevant reusable skills from local SQLite FTS.",
            "Ask the model for a tool-aware research step.",
            "Execute only tools allowed by the contract.",
            "Synthesize a Markdown answer and create an eval record.",
        ],
        "success_criteria": success_criteria,
    }

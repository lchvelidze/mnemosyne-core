from __future__ import annotations

import json
import sqlite3
import uuid
from pathlib import Path
from typing import Any

from mnemosyne_core.models import (
    AgentRun,
    EvalResult,
    MemoryRecord,
    RunEvent,
    SkillRecord,
    TaskContract,
    TerminalJob,
    TerminalJobLog,
    now_iso,
)


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    goal TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    final_answer TEXT,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS run_contracts (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
                    constraints TEXT NOT NULL,
                    allowed_tools TEXT NOT NULL,
                    success_criteria TEXT NOT NULL,
                    expected_output TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS memories (
                    id TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    source TEXT NOT NULL,
                    tags TEXT NOT NULL,
                    importance REAL NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(memory_id UNINDEXED, text);
                CREATE TABLE IF NOT EXISTS skills (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    trigger_terms TEXT NOT NULL,
                    tool_names TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE VIRTUAL TABLE IF NOT EXISTS skills_fts
                USING fts5(skill_id UNINDEXED, name, description, instructions, trigger_terms);
                CREATE TABLE IF NOT EXISTS run_memories (
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    memory_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
                    PRIMARY KEY(run_id, memory_id)
                );
                CREATE TABLE IF NOT EXISTS tool_calls (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
                    tool_name TEXT NOT NULL,
                    arguments TEXT NOT NULL,
                    result TEXT,
                    status TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL,
                    error TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS eval_results (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
                    score REAL NOT NULL,
                    notes TEXT NOT NULL,
                    passed INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    rubric TEXT NOT NULL DEFAULT '[]',
                    evaluator_version TEXT NOT NULL DEFAULT 'legacy'
                );
                CREATE TABLE IF NOT EXISTS terminal_jobs (
                    id TEXT PRIMARY KEY,
                    shell TEXT NOT NULL,
                    command TEXT NOT NULL,
                    working_directory TEXT NOT NULL,
                    shell_mode TEXT,
                    status TEXT NOT NULL,
                    pid INTEGER,
                    exit_code INTEGER,
                    error TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS terminal_job_logs (
                    id TEXT PRIMARY KEY,
                    job_id TEXT NOT NULL REFERENCES terminal_jobs(id) ON DELETE CASCADE,
                    sequence INTEGER NOT NULL,
                    stream TEXT NOT NULL,
                    text TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(job_id, sequence)
                );
                """
            )
            self._ensure_column(conn, "eval_results", "rubric", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column(
                conn,
                "eval_results",
                "evaluator_version",
                "TEXT NOT NULL DEFAULT 'legacy'",
            )

    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def create_run(self, goal: str, contract: TaskContract | None = None) -> AgentRun:
        timestamp = now_iso()
        contract = contract or default_contract(goal, [])
        run = AgentRun(
            id=str(uuid.uuid4()),
            goal=goal,
            status="running",
            created_at=timestamp,
            updated_at=timestamp,
            contract=contract,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO runs (id, goal, status, created_at, updated_at, final_answer, error)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.goal,
                    run.status,
                    run.created_at,
                    run.updated_at,
                    run.final_answer,
                    run.error,
                ),
            )
            conn.execute(
                """
                INSERT INTO run_contracts
                (run_id, constraints, allowed_tools, success_criteria, expected_output)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    contract.constraints,
                    json.dumps(contract.allowed_tools),
                    json.dumps(contract.success_criteria),
                    contract.expected_output,
                ),
            )
        return run

    def update_run(
        self,
        run_id: str,
        *,
        status: str,
        final_answer: str | None = None,
        error: str | None = None,
    ) -> AgentRun:
        timestamp = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE runs
                SET status = ?, updated_at = ?, final_answer = COALESCE(?, final_answer), error = ?
                WHERE id = ?
                """,
                (status, timestamp, final_answer, error, run_id),
            )
        run = self.get_run(run_id)
        if run is None:
            raise ValueError(f"Run not found: {run_id}")
        return run

    def get_run(self, run_id: str) -> AgentRun | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return self._run_from_row(row) if row else None

    def list_runs(self) -> list[AgentRun]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM runs ORDER BY created_at DESC").fetchall()
        return [self._run_from_row(row) for row in rows]

    def get_contract(self, run_id: str) -> TaskContract | None:
        run = self.get_run(run_id)
        return run.contract if run else None

    def append_event(self, run_id: str, event_type: str, payload: dict[str, Any]) -> RunEvent:
        with self.connect() as conn:
            next_sequence = (
                conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE run_id = ?",
                    (run_id,),
                ).fetchone()[0]
            )
            event = RunEvent(
                id=str(uuid.uuid4()),
                run_id=run_id,
                sequence=next_sequence,
                event_type=event_type,
                payload=payload,
                created_at=now_iso(),
            )
            conn.execute(
                """
                INSERT INTO events (id, run_id, sequence, event_type, payload, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.run_id,
                    event.sequence,
                    event.event_type,
                    json.dumps(event.payload),
                    event.created_at,
                ),
            )
        return event

    def list_events(self, run_id: str, after_sequence: int = 0) -> list[RunEvent]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM events
                WHERE run_id = ? AND sequence > ?
                ORDER BY sequence ASC
                """,
                (run_id, after_sequence),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def add_memory(
        self,
        text: str,
        *,
        source: str,
        tags: list[str] | None = None,
        importance: float = 0.5,
    ) -> MemoryRecord:
        record = MemoryRecord(
            id=str(uuid.uuid4()),
            text=text,
            source=source,
            tags=tags or [],
            importance=importance,
            created_at=now_iso(),
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO memories (id, text, source, tags, importance, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    record.id,
                    record.text,
                    record.source,
                    json.dumps(record.tags),
                    record.importance,
                    record.created_at,
                ),
            )
            conn.execute(
                "INSERT INTO memories_fts (memory_id, text) VALUES (?, ?)",
                (record.id, record.text),
            )
        return record

    def list_memories(self) -> list[MemoryRecord]:
        with self.connect() as conn:
            rows = conn.execute("SELECT * FROM memories ORDER BY created_at DESC").fetchall()
        return [self._memory_from_row(row) for row in rows]

    def get_memory(self, memory_id: str) -> MemoryRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM memories WHERE id = ?", (memory_id,)).fetchone()
        return self._memory_from_row(row) if row else None

    def update_memory(
        self,
        memory_id: str,
        *,
        text: str,
        source: str,
        tags: list[str],
        importance: float,
    ) -> MemoryRecord:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE memories
                SET text = ?, source = ?, tags = ?, importance = ?
                WHERE id = ?
                """,
                (text, source, json.dumps(tags), importance, memory_id),
            )
            conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,))
            conn.execute(
                "INSERT INTO memories_fts (memory_id, text) VALUES (?, ?)",
                (memory_id, text),
            )
        record = self.get_memory(memory_id)
        if record is None:
            raise ValueError(f"Memory not found: {memory_id}")
        return record

    def delete_memory(self, memory_id: str) -> bool:
        with self.connect() as conn:
            conn.execute("DELETE FROM memories_fts WHERE memory_id = ?", (memory_id,))
            cursor = conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return cursor.rowcount > 0

    def search_memories(self, query: str, limit: int = 5) -> list[MemoryRecord]:
        fts_query = self._fts_query(query)
        if not fts_query:
            return []
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT m.*
                FROM memories_fts f
                JOIN memories m ON m.id = f.memory_id
                WHERE memories_fts MATCH ?
                ORDER BY bm25(memories_fts), m.importance DESC, m.created_at DESC
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def add_skill(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        trigger_terms: list[str] | None = None,
        tool_names: list[str] | None = None,
        enabled: bool = True,
    ) -> SkillRecord:
        timestamp = now_iso()
        skill = SkillRecord(
            id=str(uuid.uuid4()),
            name=name,
            description=description,
            instructions=instructions,
            trigger_terms=trigger_terms or [],
            tool_names=tool_names or [],
            enabled=enabled,
            created_at=timestamp,
            updated_at=timestamp,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO skills
                (id, name, description, instructions, trigger_terms, tool_names,
                 enabled, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    skill.id,
                    skill.name,
                    skill.description,
                    skill.instructions,
                    json.dumps(skill.trigger_terms),
                    json.dumps(skill.tool_names),
                    int(skill.enabled),
                    skill.created_at,
                    skill.updated_at,
                ),
            )
            self._insert_skill_fts(conn, skill)
        return skill

    def list_skills(self, *, enabled_only: bool = False) -> list[SkillRecord]:
        where = "WHERE enabled = 1" if enabled_only else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"SELECT * FROM skills {where} ORDER BY updated_at DESC, created_at DESC"
            ).fetchall()
        return [self._skill_from_row(row) for row in rows]

    def get_skill(self, skill_id: str) -> SkillRecord | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
        return self._skill_from_row(row) if row else None

    def update_skill(
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
        timestamp = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE skills
                SET name = ?, description = ?, instructions = ?, trigger_terms = ?,
                    tool_names = ?, enabled = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    name,
                    description,
                    instructions,
                    json.dumps(trigger_terms),
                    json.dumps(tool_names),
                    int(enabled),
                    timestamp,
                    skill_id,
                ),
            )
            row = conn.execute("SELECT * FROM skills WHERE id = ?", (skill_id,)).fetchone()
            if row is None:
                raise ValueError(f"Skill not found: {skill_id}")
            skill = self._skill_from_row(row)
            conn.execute("DELETE FROM skills_fts WHERE skill_id = ?", (skill_id,))
            self._insert_skill_fts(conn, skill)
        return skill

    def delete_skill(self, skill_id: str) -> bool:
        with self.connect() as conn:
            conn.execute("DELETE FROM skills_fts WHERE skill_id = ?", (skill_id,))
            cursor = conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
        return cursor.rowcount > 0

    def search_skills(
        self,
        query: str,
        *,
        limit: int = 5,
        enabled_only: bool = True,
    ) -> list[SkillRecord]:
        fts_query = self._fts_query(query)
        if not fts_query:
            return []
        enabled_filter = "AND s.enabled = 1" if enabled_only else ""
        with self.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT s.*
                FROM skills_fts f
                JOIN skills s ON s.id = f.skill_id
                WHERE skills_fts MATCH ? {enabled_filter}
                ORDER BY bm25(skills_fts), s.updated_at DESC
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
        return [self._skill_from_row(row) for row in rows]

    def attach_run_memories(self, run_id: str, memories: list[MemoryRecord]) -> None:
        with self.connect() as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO run_memories (run_id, memory_id) VALUES (?, ?)",
                [(run_id, memory.id) for memory in memories],
            )

    def list_run_memories(self, run_id: str) -> list[MemoryRecord]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT m.*
                FROM run_memories rm
                JOIN memories m ON m.id = rm.memory_id
                WHERE rm.run_id = ?
                ORDER BY m.created_at DESC
                """,
                (run_id,),
            ).fetchall()
        return [self._memory_from_row(row) for row in rows]

    def record_tool_call(
        self,
        *,
        run_id: str,
        tool_name: str,
        arguments: dict[str, Any],
        result: dict[str, Any] | None,
        status: str,
        duration_ms: int,
        error: str | None = None,
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO tool_calls
                (id, run_id, tool_name, arguments, result, status, duration_ms, error, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    run_id,
                    tool_name,
                    json.dumps(arguments),
                    json.dumps(result) if result is not None else None,
                    status,
                    duration_ms,
                    error,
                    now_iso(),
                ),
            )

    def add_eval(
        self,
        run_id: str,
        *,
        score: float,
        notes: str,
        passed: bool,
        rubric: list[dict[str, Any]] | None = None,
        evaluator_version: str = "legacy",
    ) -> EvalResult:
        result = EvalResult(
            run_id=run_id,
            score=score,
            notes=notes,
            passed=passed,
            created_at=now_iso(),
            rubric=rubric or [],
            evaluator_version=evaluator_version,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO eval_results
                (run_id, score, notes, passed, created_at, rubric, evaluator_version)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result.run_id,
                    result.score,
                    result.notes,
                    int(result.passed),
                    result.created_at,
                    json.dumps(result.rubric),
                    result.evaluator_version,
                ),
            )
        return result

    def get_eval(self, run_id: str) -> EvalResult | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM eval_results WHERE run_id = ?", (run_id,)).fetchone()
        return self._eval_from_row(row) if row else None

    def create_terminal_job(
        self,
        *,
        shell: str,
        command: str,
        working_directory: str,
        shell_mode: str | None,
    ) -> TerminalJob:
        timestamp = now_iso()
        job = TerminalJob(
            id=str(uuid.uuid4()),
            shell=shell,
            command=command,
            working_directory=working_directory,
            shell_mode=shell_mode,
            status="starting",
            pid=None,
            exit_code=None,
            error=None,
            created_at=timestamp,
            started_at=None,
            completed_at=None,
            updated_at=timestamp,
        )
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO terminal_jobs
                (id, shell, command, working_directory, shell_mode, status, pid, exit_code,
                 error, created_at, started_at, completed_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job.id,
                    job.shell,
                    job.command,
                    job.working_directory,
                    job.shell_mode,
                    job.status,
                    job.pid,
                    job.exit_code,
                    job.error,
                    job.created_at,
                    job.started_at,
                    job.completed_at,
                    job.updated_at,
                ),
            )
        return job

    def update_terminal_job(
        self,
        job_id: str,
        *,
        status: str,
        pid: int | None = None,
        exit_code: int | None = None,
        error: str | None = None,
        started_at: str | None = None,
        completed_at: str | None = None,
    ) -> TerminalJob:
        timestamp = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE terminal_jobs
                SET status = ?, pid = COALESCE(?, pid), exit_code = ?, error = ?,
                    started_at = COALESCE(?, started_at),
                    completed_at = COALESCE(?, completed_at),
                    updated_at = ?
                WHERE id = ?
                """,
                (status, pid, exit_code, error, started_at, completed_at, timestamp, job_id),
            )
        job = self.get_terminal_job(job_id)
        if job is None:
            raise ValueError(f"Terminal job not found: {job_id}")
        return job

    def get_terminal_job(self, job_id: str) -> TerminalJob | None:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM terminal_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._terminal_job_from_row(row) if row else None

    def list_terminal_jobs(self, limit: int = 25) -> list[TerminalJob]:
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT * FROM terminal_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._terminal_job_from_row(row) for row in rows]

    def mark_unattached_terminal_jobs_failed(self) -> None:
        timestamp = now_iso()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE terminal_jobs
                SET status = 'failed',
                    error = 'Process was not attached after backend restart.',
                    completed_at = COALESCE(completed_at, ?),
                    updated_at = ?
                WHERE status IN ('starting', 'running', 'cancelling')
                """,
                (timestamp, timestamp),
            )

    def append_terminal_job_log(self, job_id: str, stream: str, text: str) -> TerminalJobLog:
        with self.connect() as conn:
            next_sequence = (
                conn.execute(
                    "SELECT COALESCE(MAX(sequence), 0) + 1 FROM terminal_job_logs WHERE job_id = ?",
                    (job_id,),
                ).fetchone()[0]
            )
            log = TerminalJobLog(
                id=str(uuid.uuid4()),
                job_id=job_id,
                sequence=next_sequence,
                stream=stream,
                text=text,
                created_at=now_iso(),
            )
            conn.execute(
                """
                INSERT INTO terminal_job_logs (id, job_id, sequence, stream, text, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (log.id, log.job_id, log.sequence, log.stream, log.text, log.created_at),
            )
        return log

    def list_terminal_job_logs(
        self,
        job_id: str,
        *,
        after_sequence: int = 0,
        limit: int = 500,
    ) -> list[TerminalJobLog]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT * FROM terminal_job_logs
                WHERE job_id = ? AND sequence > ?
                ORDER BY sequence ASC
                LIMIT ?
                """,
                (job_id, after_sequence, limit),
            ).fetchall()
        return [self._terminal_job_log_from_row(row) for row in rows]

    @staticmethod
    def _fts_query(query: str) -> str:
        terms = ["".join(ch for ch in part if ch.isalnum()) for part in query.split()]
        terms = [term for term in terms if term]
        return " OR ".join(terms)

    def _run_from_row(self, row: sqlite3.Row) -> AgentRun:
        contract_row = self._contract_row(row["id"])
        return AgentRun(
            id=row["id"],
            goal=row["goal"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            final_answer=row["final_answer"],
            error=row["error"],
            contract=self._contract_from_row(row["goal"], contract_row) if contract_row else None,
        )

    def _contract_row(self, run_id: str) -> sqlite3.Row | None:
        with self.connect() as conn:
            return conn.execute(
                "SELECT * FROM run_contracts WHERE run_id = ?",
                (run_id,),
            ).fetchone()

    @staticmethod
    def _contract_from_row(goal: str, row: sqlite3.Row) -> TaskContract:
        return TaskContract(
            goal=goal,
            constraints=row["constraints"],
            allowed_tools=json.loads(row["allowed_tools"]),
            success_criteria=json.loads(row["success_criteria"]),
            expected_output=row["expected_output"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> RunEvent:
        return RunEvent(
            id=row["id"],
            run_id=row["run_id"],
            sequence=row["sequence"],
            event_type=row["event_type"],
            payload=json.loads(row["payload"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _memory_from_row(row: sqlite3.Row) -> MemoryRecord:
        return MemoryRecord(
            id=row["id"],
            text=row["text"],
            source=row["source"],
            tags=json.loads(row["tags"]),
            importance=row["importance"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _skill_from_row(row: sqlite3.Row) -> SkillRecord:
        return SkillRecord(
            id=row["id"],
            name=row["name"],
            description=row["description"],
            instructions=row["instructions"],
            trigger_terms=json.loads(row["trigger_terms"]),
            tool_names=json.loads(row["tool_names"]),
            enabled=bool(row["enabled"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _terminal_job_from_row(row: sqlite3.Row) -> TerminalJob:
        return TerminalJob(
            id=row["id"],
            shell=row["shell"],
            command=row["command"],
            working_directory=row["working_directory"],
            shell_mode=row["shell_mode"],
            status=row["status"],
            pid=row["pid"],
            exit_code=row["exit_code"],
            error=row["error"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _terminal_job_log_from_row(row: sqlite3.Row) -> TerminalJobLog:
        return TerminalJobLog(
            id=row["id"],
            job_id=row["job_id"],
            sequence=row["sequence"],
            stream=row["stream"],
            text=row["text"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _insert_skill_fts(conn: sqlite3.Connection, skill: SkillRecord) -> None:
        conn.execute(
            """
            INSERT INTO skills_fts
            (skill_id, name, description, instructions, trigger_terms)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                skill.id,
                skill.name,
                skill.description,
                skill.instructions,
                " ".join(skill.trigger_terms),
            ),
        )

    @staticmethod
    def _eval_from_row(row: sqlite3.Row) -> EvalResult:
        return EvalResult(
            run_id=row["run_id"],
            score=row["score"],
            notes=row["notes"],
            passed=bool(row["passed"]),
            created_at=row["created_at"],
            rubric=json.loads(row["rubric"]),
            evaluator_version=row["evaluator_version"],
        )

    @staticmethod
    def _ensure_column(
        conn: sqlite3.Connection,
        table: str,
        column: str,
        definition: str,
    ) -> None:
        columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in columns:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def default_contract(goal: str, allowed_tools: list[str]) -> TaskContract:
    return TaskContract(
        goal=goal,
        constraints=(
            "Use only configured safe tools. Do not use shell execution or private "
            "network targets."
        ),
        allowed_tools=allowed_tools,
        success_criteria=[
            "Use relevant memory when available",
            "Use at least one safe tool when it helps the goal",
            "Return a concise Markdown final answer",
            "Create an eval record",
        ],
        expected_output="Markdown answer with source links when web evidence is used.",
    )

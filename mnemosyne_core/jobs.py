from __future__ import annotations

import subprocess
import threading
from dataclasses import dataclass
from typing import IO, Any

from mnemosyne_core.config import Settings
from mnemosyne_core.db import Database
from mnemosyne_core.models import TerminalJob, now_iso
from mnemosyne_core.tools import (
    TerminalTool,
    ToolExecutionError,
    _block_dangerous_command,
    _normalize_wsl_shell_mode,
    _wsl_bash_flag,
)


@dataclass(frozen=True)
class PreparedTerminalCommand:
    shell: str
    command: str
    working_directory: str
    shell_mode: str | None
    argv: list[str]
    cwd: str | None


class TerminalJobManager:
    def __init__(self, settings: Settings, db: Database) -> None:
        self.settings = settings
        self.db = db
        self.terminal_tool = TerminalTool(settings)
        self._processes: dict[str, subprocess.Popen[str]] = {}
        self._lock = threading.Lock()
        self.db.mark_unattached_terminal_jobs_failed()

    def start(self, arguments: dict[str, Any]) -> TerminalJob:
        prepared = self._prepare(arguments)
        job = self.db.create_terminal_job(
            shell=prepared.shell,
            command=prepared.command,
            working_directory=prepared.working_directory,
            shell_mode=prepared.shell_mode,
        )
        try:
            process = subprocess.Popen(
                prepared.argv,
                cwd=prepared.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                creationflags=_creation_flags(),
            )
        except OSError as exc:
            message = f"Terminal job failed to start: {exc}"
            self.db.append_terminal_job_log(job.id, "system", message)
            return self.db.update_terminal_job(
                job.id,
                status="failed",
                error=message,
                completed_at=now_iso(),
            )

        with self._lock:
            self._processes[job.id] = process
        started_at = now_iso()
        self.db.update_terminal_job(
            job.id,
            status="running",
            pid=process.pid,
            started_at=started_at,
        )
        self.db.append_terminal_job_log(job.id, "system", f"Started pid {process.pid}")
        self._start_reader(job.id, "stdout", process.stdout)
        self._start_reader(job.id, "stderr", process.stderr)
        monitor = threading.Thread(target=self._monitor, args=(job.id, process), daemon=True)
        monitor.start()
        current = self.db.get_terminal_job(job.id)
        if current is None:
            raise ValueError(f"Terminal job not found after start: {job.id}")
        return current

    def cancel(self, job_id: str) -> TerminalJob:
        job = self.db.get_terminal_job(job_id)
        if job is None:
            raise ValueError(f"Terminal job not found: {job_id}")
        with self._lock:
            process = self._processes.get(job_id)
        if process is None or process.poll() is not None:
            return job
        self.db.update_terminal_job(job_id, status="cancelling")
        self.db.append_terminal_job_log(job_id, "system", "Cancellation requested")
        process.terminate()
        timer = threading.Timer(5.0, self._kill_if_running, args=(job_id, process))
        timer.daemon = True
        timer.start()
        current = self.db.get_terminal_job(job_id)
        if current is None:
            raise ValueError(f"Terminal job not found after cancel: {job_id}")
        return current

    def _prepare(self, arguments: dict[str, Any]) -> PreparedTerminalCommand:
        if not self.settings.terminal_enabled:
            raise ToolExecutionError("Terminal jobs are disabled")
        shell = arguments.get("shell", "powershell")
        command = arguments.get("command")
        working_directory = arguments.get("working_directory")
        if not isinstance(shell, str) or shell.lower() not in self.terminal_tool.allowed_shells:
            raise ToolExecutionError(f"Terminal shell is not allowed: {shell}")
        if not isinstance(command, str) or not command.strip():
            raise ToolExecutionError("A non-empty command is required")
        if not isinstance(working_directory, str) or not working_directory.strip():
            raise ToolExecutionError("A non-empty working_directory is required")
        normalized_shell = shell.lower()
        _block_dangerous_command(command, normalized_shell)
        if normalized_shell == "powershell":
            cwd_path = self.terminal_tool._allowed_windows_cwd(working_directory)
            return PreparedTerminalCommand(
                shell=normalized_shell,
                command=command,
                working_directory=str(cwd_path),
                shell_mode=None,
                argv=[
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                cwd=str(cwd_path),
            )
        if normalized_shell == "wsl":
            cwd = self.terminal_tool._allowed_wsl_cwd(working_directory)
            shell_mode = _normalize_wsl_shell_mode(
                arguments.get("shell_mode", self.terminal_tool.wsl_shell_mode)
            )
            return PreparedTerminalCommand(
                shell=normalized_shell,
                command=command,
                working_directory=cwd,
                shell_mode=shell_mode,
                argv=[
                    "wsl.exe",
                    "-d",
                    self.terminal_tool.wsl_distro,
                    "--cd",
                    cwd,
                    "--",
                    "bash",
                    _wsl_bash_flag(shell_mode),
                    command,
                ],
                cwd=None,
            )
        raise ToolExecutionError(f"Unsupported terminal shell: {shell}")

    def _start_reader(self, job_id: str, stream: str, pipe: IO[str] | None) -> None:
        if pipe is None:
            return
        reader = threading.Thread(target=self._read_pipe, args=(job_id, stream, pipe), daemon=True)
        reader.start()

    def _read_pipe(self, job_id: str, stream: str, pipe: IO[str]) -> None:
        try:
            for line in iter(pipe.readline, ""):
                if line:
                    self.db.append_terminal_job_log(job_id, stream, line.rstrip("\n"))
        finally:
            pipe.close()

    def _monitor(self, job_id: str, process: subprocess.Popen[str]) -> None:
        exit_code = process.wait()
        with self._lock:
            self._processes.pop(job_id, None)
        job = self.db.get_terminal_job(job_id)
        was_cancelling = job is not None and job.status == "cancelling"
        status = "cancelled" if was_cancelling else "completed" if exit_code == 0 else "failed"
        error = None if status in {"completed", "cancelled"} else f"Command exited with {exit_code}"
        self.db.append_terminal_job_log(job_id, "system", f"Exited with code {exit_code}")
        self.db.update_terminal_job(
            job_id,
            status=status,
            exit_code=exit_code,
            error=error,
            completed_at=now_iso(),
        )

    def _kill_if_running(self, job_id: str, process: subprocess.Popen[str]) -> None:
        if process.poll() is None:
            self.db.append_terminal_job_log(job_id, "system", "Process did not exit; killing")
            process.kill()


def _creation_flags() -> int:
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)

from __future__ import annotations

import ast
import ipaddress
import operator
import os
import posixpath
import re
import socket
import subprocess
import uuid
from collections.abc import Callable
from html.parser import HTMLParser
from pathlib import Path
from time import perf_counter, sleep
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

from mnemosyne_core.config import Settings
from mnemosyne_core.models import ToolSpec


class ToolExecutionError(Exception):
    pass


ToolHandler = Callable[[dict[str, Any]], dict[str, Any]]


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, tuple[ToolSpec, ToolHandler]] = {}

    @classmethod
    def safe_defaults(cls, settings: Settings) -> ToolRegistry:
        registry = cls()
        file_tools = FileTools(settings.allowed_file_roots)
        allowed_roots_description = file_tools.allowed_roots_description()
        http_tool = HttpGetTool(settings.http_timeout_seconds, settings.http_max_bytes)
        web_search_tool = WebSearchTool(settings.http_timeout_seconds)
        terminal_tool = TerminalTool(settings)
        registry.register(
            ToolSpec(
                name="read_text_file",
                description=(
                    "Read a UTF-8 text file under an allowed local root. "
                    f"Allowed roots: {allowed_roots_description}."
                ),
                input_schema={
                    "type": "object",
                    "required": ["path"],
                    "properties": {"path": {"type": "string"}},
                },
                permission_category="filesystem.read",
            ),
            file_tools.read_text_file,
        )
        registry.register(
            ToolSpec(
                name="list_directory",
                description=(
                    "List entries in a directory under an allowed local root. "
                    f"Allowed roots: {allowed_roots_description}."
                ),
                input_schema={
                    "type": "object",
                    "required": ["path"],
                    "properties": {"path": {"type": "string"}},
                },
                permission_category="filesystem.read",
            ),
            file_tools.list_directory,
        )
        registry.register(
            ToolSpec(
                name="write_text_file",
                description=(
                    "Write UTF-8 text to a file under an allowed local root. "
                    f"Allowed roots: {allowed_roots_description}."
                ),
                input_schema={
                    "type": "object",
                    "required": ["path", "text"],
                    "properties": {
                        "path": {"type": "string"},
                        "text": {"type": "string"},
                        "overwrite": {"type": "boolean", "default": True},
                    },
                },
                permission_category="filesystem.write",
            ),
            file_tools.write_text_file,
        )
        registry.register(
            ToolSpec(
                name="calculator",
                description="Evaluate deterministic arithmetic expressions.",
                input_schema={
                    "type": "object",
                    "required": ["expression"],
                    "properties": {"expression": {"type": "string"}},
                },
                permission_category="compute.safe",
            ),
            calculate,
        )
        registry.register(
            ToolSpec(
                name="http_get",
                description=(
                    "Fetch a public HTTP or HTTPS URL with local/private network targets blocked."
                ),
                input_schema={
                    "type": "object",
                    "required": ["url"],
                    "properties": {"url": {"type": "string"}},
                },
                permission_category="network.public_read",
            ),
            http_tool.execute,
        )
        registry.register(
            ToolSpec(
                name="web_search",
                description=(
                    "Search the public web and return result titles, URLs, and snippets. "
                    "Use for current information, repository discovery, and source finding."
                ),
                input_schema={
                    "type": "object",
                    "required": ["query"],
                    "properties": {
                        "query": {"type": "string"},
                        "max_results": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "default": 5,
                        },
                    },
                },
                permission_category="network.public_search",
            ),
            web_search_tool.execute,
        )
        if settings.terminal_enabled:
            registry.register(
                ToolSpec(
                    name="run_terminal_command",
                    description=(
                        "Run a controlled local terminal command in PowerShell or WSL. "
                        "Commands may modify files, but working directories must be under "
                        f"allowed Windows roots: {allowed_roots_description}; "
                        f"allowed WSL roots: {terminal_tool.allowed_wsl_roots_description()}."
                    ),
                    input_schema={
                        "type": "object",
                        "required": ["shell", "working_directory", "command"],
                        "properties": {
                            "shell": {
                                "type": "string",
                                "enum": terminal_tool.allowed_shells,
                            },
                            "working_directory": {"type": "string"},
                            "command": {"type": "string"},
                            "shell_mode": {
                                "type": "string",
                                "enum": ["login", "interactive", "login_interactive"],
                                "default": settings.wsl_shell_mode,
                                "description": (
                                    "WSL bash startup mode. Interactive mode loads "
                                    "~/.bashrc so user-local tools such as openclaw "
                                    "are available."
                                ),
                            },
                            "timeout_seconds": {
                                "type": "number",
                                "minimum": 1,
                                "maximum": settings.terminal_timeout_seconds,
                                "default": settings.terminal_timeout_seconds,
                            },
                        },
                    },
                    permission_category="terminal.modify",
                ),
                terminal_tool.execute,
            )
        if settings.elevated_powershell_enabled:
            elevated_powershell_tool = ElevatedPowerShellTool(settings)
            registry.register(
                ToolSpec(
                    name="run_elevated_powershell",
                    description=(
                        "Launch a Windows PowerShell .ps1 script through a UAC elevation prompt. "
                        "Scripts, working directories, and log files must stay under allowed "
                        f"Windows roots: {allowed_roots_description}."
                    ),
                    input_schema={
                        "type": "object",
                        "required": ["script_path", "working_directory"],
                        "properties": {
                            "script_path": {"type": "string"},
                            "working_directory": {"type": "string"},
                            "arguments": {
                                "type": "array",
                                "items": {
                                    "type": ["string", "number", "boolean"],
                                },
                                "default": [],
                            },
                            "timeout_seconds": {
                                "type": "number",
                                "minimum": 1,
                                "maximum": settings.elevated_powershell_timeout_seconds,
                                "default": settings.elevated_powershell_timeout_seconds,
                            },
                        },
                    },
                    permission_category="terminal.elevated",
                ),
                elevated_powershell_tool.execute,
            )
        if settings.elevated_wsl_enabled:
            elevated_wsl_tool = ElevatedWslTool(settings)
            registry.register(
                ToolSpec(
                    name="run_elevated_wsl_command",
                    description=(
                        "Launch a WSL command through a Windows UAC elevation prompt. "
                        f"Working directories must be under allowed WSL roots: "
                        f"{elevated_wsl_tool.allowed_wsl_roots_description()}. "
                        f"Logs must stay under allowed Windows roots: {allowed_roots_description}."
                    ),
                    input_schema={
                        "type": "object",
                        "required": ["working_directory", "command"],
                        "properties": {
                            "working_directory": {"type": "string"},
                            "command": {"type": "string"},
                            "distro": {
                                "type": "string",
                                "default": settings.wsl_distro,
                            },
                            "shell_mode": {
                                "type": "string",
                                "enum": ["login", "interactive", "login_interactive"],
                                "default": settings.wsl_shell_mode,
                                "description": (
                                    "WSL bash startup mode. Interactive mode loads "
                                    "~/.bashrc so user-local tools such as openclaw "
                                    "are available."
                                ),
                            },
                            "timeout_seconds": {
                                "type": "number",
                                "minimum": 1,
                                "maximum": settings.elevated_wsl_timeout_seconds,
                                "default": settings.elevated_wsl_timeout_seconds,
                            },
                        },
                    },
                    permission_category="terminal.elevated",
                ),
                elevated_wsl_tool.execute,
            )
        return registry

    def register(self, spec: ToolSpec, handler: ToolHandler) -> None:
        self._tools[spec.name] = (spec, handler)

    def specs(self, allowed_tools: list[str] | None = None) -> list[ToolSpec]:
        allowed = set(allowed_tools) if allowed_tools else None
        return [
            entry[0]
            for name, entry in self._tools.items()
            if allowed is None or name in allowed
        ]

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def execute(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self._tools:
            raise ToolExecutionError(f"Unknown tool: {name}")
        _spec, handler = self._tools[name]
        return handler(arguments)


class FileTools:
    def __init__(self, allowed_roots: list[str]) -> None:
        self.allowed_roots = [Path(root).resolve() for root in allowed_roots]

    def allowed_roots_description(self) -> str:
        return ", ".join(str(root) for root in self.allowed_roots)

    def read_text_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._allowed_path(arguments.get("path"))
        if not path.is_file():
            raise ToolExecutionError(f"Path is not a file: {path}")
        return {"path": str(path), "text": path.read_text(encoding="utf-8")}

    def list_directory(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._allowed_path(arguments.get("path"))
        if not path.is_dir():
            raise ToolExecutionError(f"Path is not a directory: {path}")
        entries = sorted(
            {"name": child.name, "kind": "directory" if child.is_dir() else "file"}
            for child in path.iterdir()
        )
        return {"path": str(path), "entries": entries}

    def write_text_file(self, arguments: dict[str, Any]) -> dict[str, Any]:
        path = self._allowed_path(arguments.get("path"))
        text = arguments.get("text")
        overwrite = arguments.get("overwrite", True)
        if not isinstance(text, str):
            raise ToolExecutionError("A text string is required")
        if not isinstance(overwrite, bool):
            raise ToolExecutionError("overwrite must be a boolean")
        if path.exists() and not path.is_file():
            raise ToolExecutionError(f"Path is not a file: {path}")
        if path.exists() and not overwrite:
            raise ToolExecutionError(f"Path already exists: {path}")
        created = not path.exists()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        return {
            "path": str(path),
            "bytes": len(text.encode("utf-8")),
            "created": created,
        }

    def _allowed_path(self, raw_path: Any) -> Path:
        if not isinstance(raw_path, str) or not raw_path:
            raise ToolExecutionError("A non-empty path is required")
        path = _normalize_local_path(raw_path).resolve()
        if not any(path == root or root in path.parents for root in self.allowed_roots):
            raise ToolExecutionError(f"Path is outside allowed roots: {path}")
        return path


def _normalize_local_path(raw_path: str) -> Path:
    normalized = raw_path.replace("\\", "/")
    parts = normalized.split("/")
    if os.name == "nt" and len(parts) >= 4 and parts[1] == "mnt" and len(parts[2]) == 1:
        drive = parts[2].upper()
        remainder = "/".join(parts[3:])
        return Path(f"{drive}:/{remainder}")
    return Path(raw_path)


class TerminalTool:
    def __init__(self, settings: Settings) -> None:
        self.allowed_windows_roots = [
            Path(root).resolve() for root in settings.allowed_file_roots
        ]
        self.allowed_shells = [shell.lower() for shell in settings.terminal_shells]
        self.default_timeout_seconds = settings.terminal_timeout_seconds
        self.max_output_bytes = settings.terminal_max_output_bytes
        self.wsl_distro = settings.wsl_distro
        self.wsl_allowed_roots = [
            _normalize_wsl_root(root) for root in settings.wsl_allowed_roots
        ]
        self.wsl_shell_mode = _normalize_wsl_shell_mode(settings.wsl_shell_mode)

    def allowed_wsl_roots_description(self) -> str:
        return ", ".join(self.wsl_allowed_roots) if self.wsl_allowed_roots else "none"

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        shell = arguments.get("shell", "powershell")
        command = arguments.get("command")
        working_directory = arguments.get("working_directory")
        if not isinstance(shell, str) or shell.lower() not in self.allowed_shells:
            raise ToolExecutionError(f"Terminal shell is not allowed: {shell}")
        if not isinstance(command, str) or not command.strip():
            raise ToolExecutionError("A non-empty command is required")
        if not isinstance(working_directory, str) or not working_directory.strip():
            raise ToolExecutionError("A non-empty working_directory is required")
        timeout_seconds = self._timeout(arguments.get("timeout_seconds"))
        normalized_shell = shell.lower()
        _block_dangerous_command(command, normalized_shell)
        started = perf_counter()
        if normalized_shell == "powershell":
            cwd = self._allowed_windows_cwd(working_directory)
            completed = self._run(
                [
                    "powershell.exe",
                    "-NoProfile",
                    "-NonInteractive",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    command,
                ],
                cwd=str(cwd),
                timeout_seconds=timeout_seconds,
            )
            resolved_cwd = str(cwd)
        elif normalized_shell == "wsl":
            cwd = self._allowed_wsl_cwd(working_directory)
            shell_mode = _normalize_wsl_shell_mode(
                arguments.get("shell_mode", self.wsl_shell_mode)
            )
            completed = self._run(
                [
                    "wsl.exe",
                    "-d",
                    self.wsl_distro,
                    "--cd",
                    cwd,
                    "--",
                    "bash",
                    _wsl_bash_flag(shell_mode),
                    command,
                ],
                cwd=None,
                timeout_seconds=timeout_seconds,
            )
            resolved_cwd = cwd
        else:
            raise ToolExecutionError(f"Unsupported terminal shell: {shell}")
        duration_ms = int((perf_counter() - started) * 1000)
        stdout, stdout_truncated = _truncate_text(completed.stdout, self.max_output_bytes)
        stderr, stderr_truncated = _truncate_text(completed.stderr, self.max_output_bytes)
        return {
            "shell": normalized_shell,
            "command": command,
            "working_directory": resolved_cwd,
            "shell_mode": shell_mode if normalized_shell == "wsl" else None,
            "exit_code": completed.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
            "duration_ms": duration_ms,
            "risk": "modifies_files",
        }

    def _timeout(self, raw_timeout: Any) -> float:
        if raw_timeout is None:
            return self.default_timeout_seconds
        if not isinstance(raw_timeout, int | float):
            raise ToolExecutionError("timeout_seconds must be a number")
        timeout = float(raw_timeout)
        if timeout <= 0:
            raise ToolExecutionError("timeout_seconds must be positive")
        return min(timeout, self.default_timeout_seconds)

    def _allowed_windows_cwd(self, raw_path: str) -> Path:
        path = _normalize_local_path(raw_path).resolve()
        if not any(path == root or root in path.parents for root in self.allowed_windows_roots):
            raise ToolExecutionError(f"Working directory is outside allowed roots: {path}")
        if not path.is_dir():
            raise ToolExecutionError(f"Working directory is not a directory: {path}")
        return path

    def _allowed_wsl_cwd(self, raw_path: str) -> str:
        cwd = _normalize_wsl_root(raw_path)
        if not self.wsl_allowed_roots:
            raise ToolExecutionError("No WSL roots are configured")
        allowed = any(
            cwd == root or cwd.startswith(f"{root.rstrip('/')}/")
            for root in self.wsl_allowed_roots
        )
        if not allowed:
            raise ToolExecutionError(f"Working directory is outside allowed WSL roots: {cwd}")
        return cwd

    @staticmethod
    def _run(
        command: list[str],
        cwd: str | None,
        timeout_seconds: float,
    ) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                command,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            message = f"Terminal command timed out after {timeout_seconds:g}s"
            raise ToolExecutionError(message) from exc
        except OSError as exc:
            raise ToolExecutionError(f"Terminal command failed to start: {exc}") from exc


class ElevatedPowerShellTool:
    def __init__(self, settings: Settings) -> None:
        self.allowed_windows_roots = [
            Path(root).resolve() for root in settings.allowed_file_roots
        ]
        self.default_timeout_seconds = settings.elevated_powershell_timeout_seconds
        self.log_dir = self._allowed_path(
            settings.elevated_powershell_log_dir,
            label="Log directory",
            must_exist=False,
        )

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        script_path = self._allowed_path(arguments.get("script_path"), label="Script")
        if script_path.suffix.lower() != ".ps1":
            raise ToolExecutionError("Elevated PowerShell script must be a .ps1 file")
        if not script_path.is_file():
            raise ToolExecutionError(f"Script is not a file: {script_path}")
        working_directory = self._allowed_path(
            arguments.get("working_directory"),
            label="Working directory",
        )
        if not working_directory.is_dir():
            raise ToolExecutionError(
                f"Working directory is not a directory: {working_directory}"
            )
        tool_arguments = self._arguments(arguments.get("arguments", []))
        timeout_seconds = self._timeout(arguments.get("timeout_seconds"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        invocation_id = uuid.uuid4().hex
        wrapper_path = self.log_dir / f"elevated-{invocation_id}.ps1"
        stdout_log = self.log_dir / f"elevated-{invocation_id}.stdout.log"
        stderr_log = self.log_dir / f"elevated-{invocation_id}.stderr.log"
        exit_code_log = self.log_dir / f"elevated-{invocation_id}.exitcode.txt"
        wrapper_path.write_text(
            self._wrapper_script(
                script_path=script_path,
                working_directory=working_directory,
                arguments=tool_arguments,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
                exit_code_log=exit_code_log,
            ),
            encoding="utf-8",
            newline="\n",
        )
        launcher_arguments = _ps_array(
            ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper_path)]
        )
        launch_command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "Start-Process powershell.exe "
                f"-Verb RunAs -WorkingDirectory {_ps_quote(str(working_directory))} "
                f"-ArgumentList {launcher_arguments}"
            ),
        ]
        started = perf_counter()
        try:
            completed = subprocess.run(
                launch_command,
                cwd=str(working_directory),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            message = f"Elevated PowerShell launch timed out after {timeout_seconds:g}s"
            raise ToolExecutionError(message) from exc
        except OSError as exc:
            raise ToolExecutionError(
                f"Elevated PowerShell launch failed to start: {exc}"
            ) from exc
        duration_ms = int((perf_counter() - started) * 1000)
        return {
            "script_path": str(script_path),
            "working_directory": str(working_directory),
            "arguments": tool_arguments,
            "wrapper_path": str(wrapper_path),
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
            "exit_code_log": str(exit_code_log),
            "uac_started": completed.returncode == 0,
            "launcher_exit_code": completed.returncode,
            "launcher_stdout": completed.stdout,
            "launcher_stderr": completed.stderr,
            "duration_ms": duration_ms,
            "risk": "requires_admin_approval",
        }

    def _allowed_path(
        self,
        raw_path: Any,
        *,
        label: str,
        must_exist: bool = True,
    ) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolExecutionError(f"{label} path is required")
        path = _normalize_local_path(raw_path)
        if not path.is_absolute():
            path = self.allowed_windows_roots[0] / path
        path = path.resolve()
        if not any(path == root or root in path.parents for root in self.allowed_windows_roots):
            raise ToolExecutionError(f"{label} is outside allowed roots: {path}")
        if must_exist and not path.exists():
            raise ToolExecutionError(f"{label} does not exist: {path}")
        return path

    def _timeout(self, raw_timeout: Any) -> float:
        if raw_timeout is None:
            return self.default_timeout_seconds
        if not isinstance(raw_timeout, int | float):
            raise ToolExecutionError("timeout_seconds must be a number")
        timeout = float(raw_timeout)
        if timeout <= 0:
            raise ToolExecutionError("timeout_seconds must be positive")
        return min(timeout, self.default_timeout_seconds)

    @staticmethod
    def _arguments(raw_arguments: Any) -> list[str]:
        if raw_arguments is None:
            return []
        if not isinstance(raw_arguments, list):
            raise ToolExecutionError("arguments must be a list")
        values: list[str] = []
        for value in raw_arguments:
            if not isinstance(value, str | int | float | bool):
                raise ToolExecutionError("arguments must contain strings, numbers, or booleans")
            values.append(str(value))
        return values

    @staticmethod
    def _wrapper_script(
        *,
        script_path: Path,
        working_directory: Path,
        arguments: list[str],
        stdout_log: Path,
        stderr_log: Path,
        exit_code_log: Path,
    ) -> str:
        ps_arguments = ", ".join(_ps_quote(argument) for argument in arguments)
        return "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"$stdoutLog = {_ps_quote(str(stdout_log))}",
                f"$stderrLog = {_ps_quote(str(stderr_log))}",
                f"$exitCodeLog = {_ps_quote(str(exit_code_log))}",
                f"Set-Location -LiteralPath {_ps_quote(str(working_directory))}",
                f"$mnemosyneArgs = @({ps_arguments})",
                "try {",
                (
                    f"  & {_ps_quote(str(script_path))} @mnemosyneArgs "
                    "1> $stdoutLog 2> $stderrLog"
                ),
                "  $code = $LASTEXITCODE",
                "  if ($null -eq $code) { $code = 0 }",
                "} catch {",
                "  $_ | Out-String | Set-Content -LiteralPath $stderrLog -Encoding UTF8",
                "  $code = 1",
                "}",
                "$code | Set-Content -LiteralPath $exitCodeLog -Encoding UTF8",
                "exit $code",
                "",
            ]
        )


class ElevatedWslTool:
    def __init__(self, settings: Settings) -> None:
        self.allowed_windows_roots = [
            Path(root).resolve() for root in settings.allowed_file_roots
        ]
        self.wsl_distro = settings.wsl_distro
        self.wsl_allowed_roots = [
            _normalize_wsl_root(root) for root in settings.wsl_allowed_roots
        ]
        self.wsl_shell_mode = _normalize_wsl_shell_mode(settings.wsl_shell_mode)
        self.default_timeout_seconds = settings.elevated_wsl_timeout_seconds
        self.max_output_bytes = settings.terminal_max_output_bytes
        self.log_dir = self._allowed_windows_path(
            settings.elevated_wsl_log_dir,
            label="Log directory",
            must_exist=False,
        )

    def allowed_wsl_roots_description(self) -> str:
        return ", ".join(self.wsl_allowed_roots) if self.wsl_allowed_roots else "none"

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise ToolExecutionError("A non-empty command is required")
        working_directory = self._allowed_wsl_cwd(arguments.get("working_directory"))
        distro = arguments.get("distro", self.wsl_distro)
        if not isinstance(distro, str) or not distro.strip():
            raise ToolExecutionError("A non-empty distro is required")
        shell_mode = _normalize_wsl_shell_mode(
            arguments.get("shell_mode", self.wsl_shell_mode)
        )
        _block_dangerous_command(command, "wsl")
        timeout_seconds = self._timeout(arguments.get("timeout_seconds"))
        self.log_dir.mkdir(parents=True, exist_ok=True)
        invocation_id = uuid.uuid4().hex
        wrapper_path = self.log_dir / f"elevated-wsl-{invocation_id}.ps1"
        stdout_log = self.log_dir / f"elevated-wsl-{invocation_id}.stdout.log"
        stderr_log = self.log_dir / f"elevated-wsl-{invocation_id}.stderr.log"
        exit_code_log = self.log_dir / f"elevated-wsl-{invocation_id}.exitcode.txt"
        wrapper_path.write_text(
            self._wrapper_script(
                distro=distro,
                working_directory=working_directory,
                command=command,
                shell_mode=shell_mode,
                stdout_log=stdout_log,
                stderr_log=stderr_log,
                exit_code_log=exit_code_log,
            ),
            encoding="utf-8",
            newline="\n",
        )
        launcher_arguments = _ps_array(
            ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(wrapper_path)]
        )
        launch_command = [
            "powershell.exe",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            (
                "Start-Process powershell.exe "
                f"-Verb RunAs -WorkingDirectory {_ps_quote(str(self.log_dir))} "
                f"-ArgumentList {launcher_arguments}"
            ),
        ]
        started = perf_counter()
        try:
            completed = subprocess.run(
                launch_command,
                cwd=str(self.log_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            message = f"Elevated WSL launch timed out after {timeout_seconds:g}s"
            raise ToolExecutionError(message) from exc
        except OSError as exc:
            raise ToolExecutionError(f"Elevated WSL launch failed to start: {exc}") from exc
        log_result = _collect_elevated_log_result(
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            exit_code_log=exit_code_log,
            wait_seconds=timeout_seconds,
            max_bytes=self.max_output_bytes,
        )
        duration_ms = int((perf_counter() - started) * 1000)
        return {
            "distro": distro,
            "working_directory": working_directory,
            "command": command,
            "shell_mode": shell_mode,
            "wrapper_path": str(wrapper_path),
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
            "exit_code_log": str(exit_code_log),
            "uac_started": completed.returncode == 0,
            "launcher_exit_code": completed.returncode,
            "launcher_stdout": completed.stdout,
            "launcher_stderr": completed.stderr,
            **log_result,
            "duration_ms": duration_ms,
            "risk": "requires_admin_approval",
        }

    def _allowed_windows_path(
        self,
        raw_path: Any,
        *,
        label: str,
        must_exist: bool = True,
    ) -> Path:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolExecutionError(f"{label} path is required")
        path = _normalize_local_path(raw_path)
        if not path.is_absolute():
            path = self.allowed_windows_roots[0] / path
        path = path.resolve()
        if not any(path == root or root in path.parents for root in self.allowed_windows_roots):
            raise ToolExecutionError(f"{label} is outside allowed roots: {path}")
        if must_exist and not path.exists():
            raise ToolExecutionError(f"{label} does not exist: {path}")
        return path

    def _allowed_wsl_cwd(self, raw_path: Any) -> str:
        if not isinstance(raw_path, str) or not raw_path.strip():
            raise ToolExecutionError("A non-empty working_directory is required")
        cwd = _normalize_wsl_root(raw_path)
        if not self.wsl_allowed_roots:
            raise ToolExecutionError("No WSL roots are configured")
        allowed = any(
            cwd == root or cwd.startswith(f"{root.rstrip('/')}/")
            for root in self.wsl_allowed_roots
        )
        if not allowed:
            raise ToolExecutionError(f"Working directory is outside allowed WSL roots: {cwd}")
        return cwd

    def _timeout(self, raw_timeout: Any) -> float:
        if raw_timeout is None:
            return self.default_timeout_seconds
        if not isinstance(raw_timeout, int | float):
            raise ToolExecutionError("timeout_seconds must be a number")
        timeout = float(raw_timeout)
        if timeout <= 0:
            raise ToolExecutionError("timeout_seconds must be positive")
        return min(timeout, self.default_timeout_seconds)

    @staticmethod
    def _wrapper_script(
        *,
        distro: str,
        working_directory: str,
        command: str,
        shell_mode: str,
        stdout_log: Path,
        stderr_log: Path,
        exit_code_log: Path,
    ) -> str:
        wsl_args = _ps_array(
            [
                "-d",
                distro,
                "--cd",
                working_directory,
                "--",
                "bash",
                _wsl_bash_flag(shell_mode),
                command,
            ]
        )
        return "\n".join(
            [
                "$ErrorActionPreference = 'Stop'",
                f"$stdoutLog = {_ps_quote(str(stdout_log))}",
                f"$stderrLog = {_ps_quote(str(stderr_log))}",
                f"$exitCodeLog = {_ps_quote(str(exit_code_log))}",
                f"$wslArgs = {wsl_args}",
                "try {",
                "  & wsl.exe @wslArgs 1> $stdoutLog 2> $stderrLog",
                "  $code = $LASTEXITCODE",
                "  if ($null -eq $code) { $code = 0 }",
                "} catch {",
                "  $_ | Out-String | Set-Content -LiteralPath $stderrLog -Encoding UTF8",
                "  $code = 1",
                "}",
                "$code | Set-Content -LiteralPath $exitCodeLog -Encoding UTF8",
                "exit $code",
                "",
            ]
        )


def _normalize_wsl_root(raw_path: str) -> str:
    normalized = raw_path.replace("\\", "/")
    return posixpath.normpath(normalized if normalized.startswith("/") else f"/{normalized}")


def _normalize_wsl_shell_mode(raw_mode: Any) -> str:
    if raw_mode is None:
        return "interactive"
    if not isinstance(raw_mode, str) or not raw_mode.strip():
        raise ToolExecutionError("WSL shell_mode must be a non-empty string")
    normalized = raw_mode.strip().lower().replace("-", "_")
    aliases = {
        "login": "login",
        "noninteractive": "login",
        "non_interactive": "login",
        "interactive": "interactive",
        "login_interactive": "login_interactive",
        "interactive_login": "login_interactive",
    }
    try:
        return aliases[normalized]
    except KeyError as exc:
        raise ToolExecutionError(
            "WSL shell_mode must be one of: login, interactive, login_interactive"
        ) from exc


def _wsl_bash_flag(shell_mode: str) -> str:
    if shell_mode == "login":
        return "-lc"
    if shell_mode == "interactive":
        return "-ic"
    if shell_mode == "login_interactive":
        return "-lic"
    raise ToolExecutionError(
        "WSL shell_mode must be one of: login, interactive, login_interactive"
    )


def _block_dangerous_command(command: str, shell: str) -> None:
    normalized = " ".join(command.lower().split())
    blocked_patterns = [
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\s+-[^\s]*f",
        r"\bformat\b",
        r"\bdiskpart\b",
        r"\bbcdedit\b",
        r"\bshutdown\b",
        r"\brestart-computer\b",
        r"\bstop-computer\b",
        r"\bsudo\b",
        r"\bsu\s+-",
        r"\bchmod\s+-r\s+777\s+/",
        r"\bchown\s+-r\b",
        r"\brm\s+-[^\n;|&]*r[^\n;|&]*\s+/",
        r"\bremove-item\b[^\n;|&]*-recurse\b[^\n;|&]*(c:\\|f:\\|/)",
    ]
    if shell == "wsl":
        blocked_patterns.extend(
            [
                r"\bdd\s+.*\bof=/dev/",
                r"\bmkfs\b",
                r"\bmount\b",
                r"\bumount\b",
            ]
        )
    if any(re.search(pattern, normalized) for pattern in blocked_patterns):
        raise ToolExecutionError("Terminal command is blocked by safety policy")


def _collect_elevated_log_result(
    *,
    stdout_log: Path,
    stderr_log: Path,
    exit_code_log: Path,
    wait_seconds: float,
    max_bytes: int,
) -> dict[str, Any]:
    deadline = perf_counter() + wait_seconds
    while not exit_code_log.exists() and perf_counter() < deadline:
        sleep(0.1)
    completed = exit_code_log.exists()
    stdout, stdout_truncated = _read_optional_truncated_file(stdout_log, max_bytes)
    stderr, stderr_truncated = _read_optional_truncated_file(stderr_log, max_bytes)
    exit_code = None
    if completed:
        raw_exit_code = exit_code_log.read_text(encoding="utf-8", errors="replace").strip()
        try:
            exit_code = int(raw_exit_code)
        except ValueError:
            exit_code = None
    return {
        "completed": completed,
        "exit_code": exit_code,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
    }


def _read_optional_truncated_file(path: Path, max_bytes: int) -> tuple[str, bool]:
    if not path.exists():
        return "", False
    return _truncate_text(path.read_text(encoding="utf-8", errors="replace"), max_bytes)


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _ps_array(values: list[str]) -> str:
    return "@(" + ", ".join(_ps_quote(value) for value in values) + ")"


def _truncate_text(value: str, max_bytes: int) -> tuple[str, bool]:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


_OPERATORS: dict[type[ast.operator | ast.unaryop], Callable[..., float]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def calculate(arguments: dict[str, Any]) -> dict[str, Any]:
    expression = arguments.get("expression")
    if not isinstance(expression, str) or not expression.strip():
        raise ToolExecutionError("A non-empty expression is required")
    try:
        tree = ast.parse(expression, mode="eval")
        value = _eval_arithmetic(tree.body)
    except ToolExecutionError:
        raise
    except Exception as exc:
        raise ToolExecutionError("Only arithmetic expressions are supported") from exc
    return {"result": value}


def _eval_arithmetic(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_eval_arithmetic(node.left), _eval_arithmetic(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _OPERATORS:
        return _OPERATORS[type(node.op)](_eval_arithmetic(node.operand))
    raise ToolExecutionError("Only arithmetic expressions are supported")


class HttpGetTool:
    def __init__(self, timeout_seconds: float, max_bytes: int) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_bytes = max_bytes

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        url = arguments.get("url")
        if not isinstance(url, str) or not url:
            raise ToolExecutionError("A non-empty url is required")
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ToolExecutionError("Only http and https URLs are supported")
        self._block_private_targets(parsed.hostname)
        started = perf_counter()
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
                response = client.get(url)
                content = response.content
        except httpx.HTTPError as exc:
            raise ToolExecutionError(f"HTTP request failed: {exc}") from exc
        if len(content) > self.max_bytes:
            raise ToolExecutionError(f"HTTP response exceeded {self.max_bytes} bytes")
        elapsed_ms = int((perf_counter() - started) * 1000)
        return {
            "url": url,
            "status_code": response.status_code,
            "elapsed_ms": elapsed_ms,
            "text": content.decode(response.encoding or "utf-8", errors="replace"),
        }

    @staticmethod
    def _block_private_targets(hostname: str) -> None:
        addresses: set[str] = set()
        try:
            addresses.add(str(ipaddress.ip_address(hostname)))
        except ValueError:
            try:
                for family, _type, _proto, _canon, sockaddr in socket.getaddrinfo(hostname, None):
                    if family in {socket.AF_INET, socket.AF_INET6}:
                        addresses.add(sockaddr[0])
            except socket.gaierror as exc:
                raise ToolExecutionError(f"Could not resolve host: {hostname}") from exc
        for address in addresses:
            ip = ipaddress.ip_address(address)
            if (
                ip.is_private
                or ip.is_loopback
                or ip.is_link_local
                or ip.is_multicast
                or ip.is_unspecified
                or ip.is_reserved
            ):
                raise ToolExecutionError("HTTP tool blocks private or local network targets")


class WebSearchTool:
    search_url = "https://html.duckduckgo.com/html/"

    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds

    def execute(self, arguments: dict[str, Any]) -> dict[str, Any]:
        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ToolExecutionError("A non-empty query is required")
        max_results = arguments.get("max_results", 5)
        if not isinstance(max_results, int):
            raise ToolExecutionError("max_results must be an integer")
        max_results = max(1, min(max_results, 10))
        try:
            with httpx.Client(
                timeout=self.timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": "MnemosyneCore/0.1"},
            ) as client:
                response = client.get(self.search_url, params={"q": query})
                response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ToolExecutionError(f"Web search failed: {exc}") from exc
        results = parse_search_results(response.text, limit=max_results)
        return {"query": query, "results": results}


class _SearchResultParser(HTMLParser):
    def __init__(self, limit: int) -> None:
        super().__init__()
        self.limit = limit
        self.results: list[dict[str, str]] = []
        self._current_link: dict[str, str] | None = None
        self._capture_title = False
        self._capture_snippet = False
        self._text_chunks: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = {key: value or "" for key, value in attrs}
        classes = set(attrs_dict.get("class", "").split())
        if tag == "a" and "result__a" in classes and len(self.results) < self.limit:
            href = attrs_dict.get("href", "")
            self._current_link = {"title": "", "url": _normalize_search_url(href), "snippet": ""}
            self._capture_title = True
            self._text_chunks = []
        elif "result__snippet" in classes and self.results:
            self._capture_snippet = True
            self._text_chunks = []

    def handle_data(self, data: str) -> None:
        if self._capture_title or self._capture_snippet:
            self._text_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._capture_title and self._current_link is not None:
            self._current_link["title"] = _clean_text(" ".join(self._text_chunks))
            if self._current_link["title"] and self._current_link["url"]:
                self.results.append(self._current_link)
            self._current_link = None
            self._capture_title = False
            self._text_chunks = []
        elif self._capture_snippet:
            snippet = _clean_text(" ".join(self._text_chunks))
            if snippet and self.results:
                self.results[-1]["snippet"] = snippet
            self._capture_snippet = False
            self._text_chunks = []


def parse_search_results(html: str, limit: int = 5) -> list[dict[str, str]]:
    parser = _SearchResultParser(limit=limit)
    parser.feed(html)
    return parser.results[:limit]


def _normalize_search_url(href: str) -> str:
    if not href:
        return ""
    if href.startswith("//"):
        href = f"https:{href}"
    parsed = urlparse(href)
    query = parse_qs(parsed.query)
    if "uddg" in query and query["uddg"]:
        return unquote(query["uddg"][0])
    return href


def _clean_text(value: str) -> str:
    return " ".join(value.split())

import subprocess
from pathlib import Path

import pytest

from mnemosyne_core.config import Settings
from mnemosyne_core.tools import ToolExecutionError, ToolRegistry, parse_search_results


@pytest.fixture()
def registry(tmp_path: Path) -> ToolRegistry:
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    (allowed / "note.txt").write_text("remember the battery summary", encoding="utf-8")
    return ToolRegistry.safe_defaults(
        Settings(
            database_path=str(tmp_path / "mnemosyne.db"),
            allowed_file_roots=[str(allowed)],
            terminal_enabled=True,
            terminal_shells=["powershell", "wsl"],
            terminal_max_output_bytes=2048,
            wsl_allowed_roots=["/mnt/f"],
            http_timeout_seconds=1.0,
            http_max_bytes=2048,
        )
    )


def test_registry_rejects_unknown_tool(registry: ToolRegistry) -> None:
    with pytest.raises(ToolExecutionError, match="Unknown tool"):
        registry.execute("shell", {"command": "whoami"})


def test_file_tool_reads_inside_allowed_root(registry: ToolRegistry, tmp_path: Path) -> None:
    result = registry.execute("read_text_file", {"path": str(tmp_path / "allowed" / "note.txt")})

    assert result["text"] == "remember the battery summary"


def test_file_tool_blocks_outside_allowed_root(registry: ToolRegistry, tmp_path: Path) -> None:
    outside = tmp_path / "secret.txt"
    outside.write_text("nope", encoding="utf-8")

    with pytest.raises(ToolExecutionError, match="outside allowed roots"):
        registry.execute("read_text_file", {"path": str(outside)})


def test_file_tool_writes_text_inside_allowed_root(registry: ToolRegistry, tmp_path: Path) -> None:
    target = tmp_path / "allowed" / "draft.md"

    result = registry.execute(
        "write_text_file",
        {"path": str(target), "text": "# Draft\nRemember safe writes.\n"},
    )

    assert result["path"] == str(target.resolve())
    assert result["bytes"] == len(b"# Draft\nRemember safe writes.\n")
    assert result["created"] is True
    assert target.stat().st_size == result["bytes"]
    assert target.read_text(encoding="utf-8") == "# Draft\nRemember safe writes.\n"


def test_file_tool_creates_parent_directories_inside_allowed_root(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    target = tmp_path / "allowed" / "notes" / "research.md"

    registry.execute("write_text_file", {"path": str(target), "text": "parent folders are ok"})

    assert target.read_text(encoding="utf-8") == "parent folders are ok"


def test_file_tool_blocks_writes_outside_allowed_root(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    outside = tmp_path / "secret.txt"

    with pytest.raises(ToolExecutionError, match="outside allowed roots"):
        registry.execute("write_text_file", {"path": str(outside), "text": "nope"})

    assert not outside.exists()


def test_file_tool_rejects_non_text_writes(registry: ToolRegistry, tmp_path: Path) -> None:
    target = tmp_path / "allowed" / "draft.md"

    with pytest.raises(ToolExecutionError, match="text string"):
        registry.execute("write_text_file", {"path": str(target), "text": ["not", "text"]})


def test_file_tool_accepts_wsl_drive_paths_inside_allowed_root(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    target = (tmp_path / "allowed" / "wsl-note.md").resolve()
    if not target.drive:
        pytest.skip("WSL drive path translation is only meaningful on Windows")
    drive_root = Path(f"{target.drive}\\")
    wsl_path = f"/mnt/{target.drive[0].lower()}/{target.relative_to(drive_root).as_posix()}"

    registry.execute("write_text_file", {"path": wsl_path, "text": "translated safely"})

    assert target.read_text(encoding="utf-8") == "translated safely"


def test_calculator_accepts_basic_arithmetic(registry: ToolRegistry) -> None:
    result = registry.execute("calculator", {"expression": "(2 + 3) * 4"})

    assert result == {"result": 20}


def test_calculator_rejects_code_execution(registry: ToolRegistry) -> None:
    with pytest.raises(ToolExecutionError, match="Only arithmetic"):
        registry.execute("calculator", {"expression": "__import__('os').system('whoami')"})


def test_http_tool_blocks_private_network_targets(registry: ToolRegistry) -> None:
    with pytest.raises(ToolExecutionError, match="private or local"):
        registry.execute("http_get", {"url": "http://127.0.0.1:8000/health"})


def test_registry_includes_web_search_and_write_file_tools(registry: ToolRegistry) -> None:
    assert {"web_search", "write_text_file"} <= {spec.name for spec in registry.specs()}


def test_registry_includes_terminal_tool_when_enabled(registry: ToolRegistry) -> None:
    terminal = next(spec for spec in registry.specs() if spec.name == "run_terminal_command")

    assert terminal.permission_category == "terminal.modify"
    assert terminal.input_schema["properties"]["shell"]["enum"] == ["powershell", "wsl"]


def test_file_tool_specs_expose_allowed_roots(registry: ToolRegistry, tmp_path: Path) -> None:
    specs = {spec.name: spec for spec in registry.specs()}
    allowed_root = str((tmp_path / "allowed").resolve())

    assert allowed_root in specs["read_text_file"].description
    assert allowed_root in specs["list_directory"].description
    assert allowed_root in specs["write_text_file"].description


def test_terminal_tool_modifies_files_inside_allowed_root(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    target = tmp_path / "allowed" / "terminal-note.txt"

    result = registry.execute(
        "run_terminal_command",
        {
            "shell": "powershell",
            "working_directory": str(tmp_path / "allowed"),
            "command": f"Set-Content -LiteralPath '{target}' -Value 'created from terminal'",
        },
    )

    assert result["exit_code"] == 0
    assert result["shell"] == "powershell"
    assert result["risk"] == "modifies_files"
    assert target.read_text(encoding="utf-8").strip() == "created from terminal"


def test_terminal_tool_blocks_working_directory_outside_allowed_roots(
    registry: ToolRegistry, tmp_path: Path
) -> None:
    with pytest.raises(ToolExecutionError, match="outside allowed roots"):
        registry.execute(
            "run_terminal_command",
            {
                "shell": "powershell",
                "working_directory": str(tmp_path),
                "command": "Get-ChildItem",
            },
        )


def test_terminal_tool_blocks_dangerous_commands(registry: ToolRegistry, tmp_path: Path) -> None:
    with pytest.raises(ToolExecutionError, match="blocked"):
        registry.execute(
            "run_terminal_command",
            {
                "shell": "powershell",
                "working_directory": str(tmp_path / "allowed"),
                "command": "Remove-Item -Recurse C:\\",
            },
        )


def test_terminal_tool_truncates_large_output(registry: ToolRegistry, tmp_path: Path) -> None:
    result = registry.execute(
        "run_terminal_command",
        {
            "shell": "powershell",
            "working_directory": str(tmp_path / "allowed"),
            "command": "'x' * 3000",
            "timeout_seconds": 5,
        },
    )

    assert result["exit_code"] == 0
    assert result["stdout_truncated"] is True
    assert len(result["stdout"]) <= 2048


def test_terminal_tool_validates_wsl_working_directory(registry: ToolRegistry) -> None:
    with pytest.raises(ToolExecutionError, match="outside allowed WSL roots"):
        registry.execute(
            "run_terminal_command",
            {
                "shell": "wsl",
                "working_directory": "/etc",
                "command": "pwd",
            },
        )


def test_terminal_tool_runs_wsl_with_configured_distro(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout="/mnt/f\n", stderr="")

    monkeypatch.setattr("mnemosyne_core.tools.subprocess.run", fake_run)
    registry = ToolRegistry.safe_defaults(
        Settings(
            database_path=str(tmp_path / "mnemosyne.db"),
            allowed_file_roots=[str(tmp_path)],
            terminal_enabled=True,
            terminal_shells=["wsl"],
            wsl_distro="Ubuntu",
            wsl_allowed_roots=["/mnt/f"],
        )
    )

    result = registry.execute(
        "run_terminal_command",
        {"shell": "wsl", "working_directory": "/mnt/f", "command": "pwd"},
    )

    assert captured["command"] == [
        "wsl.exe",
        "-d",
        "Ubuntu",
        "--cd",
        "/mnt/f",
        "--",
        "bash",
        "-lc",
        "pwd",
    ]
    assert captured["kwargs"]["cwd"] is None
    assert result["stdout"] == "/mnt/f\n"


def test_web_search_parses_result_titles_urls_and_snippets() -> None:
    html = """
    <html><body>
      <a class="result__a" href="https://example.com/alpha">Alpha Result</a>
      <a class="result__snippet">Alpha snippet about agent memory.</a>
      <a class="result__a" href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.org%2Fbeta">
        Beta Result
      </a>
      <a class="result__snippet">Beta snippet about web search.</a>
    </body></html>
    """

    results = parse_search_results(html, limit=5)

    assert results == [
        {
            "title": "Alpha Result",
            "url": "https://example.com/alpha",
            "snippet": "Alpha snippet about agent memory.",
        },
        {
            "title": "Beta Result",
            "url": "https://example.org/beta",
            "snippet": "Beta snippet about web search.",
        },
    ]

from pathlib import Path
from types import SimpleNamespace

import pytest

from mnemosyne_core.config import Settings
from mnemosyne_core.model_client import LiteLLMModelClient, ModelRequest, _extract_tool_calls
from mnemosyne_core.tools import ToolRegistry


@pytest.mark.asyncio
async def test_model_prompt_guides_filesystem_tools_to_allowed_roots(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict = {}

    async def fake_completion(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Done", tool_calls=[]))]
        )

    monkeypatch.setattr("mnemosyne_core.model_client.acompletion", fake_completion)
    settings = Settings(
        database_path=str(tmp_path / "mnemosyne.db"),
        allowed_file_roots=[str(tmp_path)],
        litellm_model="test-model",
    )
    registry = ToolRegistry.safe_defaults(settings)
    client = LiteLLMModelClient(settings)

    await client.complete(
        ModelRequest(goal="write a local note", memories=[], tools=registry.specs())
    )

    messages = captured["messages"]
    combined_prompt = "\n".join(message["content"] for message in messages)
    assert str(tmp_path.resolve()) in combined_prompt
    assert "drive-letter paths" in combined_prompt
    assert "not /mnt/" in combined_prompt


def test_extract_tool_calls_accepts_xml_tool_call_blocks() -> None:
    calls = _extract_tool_calls(
        """
        <tool_call tool_name="write_text_file">
          <arg name="path">/mnt/f/myagent_data.md</arg>
          <arg name="text"># Report

Body text.</arg>
          <arg name="overwrite">true</arg>
        </tool_call>
        """
    )

    assert len(calls) == 1
    assert calls[0].name == "write_text_file"
    assert calls[0].arguments == {
        "path": "/mnt/f/myagent_data.md",
        "text": "# Report\n\nBody text.",
        "overwrite": True,
    }

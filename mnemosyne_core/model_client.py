from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field
from typing import Any, Protocol

from litellm import acompletion

from mnemosyne_core.config import Settings
from mnemosyne_core.models import MemoryRecord, SkillRecord, ToolSpec


@dataclass(frozen=True)
class ToolCallRequest:
    name: str
    arguments: dict


@dataclass(frozen=True)
class ModelRequest:
    goal: str
    memories: list[MemoryRecord]
    tools: list[ToolSpec]
    skills: list[SkillRecord] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ModelResponse:
    message: str
    tool_calls: list[ToolCallRequest] = field(default_factory=list)


class ModelClient(Protocol):
    configured: bool

    async def complete(self, request: ModelRequest) -> ModelResponse:
        raise NotImplementedError


class LiteLLMModelClient:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.configured = settings.model_configured

    async def complete(self, request: ModelRequest) -> ModelResponse:
        if not self.settings.litellm_model:
            raise RuntimeError("MNEMOSYNE_LITELLM_MODEL is not configured")
        tool_catalog = "\n".join(
            f"- {tool.name}: {tool.description}; schema={json.dumps(tool.input_schema)}"
            for tool in request.tools
        )
        memory_text = "\n".join(f"- {memory.text}" for memory in request.memories) or "- none"
        skill_text = (
            "\n".join(
                (
                    f"- {skill.name}: {skill.description}\n"
                    f"  Instructions: {skill.instructions}\n"
                    f"  Preferred tools: {', '.join(skill.tool_names) or 'none'}"
                )
                for skill in request.skills
            )
            or "- none"
        )
        tool_results_text = (
            json.dumps(request.tool_results, indent=2) if request.tool_results else "none yet"
        )
        tools_payload = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in request.tools
        ]
        if request.tool_results:
            tools_payload = []
        system_prompt = (
            "You are Mnemosyne Core's research assistant. Use Markdown for final "
            "answers. For current facts, latest information, source discovery, or "
            "repository lists, call web_search before answering. If native tool calls "
            "are unavailable, include a JSON block named tool_calls with objects "
            "containing name and arguments. For filesystem tools, use exact paths "
            "beneath the allowed roots shown in the tool descriptions. On Windows, "
            "use drive-letter paths such as F:\\folder\\file.md, not /mnt/f/ paths. "
            "When relevant skills are provided, follow their instructions."
        )
        if request.tool_results:
            system_prompt = (
                "You are Mnemosyne Core's research assistant. Use Markdown for final "
                "answers. Synthesize only from the provided tool results, relevant "
                "memory, and relevant skills. Include Markdown links to source URLs. "
                "Do not include tool_calls, XML, JSON tool requests, or requests for "
                "more searching."
            )
        response = await acompletion(
            model=self.settings.litellm_model,
            messages=[
                {
                    "role": "system",
                    "content": system_prompt,
                },
                {
                    "role": "user",
                    "content": (
                        f"Goal: {request.goal}\n\nRelevant memory:\n{memory_text}\n\n"
                        f"Relevant skills:\n{skill_text}\n\n"
                        f"Available safe tools:\n{tool_catalog}\n\n"
                        f"Tool results already collected:\n{tool_results_text}\n\n"
                        "When tool results are present, synthesize the answer from them and "
                        "include Markdown links to source URLs."
                    ),
                },
            ],
            tools=tools_payload or None,
            tool_choice="auto" if tools_payload else None,
        )
        message = response.choices[0].message
        content = message.content or ""
        tool_calls = _extract_native_tool_calls(message) or _extract_tool_calls(content)
        return ModelResponse(message=content, tool_calls=tool_calls)


def _extract_native_tool_calls(message: Any) -> list[ToolCallRequest]:
    raw_tool_calls = getattr(message, "tool_calls", None)
    if not raw_tool_calls and isinstance(message, dict):
        raw_tool_calls = message.get("tool_calls")
    calls: list[ToolCallRequest] = []
    for raw_call in raw_tool_calls or []:
        function = getattr(raw_call, "function", None)
        if function is None and isinstance(raw_call, dict):
            function = raw_call.get("function")
        name = getattr(function, "name", None)
        raw_arguments = getattr(function, "arguments", None)
        if isinstance(function, dict):
            name = function.get("name")
            raw_arguments = function.get("arguments")
        if not isinstance(name, str):
            continue
        arguments = _parse_arguments(raw_arguments)
        calls.append(ToolCallRequest(name=name, arguments=arguments))
    return calls


def _extract_tool_calls(content: str) -> list[ToolCallRequest]:
    xml_calls = _extract_xml_tool_calls(content)
    if xml_calls:
        return xml_calls
    marker = "tool_calls"
    if marker not in content:
        return []
    try:
        start = content.index("[", content.index(marker))
        end = content.index("]", start) + 1
        raw_calls = json.loads(content[start:end])
    except (ValueError, json.JSONDecodeError):
        return []
    calls: list[ToolCallRequest] = []
    for item in raw_calls:
        if isinstance(item, dict) and isinstance(item.get("name"), str):
            arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
            calls.append(ToolCallRequest(name=item["name"], arguments=arguments))
    return calls


def _extract_xml_tool_calls(content: str) -> list[ToolCallRequest]:
    calls: list[ToolCallRequest] = []
    for match in re.finditer(
        r"<tool_call\s+[^>]*tool_name=[\"'](?P<name>[^\"']+)[\"'][^>]*>"
        r"(?P<body>.*?)</tool_call>",
        content,
        flags=re.DOTALL | re.IGNORECASE,
    ):
        arguments: dict[str, Any] = {}
        for arg_match in re.finditer(
            r"<arg\s+[^>]*name=[\"'](?P<name>[^\"']+)[\"'][^>]*>"
            r"(?P<value>.*?)</arg>",
            match.group("body"),
            flags=re.DOTALL | re.IGNORECASE,
        ):
            arguments[arg_match.group("name")] = _coerce_xml_argument(
                html.unescape(arg_match.group("value")).strip()
            )
        calls.append(ToolCallRequest(name=match.group("name"), arguments=arguments))
    return calls


def _coerce_xml_argument(value: str) -> Any:
    lowered = value.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered.isdecimal():
        return int(lowered)
    return value


def _parse_arguments(raw_arguments: Any) -> dict:
    if isinstance(raw_arguments, dict):
        return raw_arguments
    if isinstance(raw_arguments, str) and raw_arguments.strip():
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}

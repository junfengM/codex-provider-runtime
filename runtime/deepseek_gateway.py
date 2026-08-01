#!/usr/bin/env python3
"""Loopback Responses API compatibility gateway for DeepSeek Chat Completions.

Codex custom providers currently speak the OpenAI Responses wire protocol, while
DeepSeek exposes structured agent tool calls through Chat Completions.  This
gateway keeps Codex unchanged at the transport boundary: it accepts Responses
requests on loopback, translates them to DeepSeek Chat Completions, and emits
Responses-compatible SSE events back to Codex.

The gateway deliberately never logs request bodies or Authorization headers.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import re
import ssl
import sys
import time
import uuid
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Iterable, Iterator, Mapping, MutableMapping, Optional
from urllib.parse import urlparse


GATEWAY_VERSION = "1"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 17892
DEFAULT_UPSTREAM = "https://api.deepseek.com"
MAX_REQUEST_BYTES = 32 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024
FUNCTION_NAME_RE = re.compile(r"[^A-Za-z0-9_-]+")
MODEL_ALIASES = {
    # Auto-review is an internal Codex model slug. A DeepSeek thread inherits
    # its provider for the reviewer sub-session, so translate the model at the
    # gateway boundary instead of sending an unsupported slug upstream.
    "codex-auto-review": "deepseek-v4-pro",
}


class GatewayError(RuntimeError):
    def __init__(self, message: str, *, status: int = 400, code: str = "invalid_request_error"):
        super().__init__(message)
        self.status = status
        self.code = code

    def payload(self) -> dict[str, Any]:
        return {
            "error": {
                "message": str(self),
                "type": self.code,
                "code": self.code,
                "param": None,
            }
        }


@dataclass(frozen=True)
class ToolTarget:
    kind: str
    name: str
    namespace: Optional[str] = None


@dataclass
class Translation:
    body: dict[str, Any]
    tools: dict[str, ToolTarget]
    warnings: list[str]


def _safe_function_name(name: str, used: set[str]) -> str:
    normalized = FUNCTION_NAME_RE.sub("_", name).strip("_") or "codex_tool"
    if len(normalized) > 64:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
        normalized = f"{normalized[:53]}_{digest}"
    candidate = normalized
    if candidate in used:
        digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
        candidate = f"{normalized[:53]}_{digest}"
    suffix = 2
    while candidate in used:
        tail = f"_{suffix}"
        candidate = normalized[: 64 - len(tail)] + tail
        suffix += 1
    used.add(candidate)
    return candidate


def _function_schema(
    wire_name: str,
    description: str,
    parameters: Any,
) -> dict[str, Any]:
    if not isinstance(parameters, dict):
        parameters = {"type": "object", "properties": {}}
    return {
        "type": "function",
        "function": {
            "name": wire_name,
            "description": description or wire_name,
            "parameters": parameters,
        },
    }


def _is_code_mode_js_target(name: str, namespace: Optional[str] = None) -> bool:
    if namespace == "node_repl" and name == "js":
        return True
    return name in {"node_repl/js", "node_repl.js", "node_repl__js"}


def _code_mode_description(description: str) -> str:
    return (
        "Execute JavaScript in the Codex tool orchestrator. "
        "The JSON argument MUST use the required `code` field, for example "
        '{"code":"const r = await tools.exec_command({cmd: \\\"pwd\\\"}); '
        'text(r.output);"}. Never use expression, js, input, source, or script '
        "as a substitute for code.\n" + description
    )


def _code_mode_exec_description(description: str) -> str:
    return (
        "Execute raw JavaScript in Codex Code Mode. Put the JavaScript source in the outer "
        '`input` field (the gateway also accepts `code`). To run a shell command, the '
        "JavaScript MUST call `await tools.exec_command({cmd: \"...\"})` and then emit the "
        "result with `text(...)`. Never call `tools.node_repl.js` or any node_repl tool "
        "recursively.\n" + description
    )


def translate_tools(tools: Iterable[Any]) -> tuple[list[dict[str, Any]], dict[str, ToolTarget], list[str]]:
    translated: list[dict[str, Any]] = []
    mapping: dict[str, ToolTarget] = {}
    warnings: list[str] = []
    used: set[str] = set()

    for raw in tools:
        if not isinstance(raw, dict):
            warnings.append("ignored a non-object tool definition")
            continue
        kind = raw.get("type")
        if kind == "function":
            name = str(raw.get("name") or "codex_tool")
            wire = _safe_function_name(name, used)
            description = str(raw.get("description") or "")
            if _is_code_mode_js_target(name):
                description = _code_mode_description(description)
            translated.append(
                _function_schema(wire, description, raw.get("parameters"))
            )
            mapping[wire] = ToolTarget("function", name)
        elif kind == "namespace":
            namespace = str(raw.get("name") or "namespace")
            for child in raw.get("tools") or []:
                if not isinstance(child, dict) or child.get("type") != "function":
                    warnings.append(f"ignored unsupported tool in namespace {namespace}")
                    continue
                name = str(child.get("name") or "codex_tool")
                wire = _safe_function_name(f"{namespace}__{name}", used)
                description = str(child.get("description") or "")
                if _is_code_mode_js_target(name, namespace):
                    description = _code_mode_description(description)
                translated.append(_function_schema(wire, description, child.get("parameters")))
                mapping[wire] = ToolTarget("function", name, namespace)
        elif kind == "custom":
            name = str(raw.get("name") or "custom_tool")
            wire = _safe_function_name(name, used)
            description = str(raw.get("description") or name)
            tool_format = raw.get("format")
            if isinstance(tool_format, dict):
                syntax = tool_format.get("syntax")
                definition = tool_format.get("definition")
                if syntax:
                    description += f"\nInput syntax: {syntax}"
                if definition:
                    description += f"\n{definition}"
            if _is_code_mode_js_target(name):
                description = _code_mode_description(description)
                parameters = {
                    "type": "object",
                    "properties": {
                        "code": {
                            "type": "string",
                            "description": "JavaScript source executed by the Codex tool orchestrator.",
                        },
                        "title": {"type": "string"},
                        "timeout_ms": {"type": "integer", "minimum": 1},
                    },
                    "required": ["code"],
                }
            else:
                if name == "exec":
                    description = _code_mode_exec_description(description)
                parameters = {
                    "type": "object",
                    "properties": {
                        "input": {
                            "type": "string",
                            "description": "Exact raw input for this freeform Codex tool.",
                        }
                    },
                    "required": ["input"],
                }
            translated.append(
                _function_schema(wire, description[:16000], parameters)
            )
            mapping[wire] = ToolTarget("custom", name)
        elif kind == "tool_search":
            wire = _safe_function_name("codex_tool_search", used)
            translated.append(
                _function_schema(
                    wire,
                    str(raw.get("description") or "Search for additional Codex tools."),
                    raw.get("parameters"),
                )
            )
            mapping[wire] = ToolTarget("tool_search", "tool_search")
        elif kind == "web_search":
            # This is an OpenAI server-hosted tool and has no corresponding
            # client-side Codex executor.  The DeepSeek catalog disables it, so
            # seeing it here means an upstream/catalog drift worth surfacing.
            warnings.append("omitted OpenAI server-hosted web_search tool")
        else:
            warnings.append(f"ignored unsupported Responses tool type: {kind!r}")
    if len(translated) > 128:
        raise GatewayError(
            f"DeepSeek accepts at most 128 functions, but Codex exposed {len(translated)}; "
            "defer or disable unused MCP/plugin tools",
            code="too_many_tools",
        )
    return translated, mapping, warnings


def _content_text(content: Any, *, user_message: bool = False) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        if "content" in content:
            return _content_text(content["content"], user_message=user_message)
        if "content_items" in content:
            return _content_text(content["content_items"], user_message=user_message)
        return json.dumps(content, ensure_ascii=False, separators=(",", ":"))
    if not isinstance(content, list):
        return "" if content is None else str(content)

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            parts.append(str(item))
            continue
        kind = item.get("type")
        if kind in {"input_text", "output_text", "text", "summary_text", "reasoning_text"}:
            parts.append(str(item.get("text") or ""))
        elif kind == "input_image":
            if user_message:
                raise GatewayError(
                    "DeepSeek V4 in this Codex catalog is text-only; image input cannot be translated safely",
                    code="unsupported_modality",
                )
            parts.append("[tool returned an image that DeepSeek cannot inspect]")
        elif kind == "input_audio":
            if user_message:
                raise GatewayError(
                    "DeepSeek V4 in this Codex catalog is text-only; audio input cannot be translated safely",
                    code="unsupported_modality",
                )
            parts.append("[tool returned audio that DeepSeek cannot inspect]")
        else:
            parts.append(json.dumps(item, ensure_ascii=False, separators=(",", ":")))
    return "\n".join(part for part in parts if part)


def _reasoning_text(item: Mapping[str, Any]) -> str:
    content = _content_text(item.get("content") or [])
    if content:
        return content
    return _content_text(item.get("summary") or [])


def _target_wire_name(
    target: ToolTarget,
    mapping: Mapping[str, ToolTarget],
) -> str:
    for wire, candidate in mapping.items():
        if candidate == target:
            return wire
    used = set(mapping)
    qualified = f"{target.namespace}__{target.name}" if target.namespace else target.name
    return _safe_function_name(qualified, used)


def _custom_arguments(raw_input: str) -> str:
    return json.dumps({"input": raw_input}, ensure_ascii=False, separators=(",", ":"))


def _tool_search_arguments(raw: Any) -> str:
    if isinstance(raw, str):
        try:
            json.loads(raw)
            return raw
        except json.JSONDecodeError:
            return json.dumps({"query": raw}, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(raw if raw is not None else {}, ensure_ascii=False, separators=(",", ":"))


def translate_messages(
    instructions: str,
    items: Iterable[Any],
    tool_mapping: Mapping[str, ToolTarget],
) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if instructions:
        messages.append({"role": "system", "content": instructions})

    assistant: Optional[dict[str, Any]] = None

    def ensure_assistant() -> MutableMapping[str, Any]:
        nonlocal assistant
        if assistant is None:
            assistant = {"role": "assistant", "content": ""}
        return assistant

    def flush_assistant() -> None:
        nonlocal assistant
        if assistant is not None:
            messages.append(assistant)
            assistant = None

    for raw in items:
        if not isinstance(raw, dict):
            continue
        kind = raw.get("type")
        if kind in {"additional_tools", "compaction_trigger"}:
            continue
        if kind == "message":
            role = str(raw.get("role") or "user")
            if role == "assistant":
                current = ensure_assistant()
                text = _content_text(raw.get("content") or [])
                current["content"] = str(current.get("content") or "") + text
            else:
                flush_assistant()
                chat_role = "system" if role in {"developer", "system"} else "user"
                messages.append(
                    {
                        "role": chat_role,
                        "content": _content_text(
                            raw.get("content") or [], user_message=chat_role == "user"
                        ),
                    }
                )
        elif kind == "reasoning":
            current = ensure_assistant()
            reasoning = _reasoning_text(raw)
            if reasoning:
                current["reasoning_content"] = reasoning
        elif kind in {"function_call", "custom_tool_call", "tool_search_call"}:
            current = ensure_assistant()
            call_id = str(raw.get("call_id") or raw.get("id") or f"call_{uuid.uuid4().hex}")
            namespace = raw.get("namespace")
            name = str(raw.get("name") or "tool_search")
            target_kind = {
                "function_call": "function",
                "custom_tool_call": "custom",
                "tool_search_call": "tool_search",
            }[str(kind)]
            target = ToolTarget(target_kind, name, str(namespace) if namespace else None)
            wire_name = _target_wire_name(target, tool_mapping)
            if kind == "function_call":
                arguments = str(raw.get("arguments") or "{}")
            elif kind == "custom_tool_call":
                arguments = _custom_arguments(str(raw.get("input") or ""))
            else:
                arguments = _tool_search_arguments(raw.get("arguments"))
            current.setdefault("tool_calls", []).append(
                {
                    "id": call_id,
                    "type": "function",
                    "function": {"name": wire_name, "arguments": arguments},
                }
            )
        elif kind in {"function_call_output", "custom_tool_call_output", "tool_search_output"}:
            flush_assistant()
            call_id = str(raw.get("call_id") or raw.get("id") or "")
            if kind == "tool_search_output":
                output = json.dumps(
                    {
                        "status": raw.get("status"),
                        "execution": raw.get("execution"),
                        "tools": raw.get("tools") or [],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            else:
                output = _content_text(raw.get("output"))
            messages.append({"role": "tool", "tool_call_id": call_id, "content": output})
        elif kind == "compaction":
            # Encrypted OpenAI compaction cannot be decoded.  The normal custom
            # model catalog does not request remote compaction.
            continue
    flush_assistant()
    return messages


def _reasoning_effort(reasoning: Any) -> Optional[str]:
    if not isinstance(reasoning, dict):
        return None
    effort = reasoning.get("effort")
    if effort is None:
        return None
    value = str(effort).lower()
    if value in {"max", "xhigh"}:
        return "max"
    if value in {"minimal", "low", "medium", "high"}:
        return "high"
    return None


def responses_to_chat(request: Mapping[str, Any]) -> Translation:
    model = request.get("model")
    if not isinstance(model, str) or not model:
        raise GatewayError("Responses request is missing a model")
    upstream_model = MODEL_ALIASES.get(model, model)
    if not upstream_model.startswith("deepseek-"):
        raise GatewayError(
            f"the DeepSeek gateway refuses non-DeepSeek model {model!r}",
            code="model_mismatch",
        )
    if request.get("stream") is False:
        raise GatewayError("Codex DeepSeek gateway requires stream=true")

    items = request.get("input") or []
    if not isinstance(items, list):
        raise GatewayError("Responses input must be an array")
    raw_tools = list(request.get("tools") or [])
    for item in items:
        if isinstance(item, dict) and item.get("type") == "additional_tools":
            raw_tools.extend(item.get("tools") or [])
    chat_tools, mapping, warnings = translate_tools(raw_tools)
    messages = translate_messages(str(request.get("instructions") or ""), items, mapping)

    text_controls = request.get("text")
    response_format: Optional[dict[str, str]] = None
    if isinstance(text_controls, dict) and isinstance(text_controls.get("format"), dict):
        text_format = text_controls["format"]
        schema = text_format.get("schema")
        if schema is not None:
            messages.insert(
                0,
                {
                    "role": "system",
                    "content": (
                        "Return one valid JSON object matching this schema. Do not wrap it in Markdown:\n"
                        + json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
                    ),
                },
            )
            response_format = {"type": "json_object"}
            warnings.append("translated Responses JSON Schema to DeepSeek json_object best effort")

    body: dict[str, Any] = {
        "model": upstream_model,
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    effort = _reasoning_effort(request.get("reasoning"))
    if effort:
        body["reasoning_effort"] = effort
    if chat_tools:
        body["tools"] = chat_tools
        # DeepSeek thinking mode rejects tool_choice=required/specific. Codex
        # uses auto, and auto preserves structured tool calling.
        requested_choice = request.get("tool_choice", "auto")
        body["tool_choice"] = "none" if requested_choice == "none" else "auto"
        if requested_choice not in {None, "auto", "none"}:
            warnings.append("normalized unsupported thinking-mode tool_choice to auto")
    if response_format:
        body["response_format"] = response_format
    if upstream_model != model:
        warnings.append(f"mapped internal Codex model {model} to {upstream_model}")
    return Translation(body=body, tools=mapping, warnings=warnings)


def _parse_wrapped_input(arguments: str) -> str:
    try:
        value = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments
    if isinstance(value, dict) and isinstance(value.get("input"), str):
        return value["input"]
    return arguments


def _parse_tool_search_arguments(arguments: str) -> Any:
    try:
        return json.loads(arguments)
    except json.JSONDecodeError:
        return {"query": arguments}


def _normalize_function_arguments(target: ToolTarget, arguments: str) -> str:
    if not _is_code_mode_js_target(target.name, target.namespace):
        return arguments or "{}"
    try:
        value = json.loads(arguments)
    except json.JSONDecodeError:
        return arguments or "{}"
    if not isinstance(value, dict) or isinstance(value.get("code"), str):
        return arguments or "{}"
    for alias in ("expression", "js", "input", "source", "script"):
        candidate = value.get(alias)
        if isinstance(candidate, str) and candidate:
            normalized: dict[str, Any] = {"code": candidate}
            if isinstance(value.get("title"), str):
                normalized["title"] = value["title"]
            if isinstance(value.get("timeout_ms"), int):
                normalized["timeout_ms"] = value["timeout_ms"]
            return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    return arguments or "{}"


def _normalize_custom_input(target: ToolTarget, arguments: str) -> str:
    if target.name == "exec" and target.namespace is None:
        try:
            value = json.loads(arguments)
        except json.JSONDecodeError:
            return arguments
        if isinstance(value, dict):
            for alias in ("input", "code", "js", "source", "script", "expression"):
                candidate = value.get(alias)
                if isinstance(candidate, str) and candidate:
                    return candidate
        return arguments
    raw_input = _parse_wrapped_input(arguments)
    if not _is_code_mode_js_target(target.name, target.namespace):
        return raw_input
    try:
        value = json.loads(raw_input)
    except json.JSONDecodeError:
        value = None
    if isinstance(value, dict):
        if isinstance(value.get("code"), str):
            return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        for alias in ("expression", "js", "input", "source", "script"):
            candidate = value.get(alias)
            if isinstance(candidate, str) and candidate:
                normalized: dict[str, Any] = {"code": candidate}
                if isinstance(value.get("title"), str):
                    normalized["title"] = value["title"]
                if isinstance(value.get("timeout_ms"), int):
                    normalized["timeout_ms"] = value["timeout_ms"]
                return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"))
    if raw_input:
        return json.dumps({"code": raw_input}, ensure_ascii=False, separators=(",", ":"))
    return json.dumps({"code": ""}, separators=(",", ":"))


class ChatStreamTranslator:
    def __init__(self, model: str, tools: Mapping[str, ToolTarget]):
        token = uuid.uuid4().hex
        self.response_id = f"resp_deepseek_{token}"
        self.model = model
        self.tools = dict(tools)
        self.created = False
        self.completed = False
        self.reasoning = ""
        self.content = ""
        self.reasoning_added = False
        self.message_added = False
        self.tool_calls: dict[int, dict[str, str]] = {}
        self.finish_reason: Optional[str] = None
        self.usage: dict[str, Any] = {}

    def _id(self, prefix: str, index: Optional[int] = None) -> str:
        suffix = self.response_id.rsplit("_", 1)[-1]
        return f"{prefix}_{suffix}" if index is None else f"{prefix}_{index}_{suffix}"

    def _created(self) -> list[dict[str, Any]]:
        if self.created:
            return []
        self.created = True
        return [
            {
                "type": "response.created",
                "response": {"id": self.response_id, "model": self.model},
            }
        ]

    def feed(self, chunk: Mapping[str, Any]) -> list[dict[str, Any]]:
        events = self._created()
        model = chunk.get("model")
        if isinstance(model, str) and model:
            self.model = model
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            self.usage = usage
        choices = chunk.get("choices")
        if not isinstance(choices, list) or not choices:
            return events
        choice = choices[0] if isinstance(choices[0], dict) else {}
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}

        reasoning_delta = delta.get("reasoning_content")
        if isinstance(reasoning_delta, str) and reasoning_delta:
            if not self.reasoning_added:
                self.reasoning_added = True
                events.append(
                    {
                        "type": "response.output_item.added",
                        "item": {
                            "type": "reasoning",
                            "id": self._id("rs"),
                            "summary": [],
                            "content": [],
                            "encrypted_content": None,
                        },
                    }
                )
            self.reasoning += reasoning_delta
            events.append(
                {
                    "type": "response.reasoning_text.delta",
                    "item_id": self._id("rs"),
                    "content_index": 0,
                    "delta": reasoning_delta,
                }
            )

        content_delta = delta.get("content")
        if isinstance(content_delta, str) and content_delta:
            if not self.message_added:
                self.message_added = True
                events.append(
                    {
                        "type": "response.output_item.added",
                        "item": {
                            "type": "message",
                            "role": "assistant",
                            "id": self._id("msg"),
                            "content": [{"type": "output_text", "text": ""}],
                        },
                    }
                )
            self.content += content_delta
            events.append({"type": "response.output_text.delta", "delta": content_delta})

        tool_deltas = delta.get("tool_calls")
        if isinstance(tool_deltas, list):
            for raw in tool_deltas:
                if not isinstance(raw, dict):
                    continue
                index = int(raw.get("index") or 0)
                call = self.tool_calls.setdefault(
                    index,
                    {"id": "", "name": "", "arguments": ""},
                )
                if raw.get("id"):
                    call["id"] += str(raw["id"])
                function = raw.get("function")
                if isinstance(function, dict):
                    if function.get("name"):
                        call["name"] += str(function["name"])
                    if function.get("arguments"):
                        call["arguments"] += str(function["arguments"])
        if choice.get("finish_reason") is not None:
            self.finish_reason = str(choice["finish_reason"])
        return events

    def finish(self) -> list[dict[str, Any]]:
        if self.completed:
            return []
        self.completed = True
        events = self._created()
        if self.reasoning_added:
            events.append(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "reasoning",
                        "id": self._id("rs"),
                        "summary": [],
                        "content": [{"type": "reasoning_text", "text": self.reasoning}],
                        "encrypted_content": None,
                    },
                }
            )
        if self.message_added:
            events.append(
                {
                    "type": "response.output_item.done",
                    "item": {
                        "type": "message",
                        "role": "assistant",
                        "id": self._id("msg"),
                        "content": [{"type": "output_text", "text": self.content}],
                    },
                }
            )
        for index in sorted(self.tool_calls):
            call = self.tool_calls[index]
            call_id = call["id"] or f"call_{uuid.uuid4().hex}"
            target = self.tools.get(call["name"], ToolTarget("function", call["name"] or "tool"))
            item: dict[str, Any]
            if target.kind == "custom":
                item = {
                    "type": "custom_tool_call",
                    "id": self._id("ct", index),
                    "call_id": call_id,
                    "name": target.name,
                    "input": _normalize_custom_input(target, call["arguments"]),
                }
                if target.namespace:
                    item["namespace"] = target.namespace
            elif target.kind == "tool_search":
                item = {
                    "type": "tool_search_call",
                    "id": self._id("ts", index),
                    "call_id": call_id,
                    "status": "completed",
                    "execution": "client",
                    "arguments": _parse_tool_search_arguments(call["arguments"]),
                }
            else:
                item = {
                    "type": "function_call",
                    "id": self._id("fc", index),
                    "call_id": call_id,
                    "name": target.name,
                    "arguments": _normalize_function_arguments(target, call["arguments"]),
                }
                if target.namespace:
                    item["namespace"] = target.namespace
            events.append({"type": "response.output_item.done", "item": item})

        if self.finish_reason == "insufficient_system_resource":
            events.append(
                {
                    "type": "response.failed",
                    "response": {
                        "id": self.response_id,
                        "error": {
                            "type": "server_error",
                            "code": "server_is_overloaded",
                            "message": "DeepSeek reported insufficient inference resources",
                        },
                    },
                }
            )
            return events
        if self.finish_reason in {"length", "content_filter"}:
            events.append(
                {
                    "type": "response.incomplete",
                    "response": {
                        "id": self.response_id,
                        "incomplete_details": {"reason": self.finish_reason},
                    },
                }
            )
            return events

        prompt_tokens = int(self.usage.get("prompt_tokens") or 0)
        completion_tokens = int(self.usage.get("completion_tokens") or 0)
        total_tokens = int(self.usage.get("total_tokens") or prompt_tokens + completion_tokens)
        prompt_details = self.usage.get("prompt_tokens_details") or {}
        completion_details = self.usage.get("completion_tokens_details") or {}
        cached_tokens = int(
            prompt_details.get("cached_tokens")
            or self.usage.get("prompt_cache_hit_tokens")
            or 0
        )
        reasoning_tokens = int(completion_details.get("reasoning_tokens") or 0)
        events.append(
            {
                "type": "response.completed",
                "response": {
                    "id": self.response_id,
                    "model": self.model,
                    "usage": {
                        "input_tokens": prompt_tokens,
                        "input_tokens_details": {
                            "cached_tokens": cached_tokens,
                            "cache_write_tokens": 0,
                        },
                        "output_tokens": completion_tokens,
                        "output_tokens_details": {"reasoning_tokens": reasoning_tokens},
                        "total_tokens": total_tokens,
                    },
                    "end_turn": not bool(self.tool_calls),
                },
            }
        )
        return events


def chat_chunks_to_responses(
    chunks: Iterable[Mapping[str, Any]],
    *,
    model: str,
    tools: Mapping[str, ToolTarget],
) -> list[dict[str, Any]]:
    translator = ChatStreamTranslator(model, tools)
    events: list[dict[str, Any]] = []
    for chunk in chunks:
        events.extend(translator.feed(chunk))
    events.extend(translator.finish())
    return events


def _upstream_connection(base_url: str, timeout: float) -> tuple[http.client.HTTPConnection, str]:
    parsed = urlparse(base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise GatewayError("invalid DeepSeek upstream URL", status=500, code="gateway_config_error")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    if parsed.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            parsed.hostname,
            port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
    else:
        connection = http.client.HTTPConnection(parsed.hostname, port, timeout=timeout)
    prefix = parsed.path.rstrip("/")
    return connection, f"{prefix}/chat/completions"


def _sse_bytes(event: Mapping[str, Any]) -> bytes:
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"data: {payload}\n\n".encode("utf-8")


class GatewayServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], upstream: str, timeout: float):
        super().__init__(address, GatewayHandler)
        self.upstream = upstream
        self.upstream_timeout = timeout


class GatewayHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.0"
    server: GatewayServer

    def log_message(self, format: str, *args: Any) -> None:
        # Method/path/status are useful operationally; headers and bodies are not.
        sys.stderr.write(
            "%s gateway %s\n" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), format % args)
        )

    def _json(self, status: int, payload: Mapping[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path.rstrip("/") in {"/healthz", "/v1/healthz"}:
            self._json(
                200,
                {
                    "status": "ok",
                    "gateway": "codex-deepseek-responses",
                    "version": GATEWAY_VERSION,
                    "upstream": self.server.upstream,
                },
            )
            return
        self._json(404, GatewayError("not found", status=404, code="not_found").payload())

    def do_POST(self) -> None:
        if self.path.rstrip("/") not in {"/responses", "/v1/responses"}:
            self._json(404, GatewayError("not found", status=404, code="not_found").payload())
            return
        stream_started = False
        translator: Optional[ChatStreamTranslator] = None
        try:
            length = int(self.headers.get("Content-Length") or "0")
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise GatewayError("invalid or oversized request body", status=413)
            authorization = self.headers.get("Authorization")
            if not authorization or not authorization.lower().startswith("bearer "):
                raise GatewayError("missing DeepSeek bearer credential", status=401, code="authentication_error")
            try:
                request = json.loads(self.rfile.read(length))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise GatewayError("request body is not valid JSON") from error
            if not isinstance(request, dict):
                raise GatewayError("request body must be a JSON object")
            translation = responses_to_chat(request)
            connection, path = _upstream_connection(
                self.server.upstream, self.server.upstream_timeout
            )
            upstream_body = json.dumps(
                translation.body, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
            connection.request(
                "POST",
                path,
                body=upstream_body,
                headers={
                    "Authorization": authorization,
                    "Content-Type": "application/json",
                    "Accept": "text/event-stream",
                    "User-Agent": f"codex-provider-runtime/{GATEWAY_VERSION}",
                },
            )
            upstream = connection.getresponse()
            if not 200 <= upstream.status < 300:
                raw_error = upstream.read(MAX_ERROR_BYTES)
                try:
                    payload = json.loads(raw_error)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = GatewayError(
                        f"DeepSeek upstream returned HTTP {upstream.status}",
                        status=upstream.status,
                        code="upstream_error",
                    ).payload()
                self._json(upstream.status, payload)
                connection.close()
                return

            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Cache-Control", "no-cache, no-store")
            self.send_header("Connection", "close")
            self.send_header("OpenAI-Model", str(request.get("model") or ""))
            if translation.warnings:
                self.send_header("X-Codex-Provider-Warnings", str(len(translation.warnings)))
            self.end_headers()
            stream_started = True

            translator = ChatStreamTranslator(str(request["model"]), translation.tools)
            saw_done = False
            while True:
                line = upstream.readline()
                if not line:
                    break
                stripped = line.strip()
                if not stripped.startswith(b"data:"):
                    continue
                data = stripped[5:].strip()
                if data == b"[DONE]":
                    saw_done = True
                    break
                try:
                    chunk = json.loads(data)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    continue
                if not isinstance(chunk, dict):
                    continue
                for event in translator.feed(chunk):
                    self.wfile.write(_sse_bytes(event))
                    self.wfile.flush()
            if not saw_done and translator.finish_reason is None:
                failed = {
                    "type": "response.failed",
                    "response": {
                        "id": translator.response_id,
                        "error": {
                            "type": "upstream_stream_error",
                            "code": "upstream_stream_error",
                            "message": "DeepSeek stream closed before completion",
                        },
                    },
                }
                self.wfile.write(_sse_bytes(failed))
            else:
                for event in translator.finish():
                    self.wfile.write(_sse_bytes(event))
            self.wfile.flush()
            connection.close()
        except GatewayError as error:
            self._json(error.status, error.payload())
        except (BrokenPipeError, ConnectionResetError):
            return
        except (OSError, http.client.HTTPException) as error:
            if stream_started:
                response_id = translator.response_id if translator else f"resp_deepseek_{uuid.uuid4().hex}"
                failed = {
                    "type": "response.failed",
                    "response": {
                        "id": response_id,
                        "error": {
                            "type": "upstream_connection_error",
                            "code": "upstream_connection_error",
                            "message": f"DeepSeek upstream stream failed: {type(error).__name__}",
                        },
                    },
                }
                try:
                    self.wfile.write(_sse_bytes(failed))
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    pass
            else:
                self._json(
                    502,
                    GatewayError(
                        f"DeepSeek upstream connection failed: {type(error).__name__}",
                        status=502,
                        code="upstream_connection_error",
                    ).payload(),
                )


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--upstream", default=DEFAULT_UPSTREAM)
    parser.add_argument("--timeout", type=float, default=300.0)
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    if args.host not in {"127.0.0.1", "::1", "localhost"}:
        print("deepseek-gateway: refusing non-loopback bind", file=sys.stderr)
        return 2
    server = GatewayServer((args.host, args.port), args.upstream, args.timeout)
    print(
        f"deepseek-gateway v{GATEWAY_VERSION} listening on {args.host}:{args.port}",
        file=sys.stderr,
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "runtime"))

import deepseek_gateway


class RequestTranslationTests(unittest.TestCase):
    def test_translates_function_namespace_custom_and_tool_search(self) -> None:
        request = {
            "model": "deepseek-v4-flash",
            "instructions": "You are a coding agent.",
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Inspect the tree"}],
                }
            ],
            "tools": [
                {
                    "type": "function",
                    "name": "exec_command",
                    "description": "Run a command",
                    "strict": False,
                    "parameters": {
                        "type": "object",
                        "properties": {"cmd": {"type": "string"}},
                        "required": ["cmd"],
                    },
                },
                {
                    "type": "namespace",
                    "name": "mcp:files",
                    "description": "File tools",
                    "tools": [
                        {
                            "type": "function",
                            "name": "read/file",
                            "description": "Read one file",
                            "strict": False,
                            "parameters": {"type": "object", "properties": {}},
                        }
                    ],
                },
                {
                    "type": "custom",
                    "name": "apply_patch",
                    "description": "Apply a patch",
                    "format": {"type": "grammar", "syntax": "lark", "definition": "start: patch"},
                },
                {
                    "type": "tool_search",
                    "execution": "client",
                    "description": "Find tools",
                    "parameters": {"type": "object", "properties": {}},
                },
                {"type": "web_search"},
            ],
            "tool_choice": "auto",
            "stream": True,
            "reasoning": {"effort": "xhigh"},
        }

        translated = deepseek_gateway.responses_to_chat(request)

        self.assertEqual(translated.body["reasoning_effort"], "max")
        self.assertEqual(translated.body["tool_choice"], "auto")
        self.assertEqual(translated.body["messages"][0]["role"], "system")
        self.assertEqual(len(translated.body["tools"]), 4)
        names = [tool["function"]["name"] for tool in translated.body["tools"]]
        self.assertIn("exec_command", names)
        self.assertTrue(any(name.startswith("mcp_files__read_file") for name in names))
        self.assertEqual(
            translated.tools["apply_patch"],
            deepseek_gateway.ToolTarget("custom", "apply_patch"),
        )
        self.assertTrue(any("web_search" in warning for warning in translated.warnings))

    def test_preserves_reasoning_and_tool_loop_history(self) -> None:
        tools = [
            {
                "type": "function",
                "name": "exec_command",
                "description": "Run a command",
                "parameters": {"type": "object", "properties": {}},
            }
        ]
        request = {
            "model": "deepseek-v4-flash",
            "stream": True,
            "tools": tools,
            "input": [
                {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "Run pwd"}],
                },
                {
                    "type": "reasoning",
                    "id": "rs_1",
                    "summary": [],
                    "content": [{"type": "reasoning_text", "text": "I need the tool."}],
                    "encrypted_content": None,
                },
                {
                    "type": "function_call",
                    "id": "fc_1",
                    "name": "exec_command",
                    "arguments": '{"cmd":"pwd"}',
                    "call_id": "call_1",
                },
                {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "/tmp",
                },
            ],
        }

        messages = deepseek_gateway.responses_to_chat(request).body["messages"]

        self.assertEqual(messages[1]["role"], "assistant")
        self.assertEqual(messages[1]["reasoning_content"], "I need the tool.")
        self.assertEqual(messages[1]["tool_calls"][0]["id"], "call_1")
        self.assertEqual(messages[2], {"role": "tool", "tool_call_id": "call_1", "content": "/tmp"})

    def test_custom_tool_history_wraps_raw_input(self) -> None:
        request = {
            "model": "deepseek-v4-pro",
            "stream": True,
            "tools": [
                {
                    "type": "custom",
                    "name": "apply_patch",
                    "description": "Apply patch",
                    "format": {"type": "grammar", "syntax": "lark", "definition": ""},
                }
            ],
            "input": [
                {
                    "type": "custom_tool_call",
                    "call_id": "patch_1",
                    "name": "apply_patch",
                    "input": "*** Begin Patch\n*** End Patch",
                }
            ],
        }

        assistant = deepseek_gateway.responses_to_chat(request).body["messages"][0]
        arguments = assistant["tool_calls"][0]["function"]["arguments"]
        self.assertEqual(
            deepseek_gateway._parse_wrapped_input(arguments),
            "*** Begin Patch\n*** End Patch",
        )

    def test_rejects_non_deepseek_model_and_user_image(self) -> None:
        with self.assertRaises(deepseek_gateway.GatewayError):
            deepseek_gateway.responses_to_chat(
                {"model": "gpt-5.6-sol", "stream": True, "input": []}
            )
        with self.assertRaises(deepseek_gateway.GatewayError) as context:
            deepseek_gateway.responses_to_chat(
                {
                    "model": "deepseek-v4-flash",
                    "stream": True,
                    "input": [
                        {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_image", "image_url": "data:image/png;base64,AA=="}],
                        }
                    ],
                }
            )
        self.assertEqual(context.exception.code, "unsupported_modality")

    def test_rejects_more_than_upstream_tool_limit(self) -> None:
        tools = [
            {
                "type": "function",
                "name": f"tool_{index}",
                "description": "test",
                "parameters": {"type": "object", "properties": {}},
            }
            for index in range(129)
        ]
        with self.assertRaises(deepseek_gateway.GatewayError) as context:
            deepseek_gateway.responses_to_chat(
                {
                    "model": "deepseek-v4-flash",
                    "stream": True,
                    "input": [],
                    "tools": tools,
                }
            )
        self.assertEqual(context.exception.code, "too_many_tools")

    def test_maps_internal_auto_review_model_to_deepseek_pro(self) -> None:
        translated = deepseek_gateway.responses_to_chat(
            {
                "model": "codex-auto-review",
                "stream": True,
                "input": [
                    {
                        "type": "message",
                        "role": "user",
                        "content": [{"type": "input_text", "text": "Review this action"}],
                    }
                ],
            }
        )
        self.assertEqual(translated.body["model"], "deepseek-v4-pro")
        self.assertTrue(any("codex-auto-review" in warning for warning in translated.warnings))

    def test_json_schema_becomes_json_object_with_schema_instruction(self) -> None:
        translated = deepseek_gateway.responses_to_chat(
            {
                "model": "deepseek-v4-flash",
                "stream": True,
                "input": [],
                "text": {
                    "format": {
                        "type": "json_schema",
                        "strict": True,
                        "name": "answer",
                        "schema": {"type": "object", "properties": {"ok": {"type": "boolean"}}},
                    }
                },
            }
        )
        self.assertEqual(translated.body["response_format"], {"type": "json_object"})
        self.assertIn("matching this schema", translated.body["messages"][0]["content"])


class StreamTranslationTests(unittest.TestCase):
    def test_stream_emits_reasoning_function_call_usage_and_completion(self) -> None:
        mapping = {"exec_command": deepseek_gateway.ToolTarget("function", "exec_command")}
        chunks = [
            {
                "model": "deepseek-v4-flash",
                "choices": [
                    {"delta": {"reasoning_content": "Use the tool."}, "finish_reason": None}
                ],
            },
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "function": {"name": "exec_command", "arguments": ""},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {"tool_calls": [{"index": 0, "function": {"arguments": '{"cmd":"pwd"}'}}]},
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 4,
                    "total_tokens": 14,
                    "prompt_tokens_details": {"cached_tokens": 2},
                    "completion_tokens_details": {"reasoning_tokens": 1},
                },
            },
        ]

        events = deepseek_gateway.chat_chunks_to_responses(
            chunks, model="deepseek-v4-flash", tools=mapping
        )

        kinds = [event["type"] for event in events]
        self.assertEqual(kinds[0], "response.created")
        self.assertIn("response.reasoning_text.delta", kinds)
        done_items = [
            event["item"]
            for event in events
            if event["type"] == "response.output_item.done"
        ]
        self.assertEqual(done_items[0]["type"], "reasoning")
        self.assertEqual(done_items[1]["type"], "function_call")
        self.assertEqual(done_items[1]["arguments"], '{"cmd":"pwd"}')
        completed = events[-1]["response"]
        self.assertEqual(completed["usage"]["input_tokens_details"]["cached_tokens"], 2)
        self.assertFalse(completed["end_turn"])

    def test_stream_unwraps_custom_tool_input(self) -> None:
        mapping = {"apply_patch": deepseek_gateway.ToolTarget("custom", "apply_patch")}
        events = deepseek_gateway.chat_chunks_to_responses(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_patch",
                                        "function": {
                                            "name": "apply_patch",
                                            "arguments": '{"input":"*** Begin Patch\\n*** End Patch"}',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ],
            model="deepseek-v4-pro",
            tools=mapping,
        )
        item = next(
            event["item"]
            for event in events
            if event.get("item", {}).get("type") == "custom_tool_call"
        )
        self.assertEqual(item["input"], "*** Begin Patch\n*** End Patch")

    def test_length_finish_becomes_incomplete(self) -> None:
        events = deepseek_gateway.chat_chunks_to_responses(
            [{"choices": [{"delta": {"content": "partial"}, "finish_reason": "length"}]}],
            model="deepseek-v4-flash",
            tools={},
        )
        self.assertEqual(events[-1]["type"], "response.incomplete")

    def test_resource_finish_becomes_retryable_failure(self) -> None:
        events = deepseek_gateway.chat_chunks_to_responses(
            [
                {
                    "choices": [
                        {
                            "delta": {},
                            "finish_reason": "insufficient_system_resource",
                        }
                    ]
                }
            ],
            model="deepseek-v4-flash",
            tools={},
        )
        self.assertEqual(events[-1]["type"], "response.failed")
        self.assertEqual(
            events[-1]["response"]["error"]["code"], "server_is_overloaded"
        )

    def test_code_mode_argument_alias_is_normalized(self) -> None:
        mapping = {
            "node_repl__js": deepseek_gateway.ToolTarget(
                "function", "js", "node_repl"
            )
        }
        events = deepseek_gateway.chat_chunks_to_responses(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_js",
                                        "function": {
                                            "name": "node_repl__js",
                                            "arguments": '{"expression":"text(42)"}',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ],
            model="deepseek-v4-flash",
            tools=mapping,
        )
        item = next(
            event["item"]
            for event in events
            if event.get("item", {}).get("type") == "function_call"
        )
        self.assertEqual(item["namespace"], "node_repl")
        self.assertEqual(json.loads(item["arguments"]), {"code": "text(42)"})

    def test_flat_code_mode_function_argument_alias_is_normalized(self) -> None:
        mapping = {
            "node_repl_js": deepseek_gateway.ToolTarget(
                "function", "node_repl/js"
            )
        }
        events = deepseek_gateway.chat_chunks_to_responses(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_js",
                                        "function": {
                                            "name": "node_repl_js",
                                            "arguments": '{"input":"text(42)"}',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ],
            model="deepseek-v4-flash",
            tools=mapping,
        )
        item = next(
            event["item"]
            for event in events
            if event.get("item", {}).get("type") == "function_call"
        )
        self.assertNotIn("namespace", item)
        self.assertEqual(item["name"], "node_repl/js")
        self.assertEqual(json.loads(item["arguments"]), {"code": "text(42)"})

    def test_code_mode_custom_tool_uses_schema_and_normalizes_input(self) -> None:
        translated = deepseek_gateway.responses_to_chat(
            {
                "model": "deepseek-v4-flash",
                "stream": True,
                "input": [],
                "tools": [
                    {
                        "type": "custom",
                        "name": "node_repl/js",
                        "description": "Execute JavaScript",
                        "format": {
                            "type": "grammar",
                            "syntax": "lark",
                            "definition": "start: object",
                        },
                    }
                ],
            }
        )
        wire_name = translated.body["tools"][0]["function"]["name"]
        schema = translated.body["tools"][0]["function"]["parameters"]
        self.assertEqual(schema["required"], ["code"])
        events = deepseek_gateway.chat_chunks_to_responses(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_custom_js",
                                        "function": {
                                            "name": wire_name,
                                            "arguments": '{"input":"text(42)"}',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ],
            model="deepseek-v4-flash",
            tools=translated.tools,
        )
        item = next(
            event["item"]
            for event in events
            if event.get("item", {}).get("type") == "custom_tool_call"
        )
        self.assertEqual(item["name"], "node_repl/js")
        self.assertEqual(json.loads(item["input"]), {"code": "text(42)"})

    def test_code_mode_exec_unwraps_code_alias_to_raw_javascript(self) -> None:
        translated = deepseek_gateway.responses_to_chat(
            {
                "model": "deepseek-v4-flash",
                "stream": True,
                "input": [],
                "tools": [
                    {
                        "type": "custom",
                        "name": "exec",
                        "description": "Run JavaScript",
                        "format": {
                            "type": "grammar",
                            "syntax": "lark",
                            "definition": "start: code",
                        },
                    }
                ],
            }
        )
        description = translated.body["tools"][0]["function"]["description"]
        self.assertIn("tools.exec_command", description)
        events = deepseek_gateway.chat_chunks_to_responses(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_exec",
                                        "function": {
                                            "name": "exec",
                                            "arguments": '{"code":"text(42)"}',
                                        },
                                    }
                                ]
                            },
                            "finish_reason": "tool_calls",
                        }
                    ]
                }
            ],
            model="deepseek-v4-flash",
            tools=translated.tools,
        )
        item = next(
            event["item"]
            for event in events
            if event.get("item", {}).get("type") == "custom_tool_call"
        )
        self.assertEqual(item["name"], "exec")
        self.assertEqual(item["input"], "text(42)")


if __name__ == "__main__":
    unittest.main()

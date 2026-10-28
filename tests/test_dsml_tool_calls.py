"""服务端 DSML 方言解析回归（DeepSeek 网页端 2026-10-28 实测回放）。"""

from __future__ import annotations

import json

from app.services.tool_calling_parse import parse_tool_response
from app.services.tool_calling_validation_retry import (
    _detect_malformed_tool_payload,
    _inspect_tool_response,
)

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "read",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "offset": {"type": "integer"},
                    "limit": {"type": "integer"},
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "run",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": ["command"],
            },
        },
    },
]

#: 用户实测原文（冒烟轮）：模型用 DSML 替代了 <adapter_calls>。
OBSERVED_DSML = (
    "<｜｜DSML｜｜ calls>\n"
    '<｜｜DSML｜｜ invoke name="read_file">\n'
    '<｜｜DSML｜｜ parameter name="path" string="true">.uwa_smoke.txt</｜｜DSML｜｜ parameter>\n'
    "</｜｜DSML｜｜ invoke>\n"
    "</｜｜DSML｜｜ calls>"
)

#: 用户实测原文（第 11 步终答）：limit 带 string="false" 类型提示。
OBSERVED_DSML_TYPED = (
    '<｜｜DSML｜｜ calls>\n'
    '<｜｜DSML｜｜ invoke name="read_file">\n'
    '<｜｜DSML｜｜ parameter name="path" string="true">pyproject.toml</｜｜DSML｜｜ parameter>\n'
    '<｜｜DSML｜｜ parameter name="limit" string="false">60</｜｜DSML｜｜ parameter>\n'
    "</｜｜DSML｜｜ invoke>\n"
    "</｜｜DSML｜｜ calls>"
)


def _arguments(parsed) -> dict:
    return json.loads(parsed["tool_calls"][0]["function"]["arguments"])


def test_dsml_observed_payload_parses():
    parsed = parse_tool_response(OBSERVED_DSML, TOOLS)
    assert parsed["mode"] == "tool_calls"
    assert parsed["tool_calls"][0]["function"]["name"] == "read_file"
    assert _arguments(parsed) == {"path": ".uwa_smoke.txt"}
    assert parsed["content"] in (None, "")


def test_dsml_typed_params_coerce_by_schema():
    parsed = parse_tool_response(OBSERVED_DSML_TYPED, TOOLS)
    assert parsed["mode"] == "tool_calls"
    args = _arguments(parsed)
    assert args["path"] == "pyproject.toml"
    assert args["limit"] == 60 and isinstance(args["limit"], int)


def test_dsml_passes_full_inspection():
    parsed = parse_tool_response(OBSERVED_DSML_TYPED, TOOLS)
    inspection = _inspect_tool_response(
        raw_text=OBSERVED_DSML_TYPED, parsed=parsed, tools=TOOLS,
        tool_choice="auto", parallel_tool_calls=None,
    )
    assert inspection["errors"] == []
    assert len(inspection["accepted_tool_calls"]) == 1


def test_dsml_unknown_tool_is_malformed():
    text = (
        '<｜｜DSML｜｜ invoke name="delete_all">'
        '<｜｜DSML｜｜ parameter name="path">x</｜｜DSML｜｜ parameter>'
        "</｜｜DSML｜｜ invoke>"
    )
    parsed = parse_tool_response(text, TOOLS)
    assert parsed["mode"] == "final"  # 未知工具：解析层放过
    reason = _detect_malformed_tool_payload(text, allowed_tool_names={"read_file", "bash"})
    assert "DSML" in reason  # 校验层识别为 malformed，触发修复重试
    inspection = _inspect_tool_response(
        raw_text=text, parsed=parsed, tools=TOOLS,
        tool_choice="auto", parallel_tool_calls=None,
    )
    assert [e["code"] for e in inspection["errors"]] == ["malformed_tool_payload"]


def test_dsml_halfwidth_variant():
    text = (
        "<||DSML|| invoke name=\"bash\">"
        "<||DSML|| parameter name=\"command\">dir</||DSML|| parameter>"
        "</||DSML|| invoke>"
    )
    parsed = parse_tool_response(text, TOOLS)
    assert parsed["mode"] == "tool_calls"
    assert _arguments(parsed) == {"command": "dir"}


def test_dsml_multiple_invokes():
    text = (
        '<｜｜DSML｜｜ invoke name="bash">'
        '<｜｜DSML｜｜ parameter name="command">echo one</｜｜DSML｜｜ parameter>'
        "</｜｜DSML｜｜ invoke>"
        '<｜｜DSML｜｜ invoke name="bash">'
        '<｜｜DSML｜｜ parameter name="command">echo two</｜｜DSML｜｜ parameter>'
        "</｜｜DSML｜｜ invoke>"
    )
    parsed = parse_tool_response(text, TOOLS)
    assert len(parsed["tool_calls"]) == 2


def test_dsml_keeps_prose_as_content():
    text = "我先读一下文件。\n" + OBSERVED_DSML
    parsed = parse_tool_response(text, TOOLS)
    assert parsed["mode"] == "tool_calls"
    assert parsed["content"] == "我先读一下文件。"


def test_plain_text_unaffected():
    text = "文件内容是：hello agent"
    parsed = parse_tool_response(text, TOOLS)
    assert parsed == {"mode": "final", "content": text, "tool_calls": []}
    assert _detect_malformed_tool_payload(text) == ""

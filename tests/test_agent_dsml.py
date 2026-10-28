"""uwa-agent DSML/宽松 XML 恢复解析单测（实战 payload 回放）。"""

from __future__ import annotations

import json

from agent.dsml import (
    find_dsml_calls,
    find_lenient_xml_calls,
    find_tool_attempts,
    needs_markup_retry,
    recover_tool_call,
)

#: 用户 2026-10-28 实测中 DeepSeek 网页端返回的原文（逐字）。
OBSERVED_DSML = (
    "<｜｜DSML｜｜ calls>\n"
    '<｜｜DSML｜｜ invoke name="read_file">\n'
    '<｜｜DSML｜｜ parameter name="path" string="true">.uwa_smoke.txt</｜｜DSML｜｜ parameter>\n'
    "</｜｜DSML｜｜ invoke>\n"
    "</｜｜DSML｜｜ calls>"
)


def test_parse_observed_payload():
    attempts = find_dsml_calls(OBSERVED_DSML)
    assert len(attempts) == 1
    assert attempts[0].name == "read_file"
    assert attempts[0].args == {"path": ".uwa_smoke.txt"}


def test_recover_observed_payload_end_to_end():
    call = recover_tool_call(OBSERVED_DSML)
    assert call is not None
    assert call["function"]["name"] == "read_file"
    assert json.loads(call["function"]["arguments"])["path"] == ".uwa_smoke.txt"
    assert call["id"].startswith("call_recovered_")


def test_halfwidth_bars_variant():
    text = (
        "<||DSML|| calls><||DSML|| invoke name=\"bash\">"
        "<||DSML|| parameter name=\"command\">echo hi</||DSML|| parameter>"
        "</||DSML|| invoke></||DSML|| calls>"
    )
    call = recover_tool_call(text)
    assert call is not None
    assert json.loads(call["function"]["arguments"]) == {"command": "echo hi", "timeout": 30}


def test_box_drawing_bars_variant():
    text = (
        "<││DSML││ invoke name=\"bash\">"
        "<││DSML││ parameter name=\"command\">dir</││DSML││ parameter>"
        "</││DSML││ invoke>"
    )
    assert recover_tool_call(text) is not None


def test_multiple_invokes_first_wins():
    text = (
        '<｜｜DSML｜｜ invoke name="bash">'
        '<｜｜DSML｜｜ parameter name="command">echo one</｜｜DSML｜｜ parameter>'
        "</｜｜DSML｜｜ invoke>"
        '<｜｜DSML｜｜ invoke name="bash">'
        '<｜｜DSML｜｜ parameter name="command">echo two</｜｜DSML｜｜ parameter>'
        "</｜｜DSML｜｜ invoke>"
    )
    attempts = find_tool_attempts(text)
    assert len(attempts) == 2
    call = recover_tool_call(text)
    assert json.loads(call["function"]["arguments"])["command"] == "echo one"


def test_mixed_long_prose_is_not_executed():
    text = "下面是一个调用示例，仅供讲解：\n" + OBSERVED_DSML + "\n" + ("x" * 200)
    assert recover_tool_call(text) is None
    assert needs_markup_retry(text) is False  # 长正文视为讲解，不打扰模型


def test_unknown_tool_needs_retry():
    text = (
        '<｜｜DSML｜｜ invoke name="delete_all">'
        '<｜｜DSML｜｜ parameter name="path">x</｜｜DSML｜｜ parameter>'
        "</｜｜DSML｜｜ invoke>"
    )
    assert recover_tool_call(text) is None
    assert needs_markup_retry(text) is True


def test_bad_args_needs_retry():
    text = (
        '<｜｜DSML｜｜ invoke name="read_file">'
        '<｜｜DSML｜｜ parameter name="offset">abc</｜｜DSML｜｜ parameter>'
        "</｜｜DSML｜｜ invoke>"
    )
    assert recover_tool_call(text) is None
    assert needs_markup_retry(text) is True


def test_plain_text_has_no_attempts():
    assert find_tool_attempts("文件内容是：hello agent") == []
    assert recover_tool_call("文件内容是：hello agent") is None
    assert needs_markup_retry("文件内容是：hello agent") is False


def test_lenient_xml_recovers_strict_miss():
    text = (
        '<adapter_calls><call name="bash" extra="oops">'
        '<arguments encoding="json"><![CDATA[{"command": "dir"}]]></arguments>'
        "</call></adapter_calls>"
    )
    attempts = find_lenient_xml_calls(text)
    assert len(attempts) == 1
    call = recover_tool_call(text)
    assert call is not None and call["function"]["name"] == "bash"


def test_lenient_xml_rejects_bad_json():
    text = '<call name="bash"><arguments encoding="json">{oops</arguments></call>'
    assert find_lenient_xml_calls(text) == []
    assert needs_markup_retry(text) is False


def test_dsml_without_name_is_ignored():
    text = "<｜｜DSML｜｜ invoke><｜｜DSML｜｜ parameter>hi</｜｜DSML｜｜ parameter></｜｜DSML｜｜ invoke>"
    assert find_dsml_calls(text) == []

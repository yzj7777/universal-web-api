"""uwa-agent 参数修复单测：主攻“工具参数 JSON 错误”失败。"""

from __future__ import annotations

import agent.repair as repair
from agent.repair import (
    clean_path,
    coerce_bash_args,
    coerce_int,
    coerce_read_file_args,
    coerce_tool_args,
    parse_arguments,
    strip_code_fences,
)


def test_strip_code_fences():
    assert strip_code_fences('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_code_fences('```\n{"a": 1}\n```') == '{"a": 1}'
    assert strip_code_fences('{"a": 1}') == '{"a": 1}'
    assert strip_code_fences("") == ""


def test_parse_arguments_plain_and_fenced():
    args, error = parse_arguments('{"path": "a.py", "offset": 2}')
    assert error == "" and args == {"path": "a.py", "offset": 2}
    args, error = parse_arguments('```json\n{"path": "a.py"}\n```')
    assert error == "" and args == {"path": "a.py"}
    args, error = parse_arguments({"path": "a.py"})
    assert error == "" and args == {"path": "a.py"}
    args, error = parse_arguments(None)
    assert error == "" and args == {}


def test_parse_arguments_invalid_json_gives_message():
    args, error = parse_arguments("not json at all {{{")
    assert args is None and "arguments" in error


def test_parse_arguments_trailing_comma_with_repair(monkeypatch):
    if repair._json_repair_loads is None:
        monkeypatch.setattr(repair, "_json_repair_loads", lambda text: {"a": 1}, raising=False)
    args, error = parse_arguments('{"a": 1,}')
    assert error == "" and args == {"a": 1}


def test_parse_arguments_without_repair_library(monkeypatch):
    monkeypatch.setattr(repair, "_json_repair_loads", None, raising=False)
    args, error = parse_arguments('{"a": 1,}')
    assert args is None and error


def test_parse_arguments_rejects_array():
    args, error = parse_arguments('[{"path": "a"}]')
    assert args is None and "数组" in error


def test_coerce_int_variants():
    assert coerce_int("10", field="offset", default=1, minimum=1, maximum=100) == (10, "")
    assert coerce_int(10.0, field="offset", default=1, minimum=1, maximum=100) == (10, "")
    assert coerce_int("30s", field="timeout", default=30, minimum=1, maximum=300) == (30, "")
    assert coerce_int(None, field="timeout", default=30, minimum=1, maximum=300) == (30, "")
    assert coerce_int(9999, field="limit", default=200, minimum=1, maximum=2000)[0] == 2000
    number, error = coerce_int("abc", field="offset", default=1, minimum=1, maximum=100)
    assert number == 1 and "整数" in error
    number, error = coerce_int(True, field="offset", default=1, minimum=1, maximum=100)
    assert number == 1 and "布尔" in error
    number, error = coerce_int(1.5, field="offset", default=1, minimum=1, maximum=100)
    assert number == 1 and "小数" in error


def test_clean_path():
    assert clean_path("  README.md\n") == ("README.md", "")
    assert clean_path('"src/a.py"') == ("src/a.py", "")
    path, error = clean_path("   ")
    assert path == "" and "不能为空" in error
    path, error = clean_path(123)
    assert path == "" and "字符串" in error


def test_coerce_read_file_args_ignores_extra_fields():
    args, error = coerce_read_file_args({"path": "a.py", "offset": "5", "limit": 10.0, "foo": 1})
    assert error == ""
    assert args == {"path": "a.py", "offset": 5, "limit": 10}


def test_coerce_read_file_args_missing_path():
    args, error = coerce_read_file_args({"offset": 1})
    assert args == {} and "path" in error


def test_coerce_bash_args():
    args, error = coerce_bash_args({"command": "  ls  ", "timeout": "10"})
    assert error == "" and args == {"command": "ls", "timeout": 10}
    args, error = coerce_bash_args({"command": "   "})
    assert args == {} and "command" in error
    args, error = coerce_bash_args({"command": "x" * 9000})
    assert args == {} and "过长" in error


def test_coerce_tool_args_unknown_tool():
    args, error = coerce_tool_args("delete_all", "{}")
    assert args == {} and "未知工具" in error


def test_coerce_tool_args_end_to_end_fenced():
    args, error = coerce_tool_args("read_file", '```json\n{"path": "x.py"}\n```')
    assert error == "" and args["path"] == "x.py"

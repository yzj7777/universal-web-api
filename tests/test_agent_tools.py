"""uwa-agent 内置工具单测：read_file / bash / dispatch。"""

from __future__ import annotations

import json
from pathlib import Path

from agent.tools import (
    TOOL_NAMES,
    TOOL_SCHEMAS,
    dispatch,
    example_args,
    execute_bash,
    execute_read_file,
)


def _write(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8")
    return path


def test_tool_schemas_are_flat_and_minimal():
    assert TOOL_NAMES == ["read_file", "bash"]
    assert len(TOOL_SCHEMAS) == 2
    for schema in TOOL_SCHEMAS:
        params = schema["function"]["parameters"]
        assert params["type"] == "object"
        # 扁平、无嵌套：每个属性都必须是 string/integer 标量。
        for name, prop in params["properties"].items():
            assert prop["type"] in ("string", "integer"), name
            assert "properties" not in prop and "items" not in prop
        # 不设 additionalProperties:false，避免弱模型多加字段就整轮失败。
        assert params.get("additionalProperties", True) is not False
    assert set(TOOL_SCHEMAS[0]["function"]["parameters"]["required"]) == {"path"}
    assert set(TOOL_SCHEMAS[1]["function"]["parameters"]["required"]) == {"command"}
    # 示例参数本身必须是合法 JSON。
    assert json.loads(example_args("read_file"))["path"]
    assert json.loads(example_args("bash"))["command"]


def test_read_file_ok_with_numbering(tmp_path):
    _write(tmp_path / "a.py", "x = 1\ny = 2\nz = 3\n")
    result = execute_read_file({"path": "a.py", "offset": 2, "limit": 2}, str(tmp_path))
    assert result.ok
    assert "1|" not in result.text
    assert "2| y = 2" in result.text
    assert "3| z = 3" in result.text
    assert "lines=2-3/3" in result.text


def test_read_file_empty_and_missing(tmp_path):
    _write(tmp_path / "empty.txt", "")
    result = execute_read_file({"path": "empty.txt", "offset": 1, "limit": 10}, str(tmp_path))
    assert result.ok and "空文件" in result.text
    result = execute_read_file({"path": "nope.txt", "offset": 1, "limit": 10}, str(tmp_path))
    assert not result.ok and "不存在" in result.text


def test_read_file_offset_out_of_range(tmp_path):
    _write(tmp_path / "a.txt", "hi\n")
    result = execute_read_file({"path": "a.txt", "offset": 99, "limit": 10}, str(tmp_path))
    assert not result.ok and "超出总行数" in result.text


def test_read_file_sandbox_blocks_escape(tmp_path):
    outside = tmp_path.parent / "outside_secret.txt"
    outside.write_text("secret", encoding="utf-8")
    try:
        result = execute_read_file({"path": "../outside_secret.txt", "offset": 1, "limit": 5}, str(tmp_path))
        assert not result.ok and "工作目录外" in result.text
        allowed = execute_read_file(
            {"path": "../outside_secret.txt", "offset": 1, "limit": 5},
            str(tmp_path), allow_outside=True,
        )
        assert allowed.ok and "secret" in allowed.text
    finally:
        outside.unlink(missing_ok=True)


def test_read_file_refuses_binary(tmp_path):
    (tmp_path / "bin.dat").write_bytes(b"\x00\x01\x02binary")
    result = execute_read_file({"path": "bin.dat", "offset": 1, "limit": 5}, str(tmp_path))
    assert not result.ok and "二进制" in result.text


def test_bash_ok_and_exit_code(tmp_path):
    result = execute_bash({"command": "echo hello", "timeout": 10}, str(tmp_path))
    assert result.ok and "hello" in result.text and "exit=0" in result.text
    result = execute_bash({"command": "exit 3", "timeout": 10}, str(tmp_path))
    assert not result.ok and "exit=3" in result.text


def test_bash_timeout(tmp_path):
    command = "python -c \"import time; time.sleep(30)\""
    result = execute_bash({"command": command, "timeout": 1}, str(tmp_path))
    assert not result.ok and "超时" in result.text


def test_bash_blocks_dangerous_patterns(tmp_path):
    for command in ("rm -rf /", "mkfs.ext4 /dev/sda1", "dd if=/dev/zero of=/dev/sda", "shutdown now"):
        result = execute_bash({"command": command, "timeout": 5}, str(tmp_path))
        assert not result.ok and "拒绝执行" in result.text, command


def test_bash_truncates_huge_output(tmp_path):
    result = execute_bash({"command": "python -c \"print('y' * 100000)\"", "timeout": 10}, str(tmp_path))
    assert "截断" in result.text
    assert len(result.text) < 40000


def test_dispatch_unknown_tool_and_bad_args(tmp_path):
    result = dispatch("rm_file", "{}", str(tmp_path))
    assert not result.ok and "未知工具" in result.text
    result = dispatch("read_file", '{"offset": 1}', str(tmp_path))
    assert not result.ok and "path" in result.text and "示例" in result.text
    result = dispatch("bash", "not json {{{", str(tmp_path))
    assert not result.ok and "arguments" in result.text


def test_dispatch_never_raises(tmp_path):
    result = dispatch("read_file", '{"path": "ok.txt"}', str(tmp_path))
    assert not result.ok  # 文件不存在 → 结构化错误，而非异常
    result = dispatch("", None, str(tmp_path))
    assert not result.ok

"""uwa-agent 主循环单测：用 FakeClient，不依赖真实服务。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from agent.client import BridgeError, ChatResult
from agent.config import AgentConfig
from agent.loop import AgentLoop, compact_history


def _tool_call(name: str, arguments: Any, call_id: str = "call_1") -> Dict[str, Any]:
    import json

    return {
        "id": call_id,
        "type": "function",
        "function": {
            "name": name,
            "arguments": arguments if isinstance(arguments, str) else json.dumps(arguments),
        },
    }


class FakeClient:
    """按脚本返回 ChatResult / 抛 BridgeError，并记录调用。"""

    def __init__(self, script: List[Any]) -> None:
        self.script = list(script)
        self.calls: List[Dict[str, Any]] = []

    def chat(self, messages, *, tools=None, tool_choice="auto",
             parallel_tool_calls=False, stream=False, temperature=0.2, timeout=None):
        self.calls.append({
            "messages": [dict(m) for m in messages],
            "tools": tools,
            "tool_choice": tool_choice,
            "parallel_tool_calls": parallel_tool_calls,
        })
        assert parallel_tool_calls is False, "Agent 必须永远单工具调用"
        if not self.script:
            return ChatResult(content="done", finish_reason="stop")
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _config(tmp_path: Path, **overrides: Any) -> AgentConfig:
    base: Dict[str, Any] = {"workdir": str(tmp_path), "max_steps": 6, "step_delay": 0}
    base.update(overrides)
    return AgentConfig(**base)


def _validation_error() -> BridgeError:
    return BridgeError("校验失败", kind="validation_exhausted", status=500,
                       code="tool_call_validation_exhausted")


# --- compact_history ---

def test_compact_history_truncates_tool_results():
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "task"},
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("bash", {"command": "ls"})]},
        {"role": "tool", "tool_call_id": "call_1", "name": "bash", "content": "x" * 10000},
    ]
    compacted = compact_history(messages, max_chars=60000, max_tool_chars=1000, keep_recent=12)
    assert len(compacted) == 4
    assert len(compacted[3]["content"]) <= 1000
    assert "省略" in compacted[3]["content"]
    # 原历史不被修改。
    assert len(messages[3]["content"]) == 10000


def test_compact_history_sliding_window_keeps_anchors():
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "first"}]
    for index in range(20):
        messages.append({"role": "assistant", "content": f"a{index} " + "y" * 500})
        messages.append({"role": "user", "content": f"u{index}"})
    compacted = compact_history(messages, max_chars=3000, max_tool_chars=6000, keep_recent=4)
    assert compacted[0]["content"] == "sys"
    assert compacted[1]["content"] == "first"
    assert compacted[-1]["content"] == "u19"
    assert len(compacted) <= 2 + 4


# --- run() ---

def test_run_final_answer_immediately(tmp_path):
    events: List[Dict[str, Any]] = []
    loop = AgentLoop(FakeClient([ChatResult(content="hello", finish_reason="stop")]),
                     _config(tmp_path), on_event=events.append)
    result = loop.run("hi")
    assert result.stop_reason == "final"
    assert result.text == "hello"
    assert result.steps == 1 and result.tool_uses == 0
    assert any(e["type"] == "step_start" for e in events)


def test_run_single_tool_then_final(tmp_path):
    (tmp_path / "note.txt").write_text("line1\nline2\n", encoding="utf-8")
    script = [
        ChatResult(content="", tool_calls=[_tool_call("read_file", {"path": "note.txt"})],
                   finish_reason="tool_calls"),
        ChatResult(content="文件有两行", finish_reason="stop"),
    ]
    client = FakeClient(script)
    loop = AgentLoop(client, _config(tmp_path))
    result = loop.run("读 note.txt")
    assert result.stop_reason == "final"
    assert result.tool_uses == 1
    assert "文件有两行" in result.text
    # 历史里应有完整的 assistant tool_calls + tool 结果。
    roles = [m.get("role") for m in result.history]
    assert roles.count("tool") == 1
    tool_message = next(m for m in result.history if m.get("role") == "tool")
    assert "line1" in str(tool_message.get("content"))
    # 发给桥接的请求必须带 2 个工具且关闭并行。
    assert len(client.calls[0]["tools"]) == 2
    assert client.calls[0]["parallel_tool_calls"] is False


def test_run_executes_only_first_tool_call(tmp_path):
    events: List[Dict[str, Any]] = []
    script = [
        ChatResult(content="", tool_calls=[
            _tool_call("bash", {"command": "echo one"}, "call_1"),
            _tool_call("bash", {"command": "echo two"}, "call_2"),
        ], finish_reason="tool_calls"),
        ChatResult(content="ok", finish_reason="stop"),
    ]
    loop = AgentLoop(FakeClient(script), _config(tmp_path), on_event=events.append)
    result = loop.run("task")
    assert result.tool_uses == 1
    assert any(e["type"] == "deferred_tools" for e in events)


def test_run_detects_repeat_loop(tmp_path):
    script = [
        ChatResult(content="", tool_calls=[_tool_call("bash", {"command": "echo x"})],
                   finish_reason="tool_calls")
        for _ in range(5)
    ]
    events: List[Dict[str, Any]] = []
    loop = AgentLoop(FakeClient(script), _config(tmp_path, max_steps=6), on_event=events.append)
    result = loop.run("task")
    assert result.stop_reason == "loop"
    assert result.tool_uses == 2  # 第三次重复被拦截，不再执行
    assert any(e["type"] == "loop_detected" for e in events)


def test_run_recovers_from_validation_exhausted(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.loop.time.sleep", lambda seconds: None)
    script = [
        _validation_error(),
        ChatResult(content="recovered", finish_reason="stop"),
    ]
    events: List[Dict[str, Any]] = []
    client = FakeClient(script)
    loop = AgentLoop(client, _config(tmp_path), on_event=events.append)
    result = loop.run("task")
    assert result.stop_reason == "final"
    assert result.text == "recovered"
    assert len(client.calls) == 2
    # 第二次调用应附带简化修复提醒。
    assert "Tool Repair" in str(client.calls[1]["messages"][-1].get("content"))
    assert any(e["type"] == "retry" for e in events)


def test_run_degrades_to_no_tools_summary(tmp_path):
    # validation_retries=2 → 本轮 3 次 attempt 全挂，随后降级轮成功。
    script = [_validation_error() for _ in range(3)]
    script.append(ChatResult(content="力所能及的总结", finish_reason="stop"))
    client = FakeClient(script)
    loop = AgentLoop(client, _config(tmp_path, validation_retries=2))
    result = loop.run("task")
    assert result.stop_reason == "degraded"
    assert "力所能及的总结" in result.text
    # 最后一次是无工具调用。
    assert client.calls[-1]["tools"] is None
    assert client.calls[-1]["tool_choice"] == "none"


def test_run_retries_transport_errors(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.loop.time.sleep", lambda seconds: None)
    script = [
        BridgeError("busy", kind="rate_limit", status=429, retryable=True),
        ChatResult(content="after retry", finish_reason="stop"),
    ]
    loop = AgentLoop(FakeClient(script), _config(tmp_path, transport_retries=2))
    result = loop.run("task")
    assert result.stop_reason == "final"
    assert result.text == "after retry"


def test_run_fatal_on_auth_error(tmp_path):
    script = [BridgeError("no", kind="auth", status=401)]
    loop = AgentLoop(FakeClient(script), _config(tmp_path))
    result = loop.run("task")
    assert result.stop_reason == "fatal"
    assert "auth" in result.text


def test_run_nudges_on_empty_reply(tmp_path):
    script = [
        ChatResult(content="  ", finish_reason="stop"),
        ChatResult(content="second try", finish_reason="stop"),
    ]
    client = FakeClient(script)
    loop = AgentLoop(client, _config(tmp_path))
    result = loop.run("task")
    assert result.text == "second try"
    assert len(client.calls) == 2
    assert result.steps == 2


def test_run_max_steps(tmp_path):
    script = [
        ChatResult(content="", tool_calls=[_tool_call("bash", {"command": f"echo {i}"}, f"call_{i}")],
                   finish_reason="tool_calls")
        for i in range(10)
    ]
    loop = AgentLoop(FakeClient(script), _config(tmp_path, max_steps=3))
    result = loop.run("task")
    assert result.stop_reason == "max_steps"
    assert result.steps == 3 and result.tool_uses == 3


def test_run_tool_arg_error_feeds_back_as_observation(tmp_path):
    script = [
        ChatResult(content="", tool_calls=[_tool_call("read_file", {"offset": 1}, "call_1")],
                   finish_reason="tool_calls"),
        ChatResult(content="明白了", finish_reason="stop"),
    ]
    loop = AgentLoop(FakeClient(script), _config(tmp_path))
    result = loop.run("task")
    assert result.stop_reason == "final"
    tool_message = next(m for m in result.history if m.get("role") == "tool")
    assert "path" in str(tool_message.get("content"))


def test_run_empty_task_uses_history_only(tmp_path):
    client = FakeClient([ChatResult(content="pong", finish_reason="stop")])
    loop = AgentLoop(client, _config(tmp_path))
    history: List[Dict[str, Any]] = [{"role": "user", "content": "ping"}]
    result = loop.run("", history=history)
    assert result.text == "pong"
    assert history is result.history  # chat 模式原地追加


# --- v0.2：DSML 恢复 / 步间等待 / 限流长退避 ---

DSML_FINAL = (
    "<｜｜DSML｜｜ calls>\n"
    '<｜｜DSML｜｜ invoke name="read_file">\n'
    '<｜｜DSML｜｜ parameter name="path">note.txt</｜｜DSML｜｜ parameter>\n'
    "</｜｜DSML｜｜ invoke>\n"
    "</｜｜DSML｜｜ calls>"
)


def test_run_recovers_dsml_final_and_executes(tmp_path):
    (tmp_path / "note.txt").write_text("recovered-content\n", encoding="utf-8")
    script = [
        ChatResult(content=DSML_FINAL, finish_reason="stop"),
        ChatResult(content="读到了", finish_reason="stop"),
    ]
    events: List[Dict[str, Any]] = []
    client = FakeClient(script)
    loop = AgentLoop(client, _config(tmp_path, step_delay=0), on_event=events.append)
    result = loop.run("task")
    assert result.stop_reason == "final"
    assert result.text == "读到了"
    assert result.tool_uses == 1
    assert any(e["type"] == "recovered_tool_call" and e["tool"] == "read_file" for e in events)
    tool_message = next(m for m in result.history if m.get("role") == "tool")
    assert "recovered-content" in str(tool_message.get("content"))


def test_run_retries_unparsed_markup_once_then_accepts(tmp_path):
    bad_markup = (
        '<｜｜DSML｜｜ invoke name="delete_all">'
        '<｜｜DSML｜｜ parameter name="path">x</｜｜DSML｜｜ parameter>'
        "</｜｜DSML｜｜ invoke>"
    )
    script = [
        ChatResult(content=bad_markup, finish_reason="stop"),
        ChatResult(content="好吧，我直接回答", finish_reason="stop"),
    ]
    client = FakeClient(script)
    loop = AgentLoop(client, _config(tmp_path, step_delay=0))
    result = loop.run("task")
    assert result.text == "好吧，我直接回答"
    assert len(client.calls) == 2
    assert "Format Repair" in str(client.calls[1]["messages"][-1].get("content"))


def test_run_accepts_mixed_prose_with_markup(tmp_path):
    text = "下面是一个示例：" + DSML_FINAL + "x" * 200
    client = FakeClient([ChatResult(content=text, finish_reason="stop")])
    loop = AgentLoop(client, _config(tmp_path, step_delay=0))
    result = loop.run("task")
    assert result.stop_reason == "final"
    assert result.tool_uses == 0
    assert len(client.calls) == 1


def test_run_sleeps_between_steps(tmp_path, monkeypatch):
    sleeps: List[float] = []
    monkeypatch.setattr("agent.loop.time.sleep", sleeps.append)
    script = [
        ChatResult(content="", tool_calls=[_tool_call("bash", {"command": "echo 1"}, "c1")],
                   finish_reason="tool_calls"),
        ChatResult(content="", tool_calls=[_tool_call("bash", {"command": "echo 2"}, "c2")],
                   finish_reason="tool_calls"),
        ChatResult(content="done", finish_reason="stop"),
    ]
    loop = AgentLoop(FakeClient(script), _config(tmp_path, step_delay=2.5))
    result = loop.run("task")
    assert result.stop_reason == "final"
    assert sleeps == [2.5, 2.5]  # 第 2、3 步之前各一次


def test_rate_limit_uses_long_backoff(tmp_path, monkeypatch):
    sleeps: List[float] = []
    monkeypatch.setattr("agent.loop.time.sleep", sleeps.append)
    script = [
        BridgeError("消息发送过于频繁，请稍后重试", kind="rate_limit", status=500,
                    code="x", retryable=True),
        ChatResult(content="recovered", finish_reason="stop"),
    ]
    loop = AgentLoop(FakeClient(script), _config(tmp_path, step_delay=0))
    result = loop.run("task")
    assert result.text == "recovered"
    assert sleeps == [30.0]


def test_rate_limit_honors_retry_after(tmp_path, monkeypatch):
    sleeps: List[float] = []
    monkeypatch.setattr("agent.loop.time.sleep", sleeps.append)
    script = [
        BridgeError("slow", kind="rate_limit", status=429, retryable=True, retry_after=90),
        ChatResult(content="ok", finish_reason="stop"),
    ]
    loop = AgentLoop(FakeClient(script), _config(tmp_path, step_delay=0))
    loop.run("task")
    assert sleeps == [90.0]


def test_rate_limit_fatal_message_is_actionable(tmp_path, monkeypatch):
    monkeypatch.setattr("agent.loop.time.sleep", lambda seconds: None)
    script = [BridgeError("频繁", kind="rate_limit", status=429, retryable=True)] * 5
    loop = AgentLoop(FakeClient(script), _config(tmp_path, step_delay=0, transport_retries=1))
    result = loop.run("task")
    assert result.stop_reason == "fatal"
    assert "限流" in result.text and "--step-delay" in result.text


def test_loop_uses_os_specific_schemas_and_prompt(tmp_path):
    client = FakeClient([ChatResult(content="ok", finish_reason="stop")])
    loop = AgentLoop(client, _config(tmp_path, step_delay=0), os_name="windows")
    loop.run("task")
    tools = client.calls[0]["tools"]
    bash_desc = next(s["function"]["description"] for s in tools
                     if s["function"]["name"] == "bash")
    assert "cmd.exe" in bash_desc
    assert "powershell" in result_history_system(client).lower()


def result_history_system(client: FakeClient) -> str:
    for message in client.calls[0]["messages"]:
        if message.get("role") == "system":
            return str(message.get("content") or "")
    return ""

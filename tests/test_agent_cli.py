"""uwa-agent CLI 单测：参数解析、env 默认、各子命令（mock 网络）。"""

from __future__ import annotations

from typing import Any, List

from agent.cli import EXIT_ERROR, EXIT_OK, EXIT_STOPPED, build_parser, main, make_config
from agent.client import ChatResult


def test_flags_work_before_and_after_subcommand():
    before = build_parser().parse_args(["--server", "http://a:1", "--model", "m1", "run", "hi"])
    assert (before.server, before.model, before.task) == ("http://a:1", "m1", ["hi"])
    after = build_parser().parse_args(["run", "--server", "http://b:2", "--model", "m2", "hi"])
    assert (after.server, after.model, after.task) == ("http://b:2", "m2", ["hi"])
    # 子命令的默认值不覆盖主解析器的值。
    mixed = build_parser().parse_args(["--server", "http://a:1", "run", "hi"])
    assert mixed.server == "http://a:1"


def test_make_config_reads_env(monkeypatch):
    monkeypatch.setenv("UWA_MODEL", "env-model")
    monkeypatch.setenv("UWA_ROUTE", "/tab/2")
    monkeypatch.delenv("UWA_SERVER", raising=False)
    monkeypatch.delenv("UWA_BASE_URL", raising=False)
    args = build_parser().parse_args(["run", "hi"])
    config = make_config(args)
    assert config.model == "env-model"
    assert config.route == "/tab/2"
    assert config.server == "http://127.0.0.1:8199"


class _StubClient:
    def __init__(self, replies: List[Any]) -> None:
        self.replies = list(replies)
        self.resolved = "stub-model"

    def resolve_model(self) -> str:
        return self.resolved

    def chat(self, messages, **kwargs: Any) -> ChatResult:
        item = self.replies.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _patch_client(monkeypatch, replies: List[Any]) -> _StubClient:
    stub = _StubClient(replies)
    monkeypatch.setattr("agent.cli.BridgeClient", lambda config: stub)
    return stub


def test_cmd_tools(capsys, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert main(["tools"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "read_file" in out and "bash" in out


def test_cmd_run_ok_stdout_has_answer(capsys, tmp_path, monkeypatch):
    _patch_client(monkeypatch, [ChatResult(content="final-answer", finish_reason="stop")])
    code = main(["--workdir", str(tmp_path), "run", "hello"])
    assert code == EXIT_OK
    captured = capsys.readouterr()
    assert "final-answer" in captured.out


def test_cmd_run_empty_task_is_error(tmp_path):
    assert main(["--workdir", str(tmp_path), "run"]) == EXIT_ERROR


def test_cmd_run_max_steps_exit_code(tmp_path, monkeypatch):
    import json

    def tool_call(index: int) -> ChatResult:
        return ChatResult(
            content="", finish_reason="tool_calls",
            tool_calls=[{"id": f"call_{index}", "type": "function",
                         "function": {"name": "bash",
                                     "arguments": json.dumps({"command": f"echo {index}"})}}],
        )

    _patch_client(monkeypatch, [tool_call(i) for i in range(5)])
    code = main(["--workdir", str(tmp_path), "--max-steps", "2", "run", "task"])
    assert code == EXIT_STOPPED


def test_cmd_doctor_unreachable_is_error():
    assert main(["--server", "http://127.0.0.1:9", "doctor"]) == EXIT_ERROR


def test_cmd_chat_quit(tmp_path, monkeypatch):
    _patch_client(monkeypatch, [])
    monkeypatch.setattr("builtins.input", lambda prompt="": "/quit")
    assert main(["--workdir", str(tmp_path), "chat"]) == EXIT_OK


def test_cmd_run_from_file(tmp_path, monkeypatch, capsys):
    task_file = tmp_path / "task.txt"
    task_file.write_text("file-task", encoding="utf-8")
    stub = _patch_client(monkeypatch, [ChatResult(content="ok", finish_reason="stop")])
    assert main(["--workdir", str(tmp_path), "run", "--file", str(task_file)]) == EXIT_OK
    assert stub is not None
    capsys.readouterr()

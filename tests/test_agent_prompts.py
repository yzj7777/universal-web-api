"""uwa-agent 提示词单测：按操作系统自适应 shell 方言。"""

from __future__ import annotations

from agent.prompts import (
    RECOVER_REMINDER,
    build_repair_messages,
    build_system_prompt,
    detect_os_name,
)


def test_detect_os_name():
    assert detect_os_name() in ("windows", "posix")


def test_windows_prompt_avoids_unix_commands():
    prompt = build_system_prompt("C:/proj", os_name="windows")
    assert "C:/proj" in prompt
    assert "cmd.exe" in prompt
    assert "powershell" in prompt.lower()
    for forbidden_example in ("ls src", "grep -rn"):
        assert forbidden_example not in prompt


def test_posix_prompt_keeps_bash_examples():
    prompt = build_system_prompt(".", os_name="posix")
    assert "bash" in prompt
    assert "ls" in prompt


def test_unknown_os_falls_back_to_posix():
    assert "bash" in build_system_prompt(".", os_name="plan9")


def test_recover_reminder_mentions_dsml():
    assert "DSML" in RECOVER_REMINDER
    assert "ONE" in RECOVER_REMINDER or "one" in RECOVER_REMINDER


def test_build_repair_messages_does_not_mutate():
    messages = [{"role": "user", "content": "hi"}]
    repaired = build_repair_messages(messages, reminder=RECOVER_REMINDER)
    assert len(messages) == 1
    assert len(repaired) == 2
    assert repaired[-1]["content"] == RECOVER_REMINDER

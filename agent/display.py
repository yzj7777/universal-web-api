"""终端渲染：进度走 stderr，最终答案走 stdout（方便管道）。"""

from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, TextIO


def _supports_color(stream: TextIO) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    return bool(getattr(stream, "isatty", lambda: False)())


class Printer:
    def __init__(self, *, verbose: bool = False, stream: TextIO | None = None) -> None:
        self.verbose = verbose
        self.stream = stream or sys.stderr
        self._color = _supports_color(self.stream)

    def _paint(self, text: str, code: str) -> str:
        if not self._color:
            return text
        return f"\033[{code}m{text}\033[0m"

    def dim(self, text: str) -> str:
        return self._paint(text, "2")

    def green(self, text: str) -> str:
        return self._paint(text, "32")

    def red(self, text: str) -> str:
        return self._paint(text, "31")

    def yellow(self, text: str) -> str:
        return self._paint(text, "33")

    def cyan(self, text: str) -> str:
        return self._paint(text, "36")

    def line(self, text: str = "") -> None:
        print(text, file=self.stream, flush=True)

    @staticmethod
    def preview(text: Any, limit: int = 300) -> str:
        value = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False)
        one_line = " ".join(value.split())
        if len(one_line) <= limit:
            return one_line
        return one_line[: limit - 1] + "…"

    def on_event(self, event: Dict[str, Any]) -> None:
        kind = str(event.get("type") or "")
        step = event.get("step", "?")
        if kind == "step_start":
            max_steps = event.get("max_steps", "?")
            self.line("\n" + self.cyan("── 步骤 " + str(step) + "/" + str(max_steps) + " ──"))
        elif kind == "llm_done":
            self.line(self.dim(
                f"[llm] {float(event.get('elapsed', 0)):0.1f}s "
                f"tool_calls={event.get('tool_calls', 0)} "
                f"content={event.get('content_chars', 0)}ch"
            ))
        elif kind == "tool_start":
            self.line(f"[tool] {event.get('tool')} {self.preview(event.get('arguments'), 200)}")
            if self.verbose:
                self.line(self.dim(f"       args={self.preview(event.get('arguments'), 800)}"))
        elif kind == "tool_done":
            status = self.green("ok") if event.get("ok") else self.red("ERROR")
            self.line(f"       → {status} {float(event.get('elapsed', 0)):0.1f}s "
                      f"{event.get('chars', 0)}ch")
        elif kind == "deferred_tools":
            names = ", ".join(str(n) for n in event.get("names") or [])
            self.line(self.yellow(f"[loop] 本轮只执行首个工具，推迟：{names}"))
        elif kind == "retry":
            self.line(self.yellow(f"[retry] {event.get('reason')} "
                                  f"(step {step}, attempt {event.get('attempt')})"))
        elif kind == "compact":
            self.line(self.dim(f"[loop] 历史压缩 {event.get('before')}→{event.get('after')} 条"))
        elif kind == "degraded":
            self.line(self.yellow(f"[loop] 工具校验持续失败，降级为无工具总结：{event.get('reason')}"))
        elif kind == "loop_detected":
            self.line(self.red(f"[loop] 检测到 {event.get('tool')} 重复调用，已停止"))
        elif kind == "fatal":
            self.line(self.red(f"[fatal] {event.get('error')}"))
        elif self.verbose:
            self.line(self.dim(f"[event] {kind} {self.preview(event)}"))

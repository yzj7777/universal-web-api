"""Agent 主循环：单步单工具 + 预算 + 历史压缩 + 失败恢复。

循环协议（为慢速网页桥接特调）：

1. 每轮只发 ``parallel_tool_calls=false``；若服务端仍返回多个 calls，
   只执行第一个，其余记为 deferred 并提示模型下一轮再调；
2. 非流式调用（桥接的工具流本就是整轮缓冲后一次性下发）；
3. 工具参数校验失败（500 ``tool_call_validation_exhausted``）不直接放弃，
   而是本地追加简化提醒再试 ``validation_retries`` 次；
4. 传输层错误（429/503/超时）退避重试 ``transport_retries`` 次；
5. 全部失败时降级为无工具总结轮，保证用户至少拿到一个回答。

事件通过 ``on_event`` 回调输出（dict，含 ``type`` 字段），CLI 用 display
模块渲染；库调用方可自行订阅。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from agent.client import BridgeClient, BridgeError, ChatResult
from agent.config import AgentConfig
from agent.prompts import (
    CONTINUE_NUDGE,
    DEGRADED_PREFIX,
    LOOP_WARNING,
    build_repair_messages,
    build_system_prompt,
)
from agent.tools import TOOL_SCHEMAS, ToolResult, dispatch

EventCallback = Callable[[Dict[str, Any]], None]


@dataclass
class AgentResult:
    text: str
    steps: int = 0
    tool_uses: int = 0
    stop_reason: str = "final"  # final/degraded/max_steps/loop/wall_time/fatal
    history: List[Dict[str, Any]] = field(default_factory=list)


# --- 历史压缩（纯函数，可单测） ------------------------------------------------

def _message_chars(message: Dict[str, Any]) -> int:
    content = message.get("content", "")
    if isinstance(content, str):
        text = content
    else:
        try:
            text = json.dumps(content, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(content)
    tool_calls = message.get("tool_calls") or []
    extra = 0
    if isinstance(tool_calls, list):
        for item in tool_calls:
            try:
                extra += len(json.dumps(item, ensure_ascii=False))
            except (TypeError, ValueError):
                extra += len(str(item))
    return len(text) + extra


def _middle_trim(text: str, limit: int) -> str:
    if len(text) <= limit or limit <= 0:
        return text
    marker = f"\n…(中间 {len(text) - limit} 字符已省略)…\n"
    if len(marker) >= limit:
        return text[:limit]
    head = int((limit - len(marker)) * 0.7)
    tail = limit - len(marker) - head
    return text[:head] + marker + text[len(text) - tail:]


def compact_history(
    messages: List[Dict[str, Any]],
    *,
    max_chars: int,
    max_tool_chars: int,
    keep_recent: int,
) -> List[Dict[str, Any]]:
    """压缩历史：先截断每条 tool 结果，再按预算做滑动窗口。

    保留：全部 system 消息 + 首条 user 消息 + 最近 keep_recent 条。
    """
    compacted: List[Dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        item = dict(message)
        if str(item.get("role") or "") == "tool" and isinstance(item.get("content"), str):
            item["content"] = _middle_trim(item["content"], max_tool_chars)
        compacted.append(item)

    total = sum(_message_chars(message) for message in compacted)
    if total <= max_chars:
        return compacted

    system = [m for m in compacted if str(m.get("role") or "") == "system"]
    rest = [m for m in compacted if str(m.get("role") or "") != "system"]
    first_user: List[Dict[str, Any]] = []
    for message in rest:
        if str(message.get("role") or "") == "user":
            first_user = [message]
            break
    tail = rest[-keep_recent:] if keep_recent > 0 else []
    # 去重（首条 user 可能已在 tail 中）。
    seen = {id(m) for m in system}
    merged = list(system)
    for message in first_user + tail:
        if id(message) not in seen:
            merged.append(message)
            seen.add(id(message))
    return merged


def _tool_signature(name: str, arguments: Any) -> str:
    try:
        return json.dumps(arguments, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(arguments)


# --- 主循环 ------------------------------------------------------------------

class AgentLoop:
    def __init__(
        self,
        client: BridgeClient,
        config: AgentConfig,
        *,
        on_event: Optional[EventCallback] = None,
        workdir: Optional[str] = None,
    ) -> None:
        self.client = client
        self.config = config
        self.workdir = workdir or config.workdir
        self._emit = on_event or (lambda event: None)

    def _call_with_retry(
        self,
        messages: List[Dict[str, Any]],
        *,
        use_tools: bool,
        step: int,
    ) -> ChatResult:
        """一轮 LLM 调用：传输重试 + 校验失败简化重试。"""
        last_error: Optional[BridgeError] = None
        attempts = 1 + self.config.validation_retries
        for attempt in range(1, attempts + 1):
            payload = messages if attempt == 1 else build_repair_messages(messages)
            if attempt > 1:
                self._emit({"type": "retry", "step": step, "attempt": attempt,
                            "of": attempts, "reason": "validation_exhausted"})
            try:
                return self._call_transport(payload, use_tools=use_tools, step=step)
            except BridgeError as exc:
                last_error = exc
                if exc.kind != "validation_exhausted":
                    raise
                if attempt >= attempts:
                    raise
        assert last_error is not None  # pragma: no cover - 逻辑兜底
        raise last_error

    def _call_transport(
        self,
        messages: List[Dict[str, Any]],
        *,
        use_tools: bool,
        step: int,
    ) -> ChatResult:
        last_error: Optional[BridgeError] = None
        for attempt in range(1 + self.config.transport_retries):
            try:
                started = time.monotonic()
                result = self.client.chat(
                    messages,
                    tools=TOOL_SCHEMAS if use_tools else None,
                    tool_choice="auto" if use_tools else "none",
                    parallel_tool_calls=False,
                    stream=False,
                )
                self._emit({"type": "llm_done", "step": step,
                            "elapsed": time.monotonic() - started,
                            "tool_calls": len(result.tool_calls),
                            "content_chars": len(result.content)})
                return result
            except BridgeError as exc:
                last_error = exc
                if not exc.retryable or attempt >= self.config.transport_retries:
                    raise
                delay = min(2.0 * (2 ** attempt), 20.0)
                self._emit({"type": "retry", "step": step, "attempt": attempt + 1,
                            "reason": f"{exc.kind}:{exc.code or exc.status}",
                            "sleep": delay})
                time.sleep(delay)
        assert last_error is not None  # pragma: no cover - 逻辑兜底
        raise last_error

    def run(self, task: str, history: Optional[List[Dict[str, Any]]] = None) -> AgentResult:
        """执行一个任务。history 可传入 chat 模式的跨轮历史（会被原地追加）。"""
        started = time.monotonic()
        messages: List[Dict[str, Any]] = history if history is not None else []
        if not any(str(m.get("role") or "") == "system" for m in messages if isinstance(m, dict)):
            messages.append({"role": "system", "content": build_system_prompt(self.workdir)})
        task_text = str(task or "").strip()
        if task_text:
            messages.append({"role": "user", "content": task_text})

        tool_uses = 0
        last_signature: Optional[str] = None
        repeat_count = 0
        degraded_text = ""

        for step in range(1, self.config.max_steps + 1):
            if time.monotonic() - started > self.config.max_wall_seconds:
                return AgentResult(degraded_text or "（墙钟预算耗尽，未完成）",
                                   steps=step - 1, tool_uses=tool_uses,
                                   stop_reason="wall_time", history=messages)
            compacted = compact_history(
                messages, max_chars=self.config.max_history_chars,
                max_tool_chars=self.config.max_tool_result_chars,
                keep_recent=self.config.keep_recent_messages,
            )
            if len(compacted) != len(messages):
                self._emit({"type": "compact", "step": step,
                            "before": len(messages), "after": len(compacted)})
            self._emit({"type": "step_start", "step": step, "max_steps": self.config.max_steps})

            try:
                result = self._call_with_retry(compacted, use_tools=True, step=step)
            except BridgeError as exc:
                if exc.kind == "validation_exhausted":
                    # 降级：无工具总结轮，保证至少给用户一个回答。
                    self._emit({"type": "degraded", "step": step, "reason": str(exc)[:200]})
                    degraded = self._degraded_answer(messages)
                    messages.append({"role": "assistant", "content": degraded})
                    return AgentResult(degraded, steps=step, tool_uses=tool_uses,
                                       stop_reason="degraded", history=messages)
                self._emit({"type": "fatal", "step": step, "error": str(exc)[:300]})
                return AgentResult(f"执行失败（{exc.kind}）：{exc}",
                                   steps=step - 1, tool_uses=tool_uses,
                                   stop_reason="fatal", history=messages)

            if not result.has_tool_calls:
                final = result.content.strip()
                if final:
                    messages.append({"role": "assistant", "content": result.content})
                    return AgentResult(result.content, steps=step, tool_uses=tool_uses,
                                       stop_reason="final", history=messages)
                # 空回复：nudge 一次（只在本轮，不污染历史则无法生效，故入历史）。
                self._emit({"type": "retry", "step": step, "reason": "empty_reply"})
                messages.append({"role": "user", "content": CONTINUE_NUDGE})
                continue

            call = result.tool_calls[0]
            if len(result.tool_calls) > 1:
                deferred = [str((c.get("function") or {}).get("name", "?")) for c in result.tool_calls[1:]]
                self._emit({"type": "deferred_tools", "step": step, "names": deferred})
            function = call.get("function") if isinstance(call.get("function"), dict) else {}
            tool_name = str(function.get("name") or "").strip()
            raw_arguments = function.get("arguments", {})
            call_id = str(call.get("id") or f"call_local_{step}")

            signature = f"{tool_name}:{_tool_signature(tool_name, raw_arguments)}"
            if signature == last_signature:
                repeat_count += 1
            else:
                repeat_count = 0
                last_signature = signature
            if repeat_count >= 2:
                self._emit({"type": "loop_detected", "step": step, "tool": tool_name})
                return AgentResult(
                    f"检测到重复调用（{tool_name} 连续 3 次相同参数），已停止以避免空转。"
                    "最后一次工具结果见上文。",
                    steps=step, tool_uses=tool_uses, stop_reason="loop", history=messages,
                )
            if repeat_count == 1:
                messages.append({"role": "assistant", "content": result.content or "",
                                 "tool_calls": result.tool_calls})
                messages.append({"role": "user", "content": LOOP_WARNING})
                # 仍然执行本次调用（第二次重复），警告在下轮生效。

            self._emit({"type": "tool_start", "step": step, "tool": tool_name,
                        "arguments": raw_arguments})
            tool_result: ToolResult = dispatch(
                tool_name, raw_arguments, self.workdir,
                allow_outside_workdir=self.config.allow_outside_workdir,
            )
            tool_uses += 1
            self._emit({"type": "tool_done", "step": step, "tool": tool_name,
                        "ok": tool_result.ok, "elapsed": tool_result.elapsed,
                        "chars": len(tool_result.text)})

            if repeat_count != 1:  # repeat==1 时 assistant 消息已追加
                messages.append({"role": "assistant", "content": result.content or "",
                                 "tool_calls": result.tool_calls})
            messages.append({"role": "tool", "tool_call_id": call_id,
                             "name": tool_name, "content": tool_result.text})

        return AgentResult("达到最大步数仍未收敛，请缩小任务范围或加 --max-steps 重试。",
                           steps=self.config.max_steps, tool_uses=tool_uses,
                           stop_reason="max_steps", history=messages)

    def _degraded_answer(self, messages: List[Dict[str, Any]]) -> str:
        compacted = compact_history(
            [*messages, {"role": "user", "content": DEGRADED_PREFIX}],
            max_chars=self.config.max_history_chars,
            max_tool_chars=self.config.max_tool_result_chars,
            keep_recent=self.config.keep_recent_messages,
        )
        try:
            result = self._call_transport(compacted, use_tools=False, step=0)
            if result.content.strip():
                return result.content
        except BridgeError as exc:
            return f"工具调用持续失败，且降级总结也失败（{exc.kind}）：{exc}"
        return "工具调用持续失败，模型也未返回可用总结。请换站点/预设后重试。"

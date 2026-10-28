"""universal-web-api 的 OpenAI 兼容 HTTP 客户端（仅标准库 urllib）。

刻意不用 requests/httpx：Agent CLI 应该在没装服务端依赖的机器上也能跑。
桥接的工具调用是“整轮缓冲后一次性返回”（见 app/api/chat.py 的 tool_calling
路径），所以主循环默认用非流式；流式只用于 doctor 探针等可选场景。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from agent.config import AgentConfig

# --- 错误分类 ---------------------------------------------------------------

#: 服务端已知的工具校验失败码（app/services/tool_calling_validation_retry.py）。
VALIDATION_EXHAUSTED_CODE = "tool_call_validation_exhausted"

#: 可重试的传输层/服务端状态码。
RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}

#: 可重试的服务端业务码（排队/超时类，换个时机或压缩历史后可能成功）。
RETRYABLE_CODES = {
    "absolute_request_timeout",
    "acquire_timeout",
    "pool_busy",
    "pool_exhausted",
    "queue_full",
    "request_timeout",
    "upstream_timeout",
    "worker_timeout",
}


class BridgeError(Exception):
    """按“Agent 该怎么处理”分类后的桥接错误。"""

    def __init__(
        self,
        message: str,
        *,
        kind: str = "server",
        status: int = 0,
        code: str = "",
        retryable: bool = False,
        raw: str = "",
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.code = code
        self.retryable = retryable
        self.raw = raw


def classify_bridge_error(status: int, payload: Any, raw_text: str = "") -> BridgeError:
    """把 HTTP 状态码 + OpenAI 风格 error body 翻译成 BridgeError。"""
    error = payload.get("error") if isinstance(payload, dict) else None
    if isinstance(error, dict):
        code = str(error.get("code") or "").strip()
        message = str(error.get("message") or "").strip()
        error_type = str(error.get("type") or "").strip()
    else:
        code, message, error_type = "", "", ""
    if not message:
        message = (raw_text or "").strip()[:500] or f"HTTP {status}"

    if status in (401, 403):
        return BridgeError(
            f"认证失败（HTTP {status}）：{message}",
            kind="auth", status=status, code=code or "auth_failed", raw=raw_text,
        )
    if code == VALIDATION_EXHAUSTED_CODE:
        # 重要：这类失败走“本地简化重试”，而不是传输层退避重试。
        return BridgeError(
            f"工具参数校验失败：{message}",
            kind="validation_exhausted", status=status, code=code, raw=raw_text,
        )
    if status == 422 or code in {"prompt_rejected", "arena_prompt_rejected"}:
        return BridgeError(
            f"提示词被拒绝（HTTP {status}）：{message}",
            kind="prompt_rejected", status=status, code=code or "prompt_rejected", raw=raw_text,
        )
    if status == 499 or code in {"manual_terminate", "request_cancelled"}:
        return BridgeError(
            f"请求被取消：{message}", kind="cancelled",
            status=status, code=code or "request_cancelled", raw=raw_text,
        )
    if status == 400 or (400 <= status < 500 and error_type == "invalid_request_error"):
        return BridgeError(
            f"请求非法（HTTP {status}）：{message}",
            kind="bad_request", status=status, code=code or "bad_request", raw=raw_text,
        )
    retryable = status in RETRYABLE_STATUS or code in RETRYABLE_CODES
    kind = "rate_limit" if status == 429 else ("timeout" if code in RETRYABLE_CODES else "server")
    return BridgeError(message, kind=kind, status=status, code=code, retryable=retryable, raw=raw_text)


# --- 响应模型 ---------------------------------------------------------------

@dataclass
class ChatResult:
    content: str = ""
    tool_calls: List[Dict[str, Any]] = field(default_factory=list)
    finish_reason: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


def parse_chat_response(payload: Dict[str, Any]) -> ChatResult:
    """解析非流式 /v1/chat/completions 响应。"""
    choices = payload.get("choices") if isinstance(payload, dict) else None
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        raise BridgeError("桥接返回了空的 choices", kind="empty", code="empty_choices")
    first = choices[0]
    message = first.get("message") if isinstance(first.get("message"), dict) else {}
    content = message.get("content")
    if content is None:
        content = ""
    tool_calls = message.get("tool_calls") or []
    if not isinstance(tool_calls, list):
        tool_calls = []
    return ChatResult(
        content=str(content or ""),
        tool_calls=[item for item in tool_calls if isinstance(item, dict)],
        finish_reason=str(first.get("finish_reason") or ""),
        raw=payload if isinstance(payload, dict) else {},
    )


def parse_sse_stream(text: str) -> ChatResult:
    """解析流式 SSE 全文，合并 content 与 tool_calls 增量。

    桥接的工具调用流是“一次性全量 delta”（见 iter_tool_stream_chunks），
    普通回复是 content 增量；这里两种都兼容，并按 index 合并
    arguments 分片（防御性处理）。
    """
    contents: List[str] = []
    tool_fragments: Dict[int, Dict[str, Any]] = {}
    finish_reason = ""
    for frame in str(text or "").replace("\r\n", "\n").split("\n\n"):
        data_text = ""
        for line in frame.split("\n"):
            stripped = line.strip()
            if stripped.startswith("data:"):
                data_text += stripped[len("data:"):].strip()
        if not data_text or data_text == "[DONE]":
            continue
        try:
            payload = json.loads(data_text)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and "error" in payload:
            raise classify_bridge_error(int(payload.get("status", 500) or 500), payload, data_text)
        choices = payload.get("choices") if isinstance(payload, dict) else None
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            continue
        choice = choices[0]
        if choice.get("finish_reason"):
            finish_reason = str(choice.get("finish_reason") or "")
        delta = choice.get("delta") if isinstance(choice.get("delta"), dict) else {}
        content = delta.get("content")
        if isinstance(content, str) and content:
            contents.append(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    contents.append(part["text"])
        raw_calls = delta.get("tool_calls")
        if isinstance(raw_calls, list):
            for fallback_index, item in enumerate(raw_calls):
                if not isinstance(item, dict):
                    continue
                try:
                    index = int(item.get("index", fallback_index))
                except (TypeError, ValueError):
                    index = fallback_index
                slot = tool_fragments.setdefault(index, {"id": "", "name": "", "arguments": ""})
                if item.get("id"):
                    slot["id"] = str(item.get("id") or "")
                function = item.get("function") if isinstance(item.get("function"), dict) else {}
                if function.get("name"):
                    slot["name"] = str(function.get("name") or "")
                arguments = function.get("arguments")
                if isinstance(arguments, str) and arguments:
                    slot["arguments"] = str(slot["arguments"] or "") + arguments

    tool_calls: List[Dict[str, Any]] = []
    for index in sorted(tool_fragments):
        slot = tool_fragments[index]
        tool_calls.append(
            {
                "id": slot["id"] or f"call_stream_{index}",
                "type": "function",
                "function": {"name": slot["name"], "arguments": slot["arguments"]},
            }
        )
    return ChatResult(content="".join(contents), tool_calls=tool_calls, finish_reason=finish_reason)


# --- 客户端 ------------------------------------------------------------------

class BridgeClient:
    def __init__(self, config: AgentConfig) -> None:
        self.config = config
        self._resolved_model: Optional[str] = None

    # -- 基础请求 --
    def _post_json(self, url: str, body: Dict[str, Any], timeout: float) -> Tuple[int, str]:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json", **self.config.auth_headers}
        request = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return int(response.getcode() or 200), response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8", "replace")
            except Exception:
                raw = ""
            try:
                payload = json.loads(raw) if raw.strip() else {}
            except (TypeError, ValueError):
                payload = {}
            raise classify_bridge_error(int(exc.code or 0), payload, raw) from exc
        except TimeoutError as exc:
            raise BridgeError(f"请求超时（{timeout:g}s）：{url}", kind="timeout", retryable=True) from exc
        except urllib.error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if "timed out" in str(reason).lower():
                raise BridgeError(
                    f"请求超时（{timeout:g}s）：{url}", kind="timeout", retryable=True,
                ) from exc
            raise BridgeError(f"无法连接服务 {url}：{reason}", kind="transport", retryable=True) from exc

    def _get_json(self, url: str, timeout: float) -> Tuple[int, Any, float]:
        headers = dict(self.config.auth_headers)
        request = urllib.request.Request(url, headers=headers, method="GET")
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            try:
                raw = exc.read().decode("utf-8", "replace")
            except Exception:
                raw = ""
            try:
                payload = json.loads(raw) if raw.strip() else {}
            except (TypeError, ValueError):
                payload = {}
            raise classify_bridge_error(int(exc.code or 0), payload, raw) from exc
        except (TimeoutError, urllib.error.URLError) as exc:
            raise BridgeError(f"无法连接服务 {url}：{exc}", kind="transport", retryable=True) from exc
        latency = time.monotonic() - started
        try:
            return int(response.getcode() or 200), json.loads(raw) if raw.strip() else {}, latency
        except (TypeError, ValueError) as exc:
            raise BridgeError(f"服务返回了非 JSON：{raw[:200]}", kind="server") from exc

    # -- 对外接口 --
    def health(self, timeout: float = 15.0) -> Tuple[Dict[str, Any], float]:
        _, payload, latency = self._get_json(self.config.health_url, timeout)
        return payload if isinstance(payload, dict) else {}, latency

    def list_models(self, timeout: float = 30.0) -> List[str]:
        _, payload, _ = self._get_json(self.config.models_url, timeout)
        items = payload.get("data") if isinstance(payload, dict) else None
        models = [
            str(item.get("id") or "").strip()
            for item in items
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ]
        return models

    def resolve_model(self) -> str:
        """model=auto 时从 /v1/models 取第一个可用模型并缓存。"""
        wanted = str(self.config.model or "auto").strip()
        if wanted.lower() != "auto":
            return wanted
        if self._resolved_model:
            return self._resolved_model
        models = self.list_models()
        if not models:
            raise BridgeError("模型列表为空：受控浏览器可能没有可用标签页", kind="server", code="no_models")
        # 桥接在无标签页时会返回占位模型 web-browser；有真实模型时优先选真实的。
        preferred = next((m for m in models if m.lower() != "web-browser"), models[0])
        self._resolved_model = preferred
        return preferred

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        tools: Optional[List[Dict[str, Any]]] = None,
        tool_choice: Any = "auto",
        parallel_tool_calls: bool = False,
        stream: bool = False,
        temperature: float = 0.2,
        timeout: Optional[float] = None,
    ) -> ChatResult:
        body: Dict[str, Any] = {
            "model": self.resolve_model(),
            "messages": messages,
            "stream": bool(stream),
            "temperature": temperature,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = tool_choice
            body["parallel_tool_calls"] = bool(parallel_tool_calls)
        status, raw = self._post_json(self.config.chat_url, body, timeout or self.config.request_timeout)
        if status >= 400:
            try:
                payload: Any = json.loads(raw) if raw.strip() else {}
            except (TypeError, ValueError):
                payload = {}
            raise classify_bridge_error(status, payload, raw)
        if stream:
            return parse_sse_stream(raw)
        try:
            payload = json.loads(raw) if raw.strip() else {}
        except (TypeError, ValueError) as exc:
            raise BridgeError(f"服务返回了非 JSON：{raw[:200]}", kind="server") from exc
        if isinstance(payload, dict) and "error" in payload:
            raise classify_bridge_error(status, payload, raw)
        if not isinstance(payload, dict):
            raise BridgeError("服务返回了非对象响应", kind="server", code="bad_response")
        return parse_chat_response(payload)

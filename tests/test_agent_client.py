"""uwa-agent 桥接客户端单测：错误分类、响应解析、URL 构造。"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from agent.client import (
    BridgeClient,
    BridgeError,
    classify_bridge_error,
    parse_chat_response,
    parse_sse_stream,
)
from agent.config import AgentConfig

# --- classify_bridge_error ---

def test_classify_auth():
    error = classify_bridge_error(401, {"error": {"message": "bad key", "code": "auth"}}, "")
    assert error.kind == "auth" and not error.retryable


def test_classify_validation_exhausted_is_special():
    error = classify_bridge_error(
        500, {"error": {"message": "bad args", "code": "tool_call_validation_exhausted"}}, "")
    assert error.kind == "validation_exhausted"
    assert not error.retryable  # 走本地简化重试，不走传输退避


def test_classify_retryable_codes():
    for status, code in [(429, ""), (503, ""), (500, "absolute_request_timeout"),
                         (500, "pool_busy"), (408, "")]:
        error = classify_bridge_error(status, {"error": {"message": "m", "code": code}}, "")
        assert error.retryable, (status, code)


def test_classify_fatal_codes():
    error = classify_bridge_error(422, {"error": {"message": "no", "code": "prompt_rejected"}}, "")
    assert error.kind == "prompt_rejected" and not error.retryable
    error = classify_bridge_error(499, {"error": {"message": "x", "code": "request_cancelled"}}, "")
    assert error.kind == "cancelled" and not error.retryable
    error = classify_bridge_error(400, {"error": {"message": "bad", "code": "bad_request"}}, "")
    assert error.kind == "bad_request" and not error.retryable


def test_classify_empty_message_falls_back_to_raw():
    error = classify_bridge_error(500, {}, "raw-html-error")
    assert "raw-html-error" in str(error)


# --- parse_chat_response ---

def test_parse_chat_response_text_and_tools():
    payload = {
        "choices": [{
            "finish_reason": "tool_calls",
            "message": {
                "role": "assistant",
                "content": "progress",
                "tool_calls": [{
                    "id": "call_1", "type": "function",
                    "function": {"name": "bash", "arguments": '{"command": "ls"}'},
                }],
            },
        }],
    }
    result = parse_chat_response(payload)
    assert result.content == "progress"
    assert result.finish_reason == "tool_calls"
    assert result.has_tool_calls
    assert result.tool_calls[0]["function"]["name"] == "bash"


def test_parse_chat_response_none_content():
    payload = {"choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": None}}]}
    assert parse_chat_response(payload).content == ""


def test_parse_chat_response_empty_choices():
    with pytest.raises(BridgeError) as exc_info:
        parse_chat_response({"choices": []})
    assert exc_info.value.kind == "empty"


# --- parse_sse_stream ---

def test_parse_sse_content_deltas():
    text = (
        'data: {"choices": [{"delta": {"content": "he"}}]}\n\n'
        ': keepalive\n\n'
        'data: {"choices": [{"delta": {"content": "llo"}}]}\n\n'
        'data: {"choices": [{"delta": {}, "finish_reason": "stop"}]}\n\n'
        "data: [DONE]\n\n"
    )
    result = parse_sse_stream(text)
    assert result.content == "hello"
    assert result.finish_reason == "stop"
    assert not result.has_tool_calls


def test_parse_sse_single_tool_delta():
    frame = {"choices": [{"delta": {
        "tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                        "function": {"name": "bash", "arguments": '{"command": "ls"}'}}],
    }, "finish_reason": None}]}
    result = parse_sse_stream(f"data: {json.dumps(frame)}\n\ndata: [DONE]\n\n")
    assert result.has_tool_calls
    assert result.tool_calls[0]["function"]["arguments"] == '{"command": "ls"}'


def test_parse_sse_merges_argument_fragments():
    frames = [
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "id": "call_1", "function": {"name": "bash", "arguments": '{"comm'}}]}}]},
        {"choices": [{"delta": {"tool_calls": [
            {"index": 0, "function": {"arguments": 'and": "ls"}'}}]}}]},
    ]
    text = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames)
    result = parse_sse_stream(text)
    assert result.tool_calls[0]["function"]["arguments"] == '{"command": "ls"}'


def test_parse_sse_error_frame_raises():
    text = 'data: {"error": {"message": "busy", "code": "pool_busy"}, "status": 503}\n\n'
    with pytest.raises(BridgeError) as exc_info:
        parse_sse_stream(text)
    assert exc_info.value.retryable


# --- AgentConfig URL ---

def test_config_url_building():
    config = AgentConfig(server="http://127.0.0.1:8199/v1/", route="url/gemini.google.com")
    assert config.server == "http://127.0.0.1:8199"
    assert config.route == "/url/gemini.google.com"
    assert config.chat_url == "http://127.0.0.1:8199/url/gemini.google.com/v1/chat/completions"
    assert config.models_url == "http://127.0.0.1:8199/v1/models"
    assert config.health_url == "http://127.0.0.1:8199/health"


def test_config_auth_headers():
    assert AgentConfig(api_key="").auth_headers == {}
    assert AgentConfig(api_key="sk").auth_headers == {"Authorization": "Bearer sk"}


# --- BridgeClient 请求路径（mock urllib） ---

class _FakeResponse:
    def __init__(self, body: bytes, code: int = 200) -> None:
        self._body = body
        self._code = code

    def getcode(self):
        return self._code

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def test_client_chat_posts_openai_body(monkeypatch):
    seen = {}

    def fake_urlopen(request, timeout=None):
        seen["url"] = request.full_url
        seen["body"] = json.loads(request.data.decode("utf-8"))
        seen["timeout"] = timeout
        payload = {"choices": [{"finish_reason": "stop",
                                "message": {"role": "assistant", "content": "hi"}}]}
        return _FakeResponse(json.dumps(payload).encode("utf-8"))

    monkeypatch.setattr("agent.client.urllib.request.urlopen", fake_urlopen)
    client = BridgeClient(AgentConfig(model="m1"))
    result = client.chat([{"role": "user", "content": "hi"}],
                         tools=[{"type": "function"}], tool_choice="auto",
                         parallel_tool_calls=False)
    assert result.content == "hi"
    assert seen["url"].endswith("/v1/chat/completions")
    assert seen["body"]["model"] == "m1"
    assert seen["body"]["parallel_tool_calls"] is False
    assert seen["timeout"] == 300.0


def test_client_chat_maps_http_error(monkeypatch):
    def fake_urlopen(request, timeout=None):
        body = json.dumps({"error": {"message": "bad", "code": "tool_call_validation_exhausted"}})
        raise urllib.error.HTTPError(request.full_url, 500, "err", {}, io.BytesIO(body.encode()))

    monkeypatch.setattr("agent.client.urllib.request.urlopen", fake_urlopen)
    client = BridgeClient(AgentConfig(model="m1"))
    with pytest.raises(BridgeError) as exc_info:
        client.chat([{"role": "user", "content": "hi"}])
    assert exc_info.value.kind == "validation_exhausted"


def test_client_resolve_model_prefers_real_model(monkeypatch):
    client = BridgeClient(AgentConfig(model="auto"))
    monkeypatch.setattr(client, "list_models", lambda timeout=30.0: ["web-browser", "gemini-1"])
    assert client.resolve_model() == "gemini-1"
    # 缓存：第二次不再请求。
    monkeypatch.setattr(client, "list_models", lambda timeout=30.0: (_ for _ in ()).throw(AssertionError))
    assert client.resolve_model() == "gemini-1"


def test_client_resolve_model_empty_raises(monkeypatch):
    client = BridgeClient(AgentConfig(model="auto"))
    monkeypatch.setattr(client, "list_models", lambda timeout=30.0: [])
    with pytest.raises(BridgeError) as exc_info:
        client.resolve_model()
    assert exc_info.value.code == "no_models"

"""Task 5:LLM 客户端测试(httpx MockTransport 模拟 vLLM,不连真实服务)。"""
import json

import httpx
import pytest

from app.llm_client import LLMClient, LLMError

SCHEMA = {"type": "object", "properties": {"intent": {"type": "string"}}}


def _client(handler) -> LLMClient:
    return LLMClient("http://fake/v1", "test-model",
                     transport=httpx.MockTransport(handler))


def test_chat_stream_yields_deltas():
    body = (
        'data: {"choices":[{"delta":{"content":"你好"}}]}\n\n'
        'data: {"choices":[{"delta":{"content":",同学"}}]}\n\n'
        "data: [DONE]\n\n"
    )

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=body,
                              headers={"content-type": "text/event-stream"})

    deltas = list(_client(handler).chat_stream([{"role": "user", "content": "hi"}]))
    assert deltas == ["你好", ",同学"]


def test_chat_stream_http_error_raises_llmerror():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="internal error")

    with pytest.raises(LLMError):
        list(_client(handler).chat_stream([{"role": "user", "content": "hi"}]))


def test_structured_disables_thinking_and_parses():
    """结构化调用必须关思考(guided_json 与 cpsy 思考模板冲突会产出坏 JSON/空内容)。"""
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"intent": "counseling"}'}}]})

    result = _client(handler).structured(
        [{"role": "user", "content": "我很难过"}], SCHEMA)
    assert result == {"intent": "counseling"}
    assert captured["chat_template_kwargs"] == {"enable_thinking": False}
    assert captured["stream"] is False
    assert "guided_json" not in captured  # 此 vLLM+模型组合上不可靠,已弃用


def test_structured_null_content_raises_llmerror():
    """content 为 null(模型把 token 全用去思考)必须按解析失败处理,不得 AttributeError。"""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": None}}]})

    with pytest.raises(LLMError):
        _client(handler).structured([{"role": "user", "content": "hi"}], SCHEMA)


def test_structured_invalid_json_raises_llmerror():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": "这不是JSON"}}]})

    with pytest.raises(LLMError):
        _client(handler).structured([{"role": "user", "content": "hi"}], SCHEMA)


def test_health_ok():
    def ok_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": []})

    assert _client(ok_handler).health() is True

    def fail_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    assert _client(fail_handler).health() is False

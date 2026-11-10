"""vLLM OpenAI 兼容 HTTP 客户端(spec §5.1)。

手写 HTTP + SSE 解析(httpx,API 风格同 requests),不引入 LLM SDK;
结构化输出走 vLLM 的 guided_json 约束解码。
测试通过构造参数 transport 注入 httpx.MockTransport,生产默认 None。
"""
from __future__ import annotations

import json
from typing import Iterator

import httpx


class LLMError(Exception):
    """LLM 调用失败(HTTP 错误/连接错误/结构化输出解析失败)。"""


def _parse_json_loose(content: str) -> dict:
    """解析模型返回的 JSON,容忍代码围栏包裹。

    异常消息不携带模型输出内容(避免对话内容进入日志/traceback)。
    """
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise LLMError(f"结构化输出解析失败: {type(exc).__name__}(内容已隐藏)") from exc
    if not isinstance(parsed, dict):
        raise LLMError("结构化输出不是对象(内容已隐藏)")
    return parsed


class LLMClient:
    def __init__(self, base_url: str, model: str, api_key: str | None = None,
                 timeout: float = 120.0, transport: httpx.BaseTransport | None = None):
        self._base_url = base_url.rstrip("/")
        self._model = model
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self._client = httpx.Client(base_url=self._base_url, headers=headers,
                                    timeout=timeout, transport=transport)

    def _post(self, payload: dict) -> httpx.Response:
        try:
            resp = self._client.post("/chat/completions", json=payload)
        except httpx.HTTPError as exc:
            raise LLMError(f"vLLM 连接失败({self._base_url}): {exc}") from exc
        if resp.status_code != 200:
            raise LLMError(
                f"vLLM 返回 {resp.status_code}: {resp.text[:300]}")
        return resp

    def chat_stream(self, messages: list[dict],
                    temperature: float = 0.7) -> Iterator[str]:
        """流式生成,逐段产出 delta 文本。"""
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
            "max_tokens": 1024,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        try:
            with self._client.stream("POST", "/chat/completions", json=payload) as resp:
                if resp.status_code != 200:
                    raise LLMError(
                        f"vLLM 返回 {resp.status_code}: {resp.text[:300]}")
                for line in resp.iter_lines():
                    if not line.startswith("data:"):
                        continue
                    data = line[len("data:"):].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    try:
                        delta = obj["choices"][0]["delta"].get("content")
                    except (KeyError, IndexError):
                        continue
                    if delta:
                        yield delta
        except LLMError:
            raise
        except httpx.HTTPError as exc:
            raise LLMError(f"vLLM 连接失败({self._base_url}): {exc}") from exc

    def structured(self, messages: list[dict], schema: dict,
                   temperature: float = 0.0) -> dict:
        """非流式结构化输出。

        schema 参数仅作调用类型标识(测试替身据此区分三层);
        生产实现不用 guided_json——它与 cpsy 模型的思考模板冲突,
        实测会产出坏 JSON 或空 content,改为提示词约束 + 关思考 + 温度 0。
        """
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
            "max_tokens": 512,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        resp = self._post(payload)
        try:
            content = resp.json()["choices"][0]["message"]["content"]
        except (KeyError, IndexError, ValueError) as exc:
            raise LLMError(f"vLLM 响应结构异常: {resp.text[:300]}") from exc
        if content is None:
            raise LLMError("结构化输出内容为空(模型未产出内容)")
        return _parse_json_loose(content)

    def health(self) -> bool:
        try:
            resp = self._client.get("/models")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

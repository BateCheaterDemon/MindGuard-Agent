"""三层管线编排(spec §5.2):意图 → RAG → 生成 → 风险 → MCP 副作用。

事件协议(Global Constraints):
  {"event":"delta","text":…}    流式增量
  {"event":"risk_done","risk_level":N|None,"archived":True,"alerted":bool}
  {"event":"done","archived":bool}
  {"event":"error","message":…}

保守降级原则:任何一层失败都按"更安全"的方向处理——
意图失败按心理处理(仍评估风险、仍存档);风险失败进管理员复核队列,
不自动发邮件(防误报);生成失败仍评估风险(防漏报)。
"""
from __future__ import annotations

import asyncio
import threading
from typing import AsyncIterator

from app.config import Config
from app.llm_client import LLMClient, LLMError
from app.prompts import (
    DEFAULT_HIGH_RISK_KEYWORDS,
    build_intent_messages,
    build_rag_messages,
    build_reply_messages,
    build_risk_messages,
    contains_high_risk_keyword,
)
from app.rag import RagIndex, RetrievedChunk
from app.schemas import (
    INTENT_SCHEMA,
    RAG_SCHEMA,
    RISK_SCHEMA,
    AlertPayload,
    Archiver,
    IntentResult,
    RagDecision,
    RiskResult,
)
from app.session_store import SessionStore


class _GenFailed(Exception):
    """生成器内部信号:回复生成失败(async generator 不能 return 值)。"""


class Pipeline:
    def __init__(self, llm: LLMClient, rag: RagIndex | None, store: SessionStore,
                 archiver: Archiver, config: Config):
        self._llm = llm
        self._rag = rag
        self._store = store
        self._archiver = archiver
        self._cfg = config
        self._keywords = config.high_risk_keywords or DEFAULT_HIGH_RISK_KEYWORDS

    # ---------- 内部工具 ----------

    async def _structured(self, messages: list[dict], schema: dict) -> dict:
        """结构化调用,失败重试一次;仍失败抛 LLMError。"""
        last: LLMError | None = None
        for _ in range(2):
            try:
                return await asyncio.to_thread(
                    self._llm.structured, messages, schema, 0.0)
            except LLMError as exc:
                last = exc
        assert last is not None
        raise last

    async def _stream(self, messages: list[dict], temperature: float = 0.7):
        """同步迭代器转异步流(线程+队列),保持首 token 低延迟与真流式。"""
        queue: asyncio.Queue = asyncio.Queue()

        def run() -> None:
            try:
                for text in self._llm.chat_stream(messages, temperature=temperature):
                    queue.put_nowait(("t", text))
                queue.put_nowait(("d", None))
            except LLMError as exc:
                queue.put_nowait(("e", exc))
            except BaseException as exc:  # noqa: BLE001 线程内任何异常都必须回到调用方,否则请求挂死
                queue.put_nowait(("e", LLMError(f"生成线程异常: {exc!r}")))

        threading.Thread(target=run, daemon=True).start()
        while True:
            kind, payload = await queue.get()
            if kind == "t":
                yield payload
            elif kind == "d":
                return
            else:
                raise payload

    async def _generate(self, session_id: str, content: str,
                        kb_context: list[RetrievedChunk] | None) -> AsyncIterator[dict]:
        """流式生成回复;yield delta/error 事件,失败时抛 _GenFailed。"""
        messages = build_reply_messages(
            self._store.recent_messages(session_id, 12), content, kb_context)
        reply_parts: list[str] = []
        try:
            async for text in self._stream(messages):
                reply_parts.append(text)
                yield {"event": "delta", "text": text}
        except LLMError:
            yield {"event": "error", "message": "生成回复失败,请稍后重试"}
            raise _GenFailed()
        if reply_parts:
            self._store.add_message(session_id, "assistant", "".join(reply_parts))

    # ---------- 主流程 ----------

    async def process(self, session_id: str, content: str) -> AsyncIterator[dict]:
        session = self._store.get_session(session_id)
        if session is None:
            yield {"event": "error", "message": "会话不存在或已过期"}
            return

        self._store.add_message(session_id, "user", content)
        self._store.touch_session(session_id)
        forced = contains_high_risk_keyword(content, self._keywords)

        # 第 1 层:意图判断
        intent: IntentResult | None = None
        try:
            raw = await self._structured(
                build_intent_messages(
                    self._store.recent_messages(session_id, 6), content),
                INTENT_SCHEMA)
            if raw.get("intent") in ("casual_chat", "counseling"):
                intent = IntentResult(
                    intent=raw["intent"],
                    emotion=str(raw.get("emotion", "")),
                    brief=str(raw.get("brief", "")),
                )
        except LLMError:
            intent = None  # 保守:按心理流程处理

        if intent is not None and intent.intent == "casual_chat" and not forced:
            try:
                async for event in self._generate(session_id, content, kb_context=None):
                    yield event
            except _GenFailed:
                pass  # 闲聊生成失败:不出风险层、不存档,直接结束
            yield {"event": "done", "archived": False}
            return

        # 第 2 层:RAG 决策 + 检索
        kb_context: list[RetrievedChunk] = []
        try:
            raw = await self._structured(
                build_rag_messages(
                    self._store.recent_messages(session_id, 6), content),
                RAG_SCHEMA)
            rag = RagDecision(need_kb=bool(raw.get("need_kb")),
                              query=str(raw.get("query", "")))
            if rag.need_kb and self._rag is not None and rag.query:
                kb_context = self._rag.search(
                    rag.query, k=self._cfg.rag_top_k,
                    threshold=self._cfg.rag_threshold)
        except LLMError:
            pass  # RAG 失败按不需要知识库处理,不影响对话

        # 生成回复(流式);捕获生成失败标记
        gen_failed = False
        try:
            async for event in self._generate(session_id, content, kb_context):
                yield event
        except _GenFailed:
            gen_failed = True

        # 第 3 层:风险判断(生成失败也要评估,防漏报)
        window = self._store.recent_messages(
            session_id, self._cfg.risk_window_turns)
        reply = "" if gen_failed else self._last_assistant_reply(session_id)
        risk: RiskResult | None = None
        try:
            raw = await self._structured(
                build_risk_messages(window, reply), RISK_SCHEMA)
            level = int(raw["risk_level"])
            if 0 <= level <= 3:
                risk = RiskResult(
                    risk_level=level,
                    risk_type=str(raw.get("risk_type", "")),
                    evidence=str(raw.get("evidence", "")),
                    summary=str(raw.get("summary", "")),
                )
        except (LLMError, KeyError, TypeError, ValueError):
            risk = None

        # MCP 副作用:存档 + (3 级)预警邮件;副作用失败绝不击穿响应流
        record = self._store.build_archive_record(session_id)
        try:
            await self._archiver.write_archive(record)
        except Exception as exc:  # noqa: BLE001
            import logging
            logging.getLogger("mindguard").error(
                "存档写入失败 session=%s: %s", session_id, exc)
            self._store.add_review_item(session_id, "存档写入失败")

        if risk is None:
            self._store.add_review_item(session_id, "风险判定解析失败")
            yield {"event": "risk_done", "risk_level": None,
                   "archived": True, "alerted": False}
        else:
            self._store.record_risk(session_id, risk)
            alerted = False
            if risk.risk_level >= 3:
                alert = AlertPayload(
                    session_id=session_id,
                    student_id=record.student_id,
                    name=record.name,
                    risk_level=risk.risk_level,
                    risk_type=risk.risk_type,
                    evidence=risk.evidence,
                    summary=risk.summary,
                    conversation=record.conversation,
                    created_at=record.updated_at,
                )
                try:
                    sent = await self._archiver.send_alert_email(alert)
                except Exception as exc:  # noqa: BLE001
                    import logging
                    logging.getLogger("mindguard").error(
                        "预警邮件调用异常 session=%s: %s", session_id, exc)
                    sent = False
                if sent:
                    alerted = True
                    self._store.record_alert(session_id, 3, "sent")
                else:
                    self._store.record_alert(session_id, 3, "failed")
                    self._store.add_review_item(session_id, "邮件发送失败")
            yield {"event": "risk_done", "risk_level": risk.risk_level,
                   "archived": True, "alerted": alerted}
        yield {"event": "done", "archived": True}

    def _last_assistant_reply(self, session_id: str) -> str:
        for msg in reversed(self._store.full_conversation(session_id)):
            if msg.role == "assistant":
                return msg.content
        return ""

"""Task 6:三层管线编排测试(FakeLLM/FakeRag/FakeArchiver,不依赖 GPU 与真实服务)。"""
import pytest

from app.config import Config
from app.llm_client import LLMError
from app.pipeline import Pipeline
from app.rag import RetrievedChunk
from app.schemas import INTENT_SCHEMA, RAG_SCHEMA, RISK_SCHEMA
from app.session_store import SessionStore


class FakeLLM:
    """按 JSON Schema 区分三层调用;记录 reply 消息供断言。"""

    def __init__(self, intent=None, rag=None, risk=None,
                 reply_text="这是一段模型回复。",
                 intent_error=False, rag_error=False, risk_error=False,
                 reply_error=False):
        self._intent = intent or {"intent": "counseling", "emotion": "低落", "brief": "x"}
        self._rag = rag or {"need_kb": False, "query": ""}
        self._risk = risk or {"risk_level": 1, "risk_type": "轻微情绪",
                              "evidence": "证据", "summary": "摘要"}
        self._reply = reply_text
        self._intent_error = intent_error
        self._rag_error = rag_error
        self._risk_error = risk_error
        self._reply_error = reply_error
        self.last_reply_messages: list[dict] = []
        self.structured_calls = 0

    def structured(self, messages, schema, temperature=0.0):
        self.structured_calls += 1
        if schema == INTENT_SCHEMA and self._intent_error:
            raise LLMError("intent 失败")
        if schema == RAG_SCHEMA and self._rag_error:
            raise LLMError("rag 失败")
        if schema == RISK_SCHEMA and self._risk_error:
            raise LLMError("risk 失败")
        if schema == INTENT_SCHEMA:
            return dict(self._intent)
        if schema == RAG_SCHEMA:
            return dict(self._rag)
        if schema == RISK_SCHEMA:
            return dict(self._risk)
        raise AssertionError(f"未知 schema 调用: {schema}")

    def chat_stream(self, messages, temperature=0.7):
        self.last_reply_messages = list(messages)
        if self._reply_error:
            raise LLMError("生成失败")
        yield self._reply


class FakeRag:
    def __init__(self, chunks=None):
        self.chunks = chunks or []
        self.queries: list[str] = []

    def search(self, query, k=4, threshold=0.35):
        self.queries.append(query)
        return self.chunks


class FakeArchiver:
    def __init__(self, alert_result=True):
        self.records = []
        self.alerts = []
        self.alert_result = alert_result

    async def write_archive(self, record):
        self.records.append(record)
        return "ok"

    async def send_alert_email(self, alert):
        self.alerts.append(alert)
        return self.alert_result


@pytest.fixture()
def store(tmp_path):
    return SessionStore(tmp_path / "p.db")


def make_pipeline(tmp_path, llm, rag=None, alert_result=True):
    store = SessionStore(tmp_path / "p.db")
    cfg = Config(admin_password="x", alert_email_to="counselor@example.edu")
    return store, Pipeline(llm=llm, rag=rag, store=store,
                           archiver=FakeArchiver(alert_result=alert_result), config=cfg)


async def collect(pipeline, sid, text):
    return [event async for event in pipeline.process(sid, text)]


class _UnexpectedErrorLLM(FakeLLM):
    """chat_stream 抛非 LLMError 异常:线程不得静默死亡导致请求挂死。"""

    def chat_stream(self, messages, temperature=0.7):
        self.last_reply_messages = list(messages)
        raise ValueError("意外的底层异常")


@pytest.mark.asyncio
async def test_generation_unexpected_error_does_not_hang(tmp_path):
    llm = _UnexpectedErrorLLM()
    store, pipeline = make_pipeline(tmp_path, llm)
    sid = store.create_session("20230001", "张三")
    import asyncio
    events = await asyncio.wait_for(collect(pipeline, sid, "我很难过"), timeout=5)
    assert any(e["event"] == "error" for e in events)
    assert events[-1]["event"] == "done"  # 流程正常收尾,连接不挂死


@pytest.mark.asyncio
async def test_casual_chat_skips_archive_and_risk(tmp_path):
    llm = FakeLLM(intent={"intent": "casual_chat", "emotion": "平静", "brief": "寒暄"})
    store, pipeline = make_pipeline(tmp_path, llm)
    sid = store.create_session("20230001", "张三")
    events = await collect(pipeline, sid, "今天天气不错")
    assert [e["event"] for e in events] == ["delta", "done"]
    assert events[-1] == {"event": "done", "archived": False}
    assert pipeline._archiver.records == []
    assert pipeline._archiver.alerts == []


@pytest.mark.asyncio
async def test_keyword_forces_risk_eval(tmp_path):
    llm = FakeLLM(intent={"intent": "casual_chat", "emotion": "平静", "brief": "寒暄"})
    store, pipeline = make_pipeline(tmp_path, llm)
    sid = store.create_session("20230001", "张三")
    events = await collect(pipeline, sid, "唉,我最近不想活了")
    assert any(e["event"] == "risk_done" for e in events)
    assert len(pipeline._archiver.records) == 1


@pytest.mark.asyncio
async def test_counseling_archives_with_risk_done(tmp_path):
    llm = FakeLLM(risk={"risk_level": 2, "risk_type": "持续困扰", "evidence": "e", "summary": "s"})
    store, pipeline = make_pipeline(tmp_path, llm)
    sid = store.create_session("20230001", "张三")
    events = await collect(pipeline, sid, "我觉得活着好累,没有希望")
    risk_done = next(e for e in events if e["event"] == "risk_done")
    assert risk_done == {"event": "risk_done", "risk_level": 2, "archived": True, "alerted": False}
    assert events[-1] == {"event": "done", "archived": True}
    assert len(pipeline._archiver.records) == 1
    assert pipeline._archiver.alerts == []


@pytest.mark.asyncio
async def test_high_risk_sends_alert(tmp_path):
    llm = FakeLLM(risk={"risk_level": 3, "risk_type": "自伤意念", "evidence": "我想跳楼", "summary": "高危"})
    store, pipeline = make_pipeline(tmp_path, llm)
    sid = store.create_session("20230001", "张三")
    events = await collect(pipeline, sid, "我真的不想活了")
    risk_done = next(e for e in events if e["event"] == "risk_done")
    assert risk_done["alerted"] is True
    assert len(pipeline._archiver.alerts) == 1
    alert = pipeline._archiver.alerts[0]
    assert alert.student_id == "20230001"
    assert alert.name == "张三"
    assert alert.evidence == "我想跳楼"
    assert alert.summary == "高危"
    assert "我真的不想活了" in alert.conversation


@pytest.mark.asyncio
async def test_risk_failure_goes_to_review_queue(tmp_path):
    llm = FakeLLM(risk_error=True)
    store, pipeline = make_pipeline(tmp_path, llm)
    sid = store.create_session("20230001", "张三")
    await collect(pipeline, sid, "我很难过")
    assert len(pipeline._archiver.records) == 1
    assert pipeline._archiver.alerts == []
    items = store.list_review_items(pending_only=True)
    assert any("风险判定解析失败" in it["reason"] for it in items)


@pytest.mark.asyncio
async def test_intent_failure_treated_as_counseling(tmp_path):
    llm = FakeLLM(intent_error=True)
    store, pipeline = make_pipeline(tmp_path, llm)
    sid = store.create_session("20230001", "张三")
    events = await collect(pipeline, sid, "随便说点什么")
    assert any(e["event"] == "risk_done" for e in events)
    assert len(pipeline._archiver.records) == 1  # 保守存档


@pytest.mark.asyncio
async def test_alert_email_failure_flags_review(tmp_path):
    llm = FakeLLM(risk={"risk_level": 3, "risk_type": "自伤意念", "evidence": "e", "summary": "s"})
    store, pipeline = make_pipeline(tmp_path, llm, alert_result=False)
    sid = store.create_session("20230001", "张三")
    events = await collect(pipeline, sid, "我不想活了")
    risk_done = next(e for e in events if e["event"] == "risk_done")
    assert risk_done["alerted"] is False
    items = store.list_review_items(pending_only=True)
    assert any("邮件发送失败" in it["reason"] for it in items)


@pytest.mark.asyncio
async def test_generation_failure_still_risks(tmp_path):
    llm = FakeLLM(reply_error=True)
    store, pipeline = make_pipeline(tmp_path, llm)
    sid = store.create_session("20230001", "张三")
    events = await collect(pipeline, sid, "我很难过")
    assert any(e["event"] == "error" for e in events)
    assert any(e["event"] == "risk_done" for e in events)  # 风险层仍执行
    assert len(pipeline._archiver.records) == 1


@pytest.mark.asyncio
async def test_rag_need_kb_injects_context(tmp_path):
    chunks = [
        RetrievedChunk(source="a.md", section="睡眠", category="情绪管理",
                       text="资料A内容", score=0.9),
        RetrievedChunk(source="b.md", section="运动", category="情绪管理",
                       text="资料B内容", score=0.8),
    ]
    llm = FakeLLM(rag={"need_kb": True, "query": "睡不着怎么办"})
    rag = FakeRag(chunks=chunks)
    store, pipeline = make_pipeline(tmp_path, llm, rag=rag)
    sid = store.create_session("20230001", "张三")
    await collect(pipeline, sid, "我最近总是睡不着,该怎么办?")
    assert rag.queries == ["睡不着怎么办"]
    system = llm.last_reply_messages[0]["content"]
    assert "【资料1】" in system and "【资料2】" in system
    assert "资料A内容" in system and "资料B内容" in system
    assert "[1]" in system  # 引用标注要求

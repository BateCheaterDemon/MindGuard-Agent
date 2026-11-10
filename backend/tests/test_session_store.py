"""Task 4:SQLite 会话存储测试。"""
from datetime import datetime, timedelta

import pytest

from app.schemas import RiskResult
from app.session_store import SessionStore


@pytest.fixture()
def store(tmp_path):
    return SessionStore(tmp_path / "test.db")


def test_create_and_get_session(store):
    sid = store.create_session("20230001", "张三")
    session = store.get_session(sid)
    assert session is not None
    assert session["student_id"] == "20230001"
    assert session["name"] == "张三"
    assert store.get_session("不存在") is None


def test_empty_identity_raises(store):
    with pytest.raises(ValueError):
        store.create_session("", "张三")
    with pytest.raises(ValueError):
        store.create_session("20230001", "  ")


def test_recent_messages_returns_last_n(store):
    sid = store.create_session("20230001", "张三")
    for i in range(10):
        store.add_message(sid, "user", f"第{i}条")
    recent = store.recent_messages(sid, turns=3)
    assert len(recent) == 3
    assert [m.content for m in recent] == ["第7条", "第8条", "第9条"]


def test_close_session_builds_archive_record(store):
    sid = store.create_session("20230001", "张三")
    store.add_message(sid, "user", "我最近很难过")
    store.add_message(sid, "assistant", "听起来你最近压力很大,愿意多说一点吗?")
    record = store.close_session(sid)
    assert record.session_id == sid
    assert record.student_id == "20230001"
    assert record.name == "张三"
    assert record.message_count == 2
    assert "我最近很难过" in record.conversation
    assert "压力很大" in record.conversation
    assert record.started_at and record.updated_at


def test_expired_sessions(store):
    now = datetime.now()
    old = store.create_session("20230001", "张三")
    fresh = store.create_session("20230002", "李四")
    closed = store.create_session("20230003", "王五")
    with store._connect() as conn:  # 测试直接改 updated_at 模拟超时
        conn.execute("UPDATE sessions SET updated_at=? WHERE session_id=?",
                     ((now - timedelta(minutes=31)).isoformat(timespec="seconds"), old))
        conn.execute("UPDATE sessions SET updated_at=? WHERE session_id=?",
                     ((now - timedelta(minutes=5)).isoformat(timespec="seconds"), fresh))
    store.close_session(closed)
    with store._connect() as conn:
        conn.execute("UPDATE sessions SET updated_at=? WHERE session_id=?",
                     ((now - timedelta(minutes=40)).isoformat(timespec="seconds"), closed))
    expired = store.expired_sessions(now)
    assert old in expired
    assert fresh not in expired
    assert closed not in expired  # 已关闭的不再算超时


def test_record_risk_updates_max(store):
    sid = store.create_session("20230001", "张三")
    store.record_risk(sid, RiskResult(2, "情绪困扰", "证据", "摘要A"))
    store.record_risk(sid, RiskResult(3, "自伤意念", "证据", "摘要B"))
    session = store.get_session(sid)
    assert session["max_risk_level"] == 3
    assert session["risk_summary"] == "摘要B"
    store.record_risk(sid, RiskResult(1, "轻微", "证据", "摘要C"))
    assert store.get_session(sid)["max_risk_level"] == 3  # 不回退


def test_build_archive_record_snapshot_does_not_close(store):
    """快照存档不关闭会话:后续消息仍可写入,超时判断仍生效。"""
    sid = store.create_session("20230001", "张三")
    store.add_message(sid, "user", "第一条")
    record = store.build_archive_record(sid)
    assert record.message_count == 1
    assert store.get_session(sid)["closed_at"] is None  # 未关闭
    store.add_message(sid, "user", "第二条")
    assert store.build_archive_record(sid).message_count == 2


def test_review_queue_flow(store):
    sid = store.create_session("20230001", "张三")
    store.add_review_item(sid, "风险判定解析失败")
    items = store.list_review_items(pending_only=True)
    assert len(items) == 1
    assert items[0]["reason"] == "风险判定解析失败"
    assert items[0]["status"] == "pending"
    store.resolve_review_item(items[0]["id"], "已人工复核,误报")
    assert store.list_review_items(pending_only=True) == []
    assert len(store.list_review_items(pending_only=False)) == 1

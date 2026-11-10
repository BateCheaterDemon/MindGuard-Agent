"""评审修复回归:过期归档留痕 + 复核队列携带对话证据。"""
from datetime import datetime

import pytest

from app.api import _close_and_archive_expired
from app.session_store import SessionStore


class _RaisingArchiver:
    async def write_archive(self, record):
        raise RuntimeError("磁盘故障")


class _RecordingArchiver:
    def __init__(self):
        self.records = []

    async def write_archive(self, record):
        self.records.append(record)
        return "ok"


@pytest.mark.asyncio
async def test_expiry_archive_failure_goes_to_review(tmp_path, caplog):
    """超时归档失败:必须留痕(日志+复核队列),不得静默丢最终行。"""
    import logging
    store = SessionStore(tmp_path / "d.db", session_timeout_minutes=30)
    sid = store.create_session("20230001", "张三")
    from datetime import timedelta
    old = datetime.now() - timedelta(minutes=40)
    with store._connect() as conn:
        conn.execute("UPDATE sessions SET updated_at=? WHERE session_id=?",
                     (old.isoformat(timespec="seconds"), sid))
    with caplog.at_level(logging.ERROR, logger="mindguard"):
        await _close_and_archive_expired(store, _RaisingArchiver(), datetime.now())
    items = store.list_review_items(pending_only=True)
    assert any("会话结束归档失败" in it["reason"] for it in items)
    assert any("会话结束归档失败" in r.getMessage() for r in caplog.records)


def test_review_items_include_conversation_excerpt(tmp_path):
    """复核队列必须携带对话摘录,人工兜底才能拿到证据。"""
    store = SessionStore(tmp_path / "d.db")
    sid = store.create_session("20230001", "张三")
    store.add_message(sid, "user", "我最近不想活了,很难过")
    store.add_message(sid, "assistant", "我在,愿意多说一点吗?")
    store.add_review_item(sid, "风险判定解析失败")
    items = store.list_review_items(pending_only=True)
    assert "我最近不想活了" in items[0]["conversation_excerpt"]

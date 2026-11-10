"""SQLite 运行库:会话、消息、风险记录、复核队列、预警记录(spec §5.6)。

连接每次操作短开(线程安全);时间统一 ISO 字符串(秒精度)。
"""
from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from app.schemas import ArchiveRecord, Message, RiskResult

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  session_id TEXT PRIMARY KEY,
  student_id TEXT NOT NULL,
  name TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  closed_at TEXT,
  intent TEXT,
  max_risk_level INTEGER,
  risk_summary TEXT,
  alerted INTEGER DEFAULT 0,
  alerted_at TEXT
);
CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  role TEXT NOT NULL,
  content TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_records(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  risk_level INTEGER NOT NULL,
  risk_type TEXT,
  evidence TEXT,
  summary TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS review_queue(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL,
  resolved_at TEXT,
  resolution TEXT
);
CREATE TABLE IF NOT EXISTS alerts(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  session_id TEXT NOT NULL,
  risk_level INTEGER NOT NULL,
  sent_at TEXT NOT NULL,
  status TEXT NOT NULL,
  error TEXT
);
"""


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class SessionStore:
    def __init__(self, db_path: Path, session_timeout_minutes: int = 30):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._timeout = timedelta(minutes=session_timeout_minutes)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    # ---------- 会话 ----------

    def create_session(self, student_id: str, name: str) -> str:
        if not student_id.strip() or not name.strip():
            raise ValueError("学号与姓名不能为空")
        session_id = uuid.uuid4().hex
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO sessions(session_id, student_id, name, created_at, updated_at)"
                " VALUES(?,?,?,?,?)",
                (session_id, student_id.strip(), name.strip(), now, now))
        return session_id

    def get_session(self, session_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
        return dict(row) if row else None

    def touch_session(self, session_id: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET updated_at=? WHERE session_id=?",
                (_now(), session_id))

    def set_intent(self, session_id: str, intent: str) -> None:
        """记录意图判定结果(存档行 intent 列的数据来源)。"""
        with self._connect() as conn:
            conn.execute(
                "UPDATE sessions SET intent=?, updated_at=? WHERE session_id=?",
                (intent, _now(), session_id))

    def build_archive_record(self, session_id: str) -> ArchiveRecord:
        """组装 Excel 行数据的快照(不关闭会话);close_session 在此基础上加关闭标记。"""
        return self._archive_record(session_id)

    def close_session(self, session_id: str) -> ArchiveRecord:
        with self._connect() as conn:
            now = _now()
            conn.execute(
                "UPDATE sessions SET closed_at=?, updated_at=? WHERE session_id=?",
                (now, now, session_id))
        return self._archive_record(session_id)

    def _archive_record(self, session_id: str) -> ArchiveRecord:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE session_id=?", (session_id,)).fetchone()
            messages = conn.execute(
                "SELECT role, content, created_at FROM messages WHERE session_id=?"
                " ORDER BY id", (session_id,)).fetchall()
        if row is None:
            raise ValueError(f"会话不存在: {session_id}")
        role_names = {"user": "用户", "assistant": "助手"}
        conversation = "\n".join(
            f"{role_names.get(m['role'], m['role'])}: {m['content']}" for m in messages)
        return ArchiveRecord(
            session_id=session_id,
            started_at=row["created_at"],
            updated_at=row["updated_at"],
            student_id=row["student_id"],
            name=row["name"],
            message_count=len(messages),
            intent=row["intent"] or "",
            max_risk_level=row["max_risk_level"] or 0,
            risk_summary=row["risk_summary"] or "",
            conversation=conversation,
            alerted=bool(row["alerted"]),
            alerted_at=row["alerted_at"] or "",
        )

    def expired_sessions(self, now: datetime) -> list[str]:
        """updated_at 超过超时阈值且未关闭的会话 id 列表。"""
        cutoff = (now - self._timeout).isoformat(timespec="seconds")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_id FROM sessions WHERE updated_at < ? AND closed_at IS NULL",
                (cutoff,)).fetchall()
        return [r["session_id"] for r in rows]

    # ---------- 消息 ----------

    def add_message(self, session_id: str, role: str, content: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO messages(session_id, role, content, created_at)"
                " VALUES(?,?,?,?)", (session_id, role, content, _now()))

    def recent_messages(self, session_id: str, turns: int = 6) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, created_at FROM messages WHERE session_id=?"
                " ORDER BY id DESC LIMIT ?", (session_id, turns)).fetchall()
        return [Message(r["role"], r["content"], r["created_at"]) for r in reversed(rows)]

    def full_conversation(self, session_id: str) -> list[Message]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT role, content, created_at FROM messages WHERE session_id=?"
                " ORDER BY id", (session_id,)).fetchall()
        return [Message(r["role"], r["content"], r["created_at"]) for r in rows]

    # ---------- 风险与复核 ----------

    def record_risk(self, session_id: str, risk: RiskResult) -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO risk_records(session_id, risk_level, risk_type, evidence, summary, created_at)"
                " VALUES(?,?,?,?,?,?)",
                (session_id, risk.risk_level, risk.risk_type, risk.evidence,
                 risk.summary, now))
            conn.execute(
                "UPDATE sessions SET max_risk_level = MAX(COALESCE(max_risk_level,0), ?),"
                " risk_summary=?, updated_at=? WHERE session_id=?",
                (risk.risk_level, risk.summary, now, session_id))

    def add_review_item(self, session_id: str, reason: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO review_queue(session_id, reason, created_at) VALUES(?,?,?)",
                (session_id, reason, _now()))

    def list_review_items(self, pending_only: bool = True) -> list[dict]:
        """复核队列项,附最近消息摘录(人工兜底拿证据,评审 Issue 5)。"""
        sql = "SELECT * FROM review_queue"
        if pending_only:
            sql += " WHERE status='pending'"
        sql += " ORDER BY id"
        with self._connect() as conn:
            rows = conn.execute(sql).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            with self._connect() as conn:
                msgs = conn.execute(
                    "SELECT role, content FROM messages WHERE session_id=?"
                    " ORDER BY id DESC LIMIT 6", (item["session_id"],)).fetchall()
            excerpt = "\n".join(
                f"{'用户' if m['role'] == 'user' else '助手'}: {m['content']}"
                for m in reversed(msgs))
            item["conversation_excerpt"] = excerpt[:300]
            items.append(item)
        return items

    def resolve_review_item(self, item_id: int, resolution: str) -> None:
        with self._connect() as conn:
            conn.execute(
                "UPDATE review_queue SET status='resolved', resolved_at=?, resolution=?"
                " WHERE id=?", (_now(), resolution, item_id))

    # ---------- 管理端查询 ----------

    def list_sessions(self, date: str | None = None,
                      student_id: str | None = None) -> list[dict]:
        """按日期(updated_at 前缀)与学号筛选会话,按更新时间倒序。"""
        sql = "SELECT * FROM sessions WHERE 1=1"
        params: list = []
        if date:
            sql += " AND updated_at LIKE ?"
            params.append(f"{date}%")
        if student_id:
            sql += " AND student_id LIKE ?"
            params.append(f"%{student_id}%")
        sql += " ORDER BY updated_at DESC LIMIT 500"
        with self._connect() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    def list_alerts(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM alerts ORDER BY id DESC LIMIT 200").fetchall()
        return [dict(r) for r in rows]

    # ---------- 预警 ----------

    def record_alert(self, session_id: str, risk_level: int,
                     status: str, error: str = "") -> None:
        now = _now()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO alerts(session_id, risk_level, sent_at, status, error)"
                " VALUES(?,?,?,?,?)", (session_id, risk_level, now, status, error))
            if status == "sent":
                conn.execute(
                    "UPDATE sessions SET alerted=1, alerted_at=? WHERE session_id=?",
                    (now, session_id))

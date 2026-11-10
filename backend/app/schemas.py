"""共享数据模型与 guided_json 用的 JSON Schema。

三张 Schema 传给 vLLM 的 extra_body={"guided_json": ...} 做约束解码,
字段名与 spec §5.2 一致,任何一层改动都要同步这里的 required 列表。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

INTENT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["casual_chat", "counseling"]},
        "emotion": {"type": "string"},
        "brief": {"type": "string"},
    },
    "required": ["intent", "emotion", "brief"],
}

RAG_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "need_kb": {"type": "boolean"},
        "query": {"type": "string"},
    },
    "required": ["need_kb", "query"],
}

RISK_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "risk_level": {"type": "integer", "minimum": 0, "maximum": 3},
        "risk_type": {"type": "string"},
        "evidence": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["risk_level", "risk_type", "evidence", "summary"],
}


@dataclass
class IntentResult:
    intent: str  # "casual_chat" | "counseling"
    emotion: str
    brief: str


@dataclass
class RagDecision:
    need_kb: bool
    query: str


@dataclass
class RiskResult:
    risk_level: int  # 0-3
    risk_type: str
    evidence: str
    summary: str


@dataclass
class Message:
    role: str  # "user" | "assistant"
    content: str
    created_at: str


@dataclass
class ArchiveRecord:
    """Excel 一行的全部字段,列顺序即字段顺序(与 Global Constraints 的 12 列一致)。"""
    session_id: str
    started_at: str
    updated_at: str
    student_id: str
    name: str
    message_count: int
    intent: str
    max_risk_level: int
    risk_summary: str
    conversation: str
    alerted: bool
    alerted_at: str


@dataclass
class AlertPayload:
    session_id: str
    student_id: str
    name: str
    risk_level: int
    risk_type: str
    evidence: str
    summary: str
    conversation: str
    created_at: str


class Archiver(Protocol):
    """三层管线的副作用出口:由 MCP 工具服务器(McpTools)实现。"""

    async def write_archive(self, record: ArchiveRecord) -> str: ...

    async def send_alert_email(self, alert: AlertPayload) -> bool: ...

#!/usr/bin/env python
"""MCP 工具服务器(FastMCP,stdio):Excel 存档 + 预警邮件。

由后端 McpTools 作为子进程拉起;配置经 MINDGUARD_* 环境变量传入
(与 backend/app/config.py 同一套命名),避免与后端争抢配置文件。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # 仓库根
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

from fastmcp import FastMCP  # noqa: E402

from app.config import SmtpConfig  # noqa: E402
from app.schemas import AlertPayload, ArchiveRecord  # noqa: E402
from email_sender import SmtpError, send_alert  # noqa: E402
from excel_writer import append_archive_row  # noqa: E402

mcp = FastMCP("mindguard-tools")

ARCHIVES_DIR = Path(os.environ.get("MINDGUARD_ARCHIVES_DIR", "data/archives"))
ALERT_EMAIL_TO = os.environ.get("MINDGUARD_ALERT_EMAIL_TO", "")
SMTP = SmtpConfig(
    host=os.environ.get("MINDGUARD_SMTP_HOST", ""),
    port=int(os.environ.get("MINDGUARD_SMTP_PORT", "465")),
    username=os.environ.get("MINDGUARD_SMTP_USERNAME", ""),
    password=os.environ.get("MINDGUARD_SMTP_PASSWORD", ""),
    from_addr=os.environ.get("MINDGUARD_SMTP_FROM", ""),
    use_ssl=os.environ.get("MINDGUARD_SMTP_SSL", "true").lower() != "false",
)


@mcp.tool()
def write_archive(record: dict) -> str:
    """把一条对话存档追加到当日 Excel(按日分文件,自动建表头)。"""
    rec = ArchiveRecord(**record)
    return append_archive_row(rec, ARCHIVES_DIR)


@mcp.tool()
def send_alert_email(payload: dict) -> str:
    """向辅导员发送高风险预警邮件;失败抛错(由调用方进入复核队列)。"""
    if not ALERT_EMAIL_TO:
        raise RuntimeError("未配置收件邮箱(MINDGUARD_ALERT_EMAIL_TO)")
    alert = AlertPayload(**payload)
    try:
        send_alert(SMTP, alert, to_addrs=ALERT_EMAIL_TO)
    except SmtpError as exc:
        raise RuntimeError(str(exc)) from exc
    return "sent"


if __name__ == "__main__":
    mcp.run()

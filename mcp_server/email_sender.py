"""预警邮件:模板渲染 + SMTP 发送(重试 3 次退避,spec §5.4)。"""
from __future__ import annotations

import smtplib
import time
from email.message import EmailMessage

from app.config import SmtpConfig
from app.schemas import AlertPayload


class SmtpError(Exception):
    """邮件发送最终失败(重试耗尽)。"""


def render_alert_email(alert: AlertPayload) -> tuple[str, str]:
    subject = f"【MindGuard 高风险预警】学号 {alert.student_id} {alert.name}"
    body = f"""辅导员您好:

系统检测到一条高风险对话,请及时核实并联系该学生。

- 学生:学号 {alert.student_id} · {alert.name}(身份为学生自填,请核实)
- 时间:{alert.created_at}
- 风险等级:{alert.risk_level}/3(高风险)
- 风险类型:{alert.risk_type}
- 对话证据摘录:{alert.evidence}
- 对话摘要:{alert.summary}

对话记录:
{alert.conversation}

处置建议:请尽快核实情况并主动联系该学生;如需专业支持,
可转介学校心理中心,或拨打心理援助热线 12356。

本邮件由系统自动判定生成,请人工核实后处理。
"""
    return subject, body


def _send_once(smtp: SmtpConfig, alert: AlertPayload, to_addrs: str) -> None:
    subject, body = render_alert_email(alert)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp.from_addr
    msg["To"] = to_addrs
    msg.set_content(body)
    if smtp.use_ssl:
        with smtplib.SMTP_SSL(smtp.host, smtp.port, timeout=30) as server:
            if smtp.username:
                server.login(smtp.username, smtp.password)
            server.send_message(msg)
    else:
        with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as server:
            server.starttls()
            if smtp.username:
                server.login(smtp.username, smtp.password)
            server.send_message(msg)


def send_alert(smtp: SmtpConfig, alert: AlertPayload, to_addrs: str = "") -> None:
    """发送预警邮件;失败重试 3 次(退避 1s/2s),仍失败抛 SmtpError。"""
    last: Exception | None = None
    for attempt in range(3):
        try:
            _send_once(smtp, alert, to_addrs)
            return
        except (OSError, smtplib.SMTPException) as exc:
            last = exc
            if attempt < 2:
                time.sleep(attempt + 1)
    raise SmtpError(f"预警邮件 3 次发送均失败: {last}")

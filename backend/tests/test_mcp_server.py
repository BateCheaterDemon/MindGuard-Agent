"""Task 7:Excel 归档与预警邮件测试(不连真实 SMTP)。"""
import smtplib
import threading

import pytest

from app.config import SmtpConfig
from app.schemas import AlertPayload, ArchiveRecord
from mcp_server.email_sender import SmtpError, render_alert_email, send_alert
from mcp_server.excel_writer import append_archive_row


def make_record(session_id="s1", updated_at="2026-11-10T10:00:00") -> ArchiveRecord:
    return ArchiveRecord(
        session_id=session_id, started_at="2026-11-10T09:00:00",
        updated_at=updated_at, student_id="20230001", name="张三",
        message_count=2, intent="counseling", max_risk_level=3,
        risk_summary="高危摘要", conversation="用户: 我不想活了\n助手: 我在。",
        alerted=False, alerted_at="")


def make_alert() -> AlertPayload:
    return AlertPayload(
        session_id="s1", student_id="20230001", name="张三", risk_level=3,
        risk_type="自伤意念", evidence="我想跳楼", summary="高危",
        conversation="用户: 我不想活了", created_at="2026-11-10T10:00:00")


def test_formula_injection_neutralized(tmp_path):
    """学生可控字段以 = + - @ 开头时,必须存为纯文本而非公式。"""
    import openpyxl
    record = make_record()
    record.student_id = "=1+1"
    record.name = "@cmd"
    record.conversation = "=HYPERLINK(\"http://evil\",\"点我\")"
    path = append_archive_row(record, tmp_path)
    ws = openpyxl.load_workbook(path).active
    assert ws.cell(row=2, column=4).value == "=1+1"
    assert ws.cell(row=2, column=4).data_type == "s"  # 文本,非公式 'f'
    assert ws.cell(row=2, column=5).data_type == "s"
    assert ws.cell(row=2, column=6).data_type == "n"  # 数字列保持数字


def test_new_file_writes_header_and_row(tmp_path):
    path = append_archive_row(make_record(), tmp_path)
    import openpyxl
    wb = openpyxl.load_workbook(path)
    ws = wb.active
    header = [c.value for c in ws[1]]
    assert header == ["会话ID", "开始时间", "更新时间", "学号", "姓名", "消息数",
                      "意图", "最高风险等级", "风险摘要", "完整对话", "是否已预警", "预警时间"]
    assert ws.max_row == 2
    assert ws.cell(row=2, column=4).value == "20230001"
    assert ws.cell(row=2, column=5).value == "张三"


def test_append_twice_keeps_both_rows(tmp_path):
    append_archive_row(make_record(session_id="s1"), tmp_path)
    append_archive_row(make_record(session_id="s2"), tmp_path)
    import openpyxl
    ws = openpyxl.load_workbook(tmp_path / "2026-11-10.xlsx").active
    assert ws.max_row == 3  # 表头 + 2 行
    assert ws.cell(row=2, column=1).value == "s1"
    assert ws.cell(row=3, column=1).value == "s2"


def test_concurrent_appends(tmp_path):
    errors: list[Exception] = []

    def worker(i: int) -> None:
        try:
            append_archive_row(make_record(session_id=f"s{i}"), tmp_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    import openpyxl
    ws = openpyxl.load_workbook(tmp_path / "2026-11-10.xlsx").active
    assert ws.max_row == 9  # 表头 + 8 行全部在,无覆盖


def test_render_alert_email_contains_required_fields():
    subject, body = render_alert_email(make_alert())
    assert "20230001" in subject and "张三" in subject
    assert "请人工核实" in body
    assert "身份为学生自填" in body
    assert "我想跳楼" in body  # 证据摘录
    assert "自伤意念" in body
    assert "12356" in body


class _FakeSMTP:
    """可编程失败次数的 SMTP 替身。"""
    attempts = 0
    fail_times = 0

    def __init__(self, *args, **kwargs):
        type(self).attempts += 1
        if type(self).attempts <= type(self).fail_times:
            raise OSError("connection refused")

    def login(self, *a, **k):
        pass

    def send_message(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_send_alert_retries_then_raises(monkeypatch):
    _FakeSMTP.attempts = 0
    _FakeSMTP.fail_times = 99
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    smtp = SmtpConfig(host="smtp.example.com", port=465, from_addr="a@b.c",
                      use_ssl=True)
    with pytest.raises(SmtpError):
        send_alert(smtp, make_alert())
    assert _FakeSMTP.attempts == 3


def test_send_alert_succeeds_after_retries(monkeypatch):
    _FakeSMTP.attempts = 0
    _FakeSMTP.fail_times = 2
    monkeypatch.setattr(smtplib, "SMTP_SSL", _FakeSMTP)
    smtp = SmtpConfig(host="smtp.example.com", port=465, from_addr="a@b.c",
                      use_ssl=True)
    send_alert(smtp, make_alert())  # 不抛异常即成功
    assert _FakeSMTP.attempts == 3

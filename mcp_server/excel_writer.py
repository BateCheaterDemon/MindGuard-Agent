"""Excel 对话存档:按日分文件,fcntl 文件锁保证并发追加不损坏(spec §5.4)。"""
from __future__ import annotations

import fcntl
from pathlib import Path

from openpyxl import Workbook, load_workbook

from app.schemas import ArchiveRecord

COLUMNS = ["会话ID", "开始时间", "更新时间", "学号", "姓名", "消息数", "意图",
           "最高风险等级", "风险摘要", "完整对话", "是否已预警", "预警时间"]


def _row_values(record: ArchiveRecord) -> list:
    return [record.session_id, record.started_at, record.updated_at,
            record.student_id, record.name, record.message_count, record.intent,
            record.max_risk_level, record.risk_summary, record.conversation,
            "是" if record.alerted else "否", record.alerted_at]


def append_archive_row(record: ArchiveRecord, archives_dir: Path) -> str:
    """按 updated_at 的日期写入当日 xlsx;无则建表头,有则追加。返回文件路径。"""
    archives_dir.mkdir(parents=True, exist_ok=True)
    date = record.updated_at[:10] if record.updated_at else ""
    if not date:
        from datetime import date as _date
        date = _date.today().isoformat()
    path = archives_dir / f"{date}.xlsx"
    lock_path = path.with_suffix(".xlsx.lock")
    with open(lock_path, "w", encoding="utf-8") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if path.exists():
                wb = load_workbook(path)
                ws = wb.active
            else:
                wb = Workbook()
                ws = wb.active
                ws.append(COLUMNS)
            ws.append(_row_values(record))
            wb.save(path)
            wb.close()
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
    return str(path)

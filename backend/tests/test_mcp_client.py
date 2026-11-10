"""Task 7:McpTools 与 MCP 服务器 stdio 全链路测试(启动真实子进程)。"""
from pathlib import Path

import pytest

from app.config import Config
from app.mcp_client import McpTools
from app.schemas import ArchiveRecord

REPO_ROOT = Path(__file__).resolve().parents[2]
SERVER_SCRIPT = REPO_ROOT / "mcp_server" / "server.py"


@pytest.mark.asyncio
async def test_mcp_tools_write_archive_roundtrip(tmp_path):
    cfg = Config(admin_password="x", archives_dir=tmp_path / "archives",
                 alert_email_to="counselor@example.edu")
    tools = McpTools(server_script=SERVER_SCRIPT, config=cfg)
    await tools.start()
    try:
        record = ArchiveRecord(
            session_id="s1", started_at="2026-11-10T09:00:00",
            updated_at="2026-11-10T10:00:00", student_id="20230001", name="张三",
            message_count=1, intent="counseling", max_risk_level=2,
            risk_summary="摘要", conversation="用户: 我很难过", alerted=False,
            alerted_at="")
        result = await tools.write_archive(record)
        # 必须落在配置的 archives 目录,而非子进程 cwd 相对路径(曾致写到 backend/data/)
        assert Path(result).parent == tmp_path / "archives"
        assert Path(result).exists()
    finally:
        await tools.close()

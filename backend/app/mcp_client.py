"""MCP 工具客户端:后端侧实现 Archiver 协议,经 stdio 调用 MCP 服务器。"""
from __future__ import annotations

import os
from dataclasses import asdict
from pathlib import Path

from app.config import Config
from app.schemas import AlertPayload, ArchiveRecord


class McpTools:
    """实现 Archiver(异步);配置经环境变量传给子进程服务器。"""

    def __init__(self, server_script: Path, config: Config):
        self._script = Path(server_script)
        self._cfg = config
        self._client = None
        self._ctx = None

    async def start(self) -> None:
        """启动并连接 MCP 服务器子进程;失败抛 RuntimeError。

        fastmcp 对子进程使用消毒环境(不继承 os.environ),
        因此配置必须显式放入 env;cwd 固定为仓库根,保证
        server.py 里的相对默认路径(data/archives)落在正确位置。
        """
        server_env = dict(os.environ)
        server_env.update({
            "PYTHONUTF8": "1",
            "MINDGUARD_ARCHIVES_DIR": str(self._cfg.archives_dir),
            "MINDGUARD_ALERT_EMAIL_TO": self._cfg.alert_email_to,
            "MINDGUARD_SMTP_HOST": self._cfg.smtp.host,
            "MINDGUARD_SMTP_PORT": str(self._cfg.smtp.port),
            "MINDGUARD_SMTP_USERNAME": self._cfg.smtp.username,
            "MINDGUARD_SMTP_PASSWORD": self._cfg.smtp.password,
            "MINDGUARD_SMTP_FROM": self._cfg.smtp.from_addr,
            "MINDGUARD_SMTP_SSL": "true" if self._cfg.smtp.use_ssl else "false",
        })
        try:
            from fastmcp import Client
            from fastmcp.client.transports import PythonStdioTransport
            transport = PythonStdioTransport(
                script_path=self._script,
                env=server_env,
                cwd=str(Path(__file__).resolve().parents[2]),  # 仓库根
            )
            self._client = Client(transport)
            self._ctx = self._client.__aenter__()
            await self._ctx
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"MCP 工具服务器启动失败: {exc}") from exc

    async def close(self) -> None:
        if self._client is not None:
            await self._client.__aexit__(None, None, None)
            self._client = None

    async def _call(self, name: str, **kwargs) -> str:
        if self._client is None:
            raise RuntimeError("MCP 工具服务器未启动")
        result = await self._client.call_tool(name, kwargs)
        if result.is_error:
            raise RuntimeError(f"MCP 工具 {name} 失败: {result.content}")
        return result.content[0].text if result.content else ""

    async def write_archive(self, record: ArchiveRecord) -> str:
        return await self._call("write_archive", record=asdict(record))

    async def send_alert_email(self, alert: AlertPayload) -> bool:
        """发送预警邮件;任何失败(工具报错/协议异常/网络)一律视为发送失败,
        由管线进入复核队列——绝不让异常击穿 SSE 响应流。"""
        try:
            await self._call("send_alert_email", payload=asdict(alert))
            return True
        except Exception:  # noqa: BLE001
            return False

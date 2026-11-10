"""FastAPI 入口。

Task 1 只提供最小应用与健康检查;Task 8 会扩展 create_app 为真实装配
(Pipeline / LLMClient / RagIndex / SessionStore / McpTools / 静态托管)。
"""
from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="MindGuard")

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True}

    return app

"""FastAPI 入口与真实装配。

- create_app() 无参:最小应用(Task 1 契约,仅 /api/health)
- create_app(config) 全量装配:LLMClient / RagIndex / SessionStore /
  McpTools / Pipeline + 管理端 + 前端静态托管 + 日志
"""
from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.config import PROJECT_ROOT, Config, load_config


def _setup_logging(config: Config) -> None:
    config.log_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        config.log_dir / "backend.log", maxBytes=2 * 1024 * 1024,
        backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("mindguard").addHandler(handler)
    logging.getLogger("mindguard").setLevel(logging.INFO)


def create_app(config: Config | None = None) -> FastAPI:
    if config is None:
        # Task 1 契约:最小应用
        app = FastAPI(title="MindGuard")

        @app.get("/api/health")
        def health() -> dict:
            return {"ok": True}

        return app

    _setup_logging(config)
    log = logging.getLogger("mindguard")

    from app.api import create_app as build_api
    from app.llm_client import LLMClient
    from app.mcp_client import McpTools
    from app.pipeline import Pipeline
    from app.session_store import SessionStore

    llm = LLMClient(config.vllm_base_url, config.model_name)

    rag = None
    if config.chroma_dir.exists():
        try:
            from app.rag import RagIndex, TransformersEmbedder
            rag = RagIndex(
                config.chroma_dir,
                TransformersEmbedder(config.embedding_model, config.embedding_device))
        except Exception as exc:  # noqa: BLE001 知识库缺失不阻塞主流程
            log.warning("Chroma 加载失败,RAG 降级为直答: %s", exc)

    store = SessionStore(config.db_path, config.session_timeout_minutes)
    tools = McpTools(PROJECT_ROOT / "mcp_server" / "server.py", config)
    pipeline = Pipeline(llm, rag, store, tools, config)
    probes = {
        "vllm": llm.health,
        "chroma": lambda: rag is not None,
        "mcp": lambda: tools._client is not None,
    }
    app = build_api(config, pipeline, store, probes=probes, tools=tools)
    app.mount("/", StaticFiles(directory=str(config.frontend_dir), html=True),
              name="static")
    return app


if __name__ == "__main__":
    import uvicorn

    cfg = load_config()
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port)

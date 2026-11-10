"""API 路由:会话 / 聊天 SSE / 健康检查 / 管理端(spec §5.5)。"""
from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import StreamingResponse

from app.config import Config
from app.security import (
    check_admin_password,
    clear_all_tokens,
    issue_admin_token,
    rate_limit_middleware,
    require_admin,
)
from app.session_store import SessionStore

Probe = Callable[[], bool]


def create_app(config: Config, pipeline, store: SessionStore,
               probes: dict[str, Probe] | None = None,
               tools=None) -> FastAPI:
    app = FastAPI(title="MindGuard")
    app.state.rate_limiter = None
    if config.rate_limit_per_minute > 0:
        from app.security import RateLimiter
        app.state.rate_limiter = RateLimiter({
            "/api/chat": config.rate_limit_per_minute,
            "/api/sessions": config.rate_limit_per_minute,
            "/api/admin/login": 5,  # 防密码爆破
        })
    app.middleware("http")(rate_limit_middleware)

    # ---------- 生命周期:启动 MCP 工具服务器 + 会话超时归档 ----------

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if tools is not None:
            await tools.start()
        loop_task: asyncio.Task | None = None
        if tools is not None:
            loop_task = asyncio.create_task(
                _expiry_loop(store, tools, config.session_timeout_minutes))
        try:
            yield
        finally:
            if loop_task is not None:
                loop_task.cancel()
            if tools is not None:
                await tools.close()
            clear_all_tokens()

    app.router.lifespan_context = lifespan

    # ---------- 学生端 ----------

    @app.post("/api/sessions")
    async def create_session(body: dict) -> dict:
        student_id = str(body.get("student_id", "")).strip()
        name = str(body.get("name", "")).strip()
        if not student_id or not name:
            raise HTTPException(status_code=400, detail="学号与姓名不能为空")
        session_id = await asyncio.to_thread(
            store.create_session, student_id, name)
        return {"session_id": session_id}

    @app.post("/api/chat")
    async def chat(body: dict) -> StreamingResponse:
        session_id = str(body.get("session_id", ""))
        message = str(body.get("message", "")).strip()
        if not message:
            raise HTTPException(status_code=400, detail="消息不能为空")
        if store.get_session(session_id) is None:
            raise HTTPException(status_code=400, detail="会话不存在或已过期")
        return build_chat_stream(pipeline, session_id, message)

    @app.get("/api/health")
    async def health() -> dict:
        probe_map = probes or {}
        return {
            "ok": True,
            "vllm": probe_map.get("vllm", lambda: False)(),
            "chroma": probe_map.get("chroma", lambda: False)(),
            "mcp": probe_map.get("mcp", lambda: False)(),
        }

    # ---------- 管理端 ----------

    @app.post("/api/admin/login")
    async def admin_login(body: dict, request: Request, response: Response) -> dict:
        ip = request.client.host if request.client else "unknown"
        if not check_admin_password(str(body.get("password", "")), config):
            import logging
            logging.getLogger("mindguard").warning(
                "管理员登录失败 ip=%s", ip)
            raise HTTPException(status_code=401, detail="密码错误")
        token = issue_admin_token()
        response.set_cookie("mg_admin_token", token, httponly=True,
                            samesite="lax", max_age=12 * 3600,
                            secure=config.cookie_secure)
        return {"ok": True}

    @app.post("/api/admin/logout", dependencies=[Depends(require_admin)])
    async def admin_logout(response: Response) -> dict:
        response.delete_cookie("mg_admin_token")
        return {"ok": True}

    @app.get("/api/admin/archives", dependencies=[Depends(require_admin)])
    async def admin_archives(date: str = "", student_id: str = "") -> dict:
        rows = await asyncio.to_thread(
            store.list_sessions, date or None, student_id or None)
        return {"sessions": rows}

    @app.get("/api/admin/review", dependencies=[Depends(require_admin)])
    async def admin_review() -> dict:
        items = await asyncio.to_thread(store.list_review_items, False)
        return {"items": items}

    @app.post("/api/admin/review/{item_id}/resolve",
              dependencies=[Depends(require_admin)])
    async def admin_resolve_review(item_id: int, body: dict) -> dict:
        await asyncio.to_thread(
            store.resolve_review_item, item_id, str(body.get("resolution", "")))
        return {"ok": True}

    @app.get("/api/admin/alerts", dependencies=[Depends(require_admin)])
    async def admin_alerts() -> dict:
        return {"alerts": await asyncio.to_thread(store.list_alerts)}

    @app.get("/api/admin/config", dependencies=[Depends(require_admin)])
    async def admin_get_config() -> dict:
        return {
            "alert_email_to": config.alert_email_to,
            "smtp_host": config.smtp.host,
            "smtp_port": config.smtp.port,
            "smtp_username": config.smtp.username,
            "smtp_from": config.smtp.from_addr,
            "smtp_use_ssl": config.smtp.use_ssl,
            "rag_top_k": config.rag_top_k,
            "rag_threshold": config.rag_threshold,
        }

    @app.post("/api/admin/config", dependencies=[Depends(require_admin)])
    async def admin_set_config(body: dict) -> dict:
        # 凭据/收件邮箱等敏感字段不可运行时修改(防 SSRF 与凭据外泄),
        # 只能经环境变量/配置文件修改并重启;仅非敏感检索参数可在线调整。
        mutable = {"rag_top_k", "rag_threshold"}
        immutable = sorted(set(body) - mutable)
        if immutable:
            raise HTTPException(
                status_code=400,
                detail=f"以下字段需通过环境变量/配置文件修改并重启: {immutable}")
        if "rag_top_k" in body:
            config.rag_top_k = int(body["rag_top_k"])
        if "rag_threshold" in body:
            config.rag_threshold = float(body["rag_threshold"])
        return {"ok": True}

    # 前端静态托管:必须在所有 API 路由之后挂载,否则 Mount("/") 会遮蔽后续路由
    from fastapi.staticfiles import StaticFiles
    if config.frontend_dir.exists():
        app.mount("/", StaticFiles(directory=str(config.frontend_dir), html=True),
                  name="static")

    return app


def build_chat_stream(pipeline, session_id: str, message: str) -> StreamingResponse:
    """SSE 响应:管线由独立 worker task 驱动,经队列中转。

    客户端断开只会关闭本响应生成器,worker 继续跑完风险层与存档——
    "发完高危消息就关页面"不能成为漏报路径(评审 Issue 3)。
    """
    import logging
    queue: asyncio.Queue = asyncio.Queue()

    async def drain() -> None:
        try:
            async for event in pipeline.process(session_id, message):
                await queue.put(event)
        except Exception as exc:  # noqa: BLE001 最后防线:异常必须可见,不静默断连
            logging.getLogger("mindguard").exception(
                "chat 流异常 session=%s", session_id)
            await queue.put({"event": "error", "message": "系统内部错误,请稍后重试"})
        finally:
            await queue.put(None)  # 结束哨兵

    worker = asyncio.create_task(drain())

    async def gen():
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield f"event: {event['event']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
        finally:
            pass  # 断连时不取消 worker:风险+存档必须完成

    return StreamingResponse(gen(), media_type="text/event-stream")


async def _close_and_archive_expired(store: SessionStore, archiver,
                                     now: datetime) -> None:
    """关闭超时会话并写最终归档行;失败必须留痕,不得静默丢最终行(评审 Issue 4)。"""
    import logging
    log = logging.getLogger("mindguard")
    for sid in await asyncio.to_thread(store.expired_sessions, now):
        try:
            record = await asyncio.to_thread(store.close_session, sid)
            await archiver.write_archive(record)
        except Exception as exc:  # noqa: BLE001
            log.error("会话结束归档失败 session=%s: %s", sid, exc)
            try:
                await asyncio.to_thread(
                    store.add_review_item, sid, "会话结束归档失败")
            except Exception:  # noqa: BLE001 复核写入失败不再处理
                pass


async def _expiry_loop(store: SessionStore, archiver,
                       timeout_minutes: int) -> None:
    """每 60 秒关闭超时会话并写最终归档行。"""
    while True:
        try:
            await _close_and_archive_expired(store, archiver, datetime.now())
        except Exception:  # noqa: BLE001 循环必须存活
            pass
        await asyncio.sleep(60)

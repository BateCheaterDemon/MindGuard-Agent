"""管理端安全:密码校验、token 签发/校验、限流中间件(spec §5.5/§6)。"""
from __future__ import annotations

import hmac
import secrets
import threading
import time
from collections import deque

from fastapi import HTTPException, Request

from app.config import Config

_TOKEN_TTL_SECONDS = 12 * 3600
_tokens: dict[str, float] = {}
_tokens_lock = threading.Lock()


def check_admin_password(plain: str, cfg: Config) -> bool:
    return hmac.compare_digest(plain, cfg.admin_password)


def issue_admin_token() -> str:
    token = secrets.token_urlsafe(32)
    with _tokens_lock:
        _tokens[token] = time.time() + _TOKEN_TTL_SECONDS
    return token


def verify_admin_token(token: str | None) -> bool:
    if not token:
        return False
    with _tokens_lock:
        expires = _tokens.get(token)
        if expires is None:
            return False
        if time.time() > expires:
            _tokens.pop(token, None)
            return False
    return True


def clear_all_tokens() -> None:
    """测试与重启时清空。"""
    with _tokens_lock:
        _tokens.clear()


def require_admin(request: Request) -> None:
    if not verify_admin_token(request.cookies.get("mg_admin_token")):
        raise HTTPException(status_code=401, detail="未登录或登录已过期")


class RateLimiter:
    """滑动窗口限流:按路径配置每 IP 每分钟上限;未配置的路径不限。"""

    def __init__(self, budgets: dict[str, int]):
        self._budgets = budgets
        self._hits: dict[tuple[str, str], deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, ip: str, path: str) -> bool:
        limit = self._budgets.get(path)
        if limit is None:
            return True
        now = time.monotonic()
        key = (ip, path)
        with self._lock:
            dq = self._hits.setdefault(key, deque())
            while dq and now - dq[0] > 60:
                dq.popleft()
            if len(dq) >= limit:
                return False
            dq.append(now)
            return True


async def rate_limit_middleware(request: Request, call_next):
    """按路径限流(额度表在创建 app 时注入)。"""
    limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)
    if limiter is not None:
        ip = request.client.host if request.client else "unknown"
        if not limiter.check(ip, request.url.path):
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=429, content={"detail": "请求过于频繁,请稍后再试"})
    return await call_next(request)

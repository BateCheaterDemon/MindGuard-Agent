"""Task 8:API 层与管理端安全测试(TestClient + FakePipeline + 真实 SessionStore)。"""
import pytest
from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Config
from app.session_store import SessionStore


class FakePipeline:
    def __init__(self, events=None):
        self.events = events or [
            {"event": "delta", "text": "你好"},
            {"event": "done", "archived": False},
        ]
        self.calls = []

    async def process(self, session_id, content):
        self.calls.append((session_id, content))
        for event in self.events:
            yield event


@pytest.fixture()
def store(tmp_path):
    return SessionStore(tmp_path / "api.db")


def make_app(store, pipeline=None, probes=None, rate_limit=3):
    cfg = Config(admin_password="secret123", rate_limit_per_minute=rate_limit,
                 alert_email_to="c@example.edu")
    return create_app(cfg, pipeline or FakePipeline(), store, probes=probes)


def test_create_session_ok_and_empty_rejected(store):
    client = TestClient(make_app(store))
    resp = client.post("/api/sessions", json={"student_id": "20230001", "name": "张三"})
    assert resp.status_code == 200
    assert resp.json()["session_id"]
    assert client.post("/api/sessions", json={"student_id": "", "name": "张三"}).status_code == 400
    assert client.post("/api/sessions", json={"student_id": "20230001", "name": "  "}).status_code == 400


def test_chat_sse_protocol(store):
    client = TestClient(make_app(store))
    sid = client.post("/api/sessions", json={"student_id": "20230001", "name": "张三"}).json()["session_id"]
    resp = client.post("/api/chat", json={"session_id": sid, "message": "你好"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    assert "event: delta" in resp.text
    assert 'data: {"event": "delta", "text": "你好"}' in resp.text
    assert "event: done" in resp.text
    # 未知会话 400
    assert client.post("/api/chat", json={"session_id": "不存在", "message": "x"}).status_code == 400


def test_chat_rate_limited(store):
    client = TestClient(make_app(store, rate_limit=3))
    sid = client.post("/api/sessions", json={"student_id": "20230001", "name": "张三"}).json()["session_id"]
    codes = [client.post("/api/chat", json={"session_id": sid, "message": f"m{i}"}).status_code
             for i in range(4)]
    assert codes[:3] == [200, 200, 200]
    assert codes[3] == 429


def test_admin_login_and_guard(store):
    client = TestClient(make_app(store))
    assert client.post("/api/admin/login", json={"password": "wrong"}).status_code == 401
    resp = client.post("/api/admin/login", json={"password": "secret123"})
    assert resp.status_code == 200
    assert "mg_admin_token" in resp.headers.get("set-cookie", "")
    assert client.get("/api/admin/review").status_code == 200
    fresh = TestClient(make_app(store))
    assert fresh.get("/api/admin/review").status_code == 401


def test_admin_archives_lists_sessions(store):
    client = TestClient(make_app(store))
    sid = client.post("/api/sessions", json={"student_id": "20230001", "name": "张三"}).json()["session_id"]
    store.add_message(sid, "user", "我很难过")
    client.post("/api/admin/login", json={"password": "secret123"})
    resp = client.get("/api/admin/archives", params={"student_id": "20230001"})
    assert resp.status_code == 200
    sessions = resp.json()["sessions"]
    assert any(s["session_id"] == sid and s["name"] == "张三" for s in sessions)
    assert client.get("/api/admin/archives", params={"student_id": "999"}).json()["sessions"] == []


def test_admin_login_rate_limited(store):
    client = TestClient(make_app(store, rate_limit=3))
    codes = [client.post("/api/admin/login", json={"password": "wrong"}).status_code
             for _ in range(7)]
    assert codes[:5] == [401] * 5  # 每 IP 每分钟最多 5 次尝试
    assert codes[5] == 429


def test_admin_config_roundtrip(store):
    client = TestClient(make_app(store))
    client.post("/api/admin/login", json={"password": "secret123"})
    resp = client.get("/api/admin/config")
    assert resp.status_code == 200
    assert resp.json()["alert_email_to"] == "c@example.edu"
    # 敏感/凭据类字段不可运行时修改:拒绝并提示走环境变量/配置文件
    resp = client.post("/api/admin/config", json={"alert_email_to": "new@example.edu"})
    assert resp.status_code == 400
    resp = client.post("/api/admin/config", json={"smtp_host": "evil.example.com"})
    assert resp.status_code == 400
    # 非敏感参数可运行时修改
    resp = client.post("/api/admin/config", json={"rag_top_k": 6})
    assert resp.status_code == 200
    assert client.get("/api/admin/config").json()["rag_top_k"] == 6


def test_admin_cookie_secure_flag(store, tmp_path):
    cfg = Config(admin_password="secret123", cookie_secure=True,
                 frontend_dir=tmp_path)
    app = create_app(cfg, FakePipeline(), store)
    client = TestClient(app)
    resp = client.post("/api/admin/login", json={"password": "secret123"})
    assert resp.status_code == 200
    assert "Secure" in resp.headers.get("set-cookie", "")


def test_health_reports_probes(store):
    probes = {"vllm": lambda: True, "chroma": lambda: False, "mcp": lambda: True}
    client = TestClient(make_app(store, probes=probes))
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "vllm": True, "chroma": False, "mcp": True}

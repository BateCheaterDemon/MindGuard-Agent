"""Task 9:前端静态托管测试。"""
from pathlib import Path

from fastapi.testclient import TestClient

from app.api import create_app
from app.config import Config
from app.session_store import SessionStore

REPO_ROOT = Path(__file__).resolve().parents[2]


class FakePipeline:
    async def process(self, session_id, content):
        yield {"event": "done", "archived": False}


def make_client(tmp_path):
    cfg = Config(admin_password="x", frontend_dir=REPO_ROOT / "frontend",
                 chroma_dir=tmp_path / "no-chroma")
    store = SessionStore(tmp_path / "s.db")
    app = create_app(cfg, FakePipeline(), store)
    return TestClient(app)


def test_frontend_serving(tmp_path):
    client = make_client(tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "<title>MindGuard" in resp.text
    assert client.get("/admin.html").status_code == 200
    css = client.get("/css/style.css")
    assert css.status_code == 200
    assert css.headers["content-type"].startswith("text/css")
    assert client.get("/不存在的页面.html").status_code == 404


def test_api_routes_survive_static_mount(tmp_path):
    """静态挂载不得遮蔽 API 路由(挂载顺序回归钉)。"""
    client = make_client(tmp_path)
    assert client.get("/api/health").status_code == 200
    # 管理路由可达(未登录 401,而不是被静态挂载吞成 404)
    assert client.get("/api/admin/review").status_code == 401
    assert client.post("/api/sessions", json={"student_id": "x", "name": "y"}).status_code == 200

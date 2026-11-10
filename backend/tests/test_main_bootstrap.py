"""Task 10 回归:生产入口 `python -m app.main` 从 backend/ 目录启动时必须能导入
knowledge 包(仓库根不在 sys.path 时曾致 ModuleNotFoundError)。"""
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


def test_app_main_full_assembly_from_backend_cwd(tmp_path):
    code = (
        "import sys; sys.path.insert(0, '.') ; "
        "from pathlib import Path; "
        "from app.config import Config; "
        "from app.main import create_app; "
        f"cfg = Config(admin_password='x', db_path=Path('{tmp_path}/d.db'), "
        f"chroma_dir=Path('{tmp_path}/no-chroma'), "
        f"frontend_dir=Path('{tmp_path}/no-frontend')); "
        "app = create_app(cfg); "
        "print('routes', len(app.routes))"
    )
    proc = subprocess.run([sys.executable, "-c", code],
                          cwd=REPO / "backend", capture_output=True,
                          text=True, timeout=120)
    assert proc.returncode == 0, proc.stderr
    assert "routes" in proc.stdout

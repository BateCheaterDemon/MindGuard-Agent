"""Task 3 回归:build_chroma.py 作为独立脚本可运行(fake embedder,不依赖 GPU)。

钉住之前踩过的坑:直接 `python knowledge/build_chroma.py` 时
仓库根不在 sys.path,`import knowledge.chunker` 会失败。
"""
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_build_chroma_script_runs_with_fake_embedder(tmp_path):
    proc = subprocess.run(
        [sys.executable, "knowledge/build_chroma.py",
         "--docs-dir", "knowledge/raw", "--persist-dir", str(tmp_path / "chroma"),
         "--embed", "fake"],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr
    assert "共入库" in proc.stdout

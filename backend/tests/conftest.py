"""pytest bootstrap:从仓库根运行 `pytest backend/tests` 时,把 backend/ 加入 sys.path。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

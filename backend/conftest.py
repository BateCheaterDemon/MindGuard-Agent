"""pytest bootstrap:让 `app` 包可在仓库根目录/backend 目录两种运行方式下导入。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

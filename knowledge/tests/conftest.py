"""pytest bootstrap:让 knowledge/ 下的模块可从 knowledge/tests 导入。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

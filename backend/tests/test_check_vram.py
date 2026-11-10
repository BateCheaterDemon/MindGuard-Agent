"""Task 10:显存预检测试(monkeypatch pynvml,不依赖真实 GPU 状态)。"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

import pynvml  # nvidia-ml-py(vllm_deployment 环境已有)

from check_vram import budget_ok


class _FakeHandle:
    pass


def test_budget_ok_insufficient(monkeypatch):
    monkeypatch.setattr(pynvml, "nvmlInit", lambda: None)
    monkeypatch.setattr(pynvml, "nvmlShutdown", lambda: None)
    monkeypatch.setattr(pynvml, "nvmlDeviceGetHandleByIndex",
                        lambda i: _FakeHandle())
    monkeypatch.setattr(pynvml, "nvmlDeviceGetMemoryInfo",
                        lambda h: type("Mem", (), {"total": 8 * 1024 ** 3})())
    ok, detail = budget_ok(min_gib=16.0)
    assert ok is False
    assert detail["total_gib"] < 16.0


def test_budget_ok_sufficient(monkeypatch):
    monkeypatch.setattr(pynvml, "nvmlInit", lambda: None)
    monkeypatch.setattr(pynvml, "nvmlShutdown", lambda: None)
    monkeypatch.setattr(pynvml, "nvmlDeviceGetHandleByIndex",
                        lambda i: _FakeHandle())
    monkeypatch.setattr(pynvml, "nvmlDeviceGetMemoryInfo",
                        lambda h: type("Mem", (), {"total": 32 * 1024 ** 3})())
    ok, detail = budget_ok(min_gib=16.0)
    assert ok is True
    assert detail["total_gib"] >= 16.0

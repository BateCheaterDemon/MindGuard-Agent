"""Task 1:配置加载测试。"""
import pytest

from app import config as config_mod


def test_load_config_defaults(monkeypatch):
    """未提供 config 文件时,默认值与 Global Constraints 一致。"""
    monkeypatch.setenv("MINDGUARD_ADMIN_PASSWORD", "testpass")
    cfg = config_mod.load_config()
    assert cfg.model_name == "Qwen3.5-4B-cpsy-W4A16"
    assert cfg.vllm_base_url == "http://127.0.0.1:8000/v1"
    assert cfg.port == 8080
    assert cfg.rag_top_k == 4
    assert cfg.rag_threshold == 0.35
    assert cfg.session_timeout_minutes == 30
    assert cfg.risk_window_turns == 6
    assert cfg.rate_limit_per_minute == 20
    assert cfg.embedding_model == "bge-m3"
    assert cfg.embedding_device == "auto"


def test_missing_admin_password_raises(monkeypatch):
    """管理员密码是必填项,未提供时报 RuntimeError。"""
    monkeypatch.delenv("MINDGUARD_ADMIN_PASSWORD", raising=False)
    with pytest.raises(RuntimeError):
        config_mod.load_config()

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
    assert cfg.embedding_model == "bge-small-zh-v1.5"  # 网络受限裁决:默认降级模型,可配置回 bge-m3
    assert cfg.embedding_device == "auto"
    # 数据/前端目录必须落在仓库根(data/ 与 build_chroma/演练脚本的约定一致)
    repo_root = config_mod.PROJECT_ROOT
    assert repo_root.name == "MindGuard"
    assert cfg.chroma_dir == repo_root / "data" / "chroma"
    assert cfg.archives_dir == repo_root / "data" / "archives"
    assert cfg.frontend_dir == repo_root / "frontend"
    assert (repo_root / "mcp_server" / "server.py").exists()


def test_missing_admin_password_raises(monkeypatch):
    """管理员密码是必填项,未提供时报 RuntimeError。"""
    monkeypatch.delenv("MINDGUARD_ADMIN_PASSWORD", raising=False)
    with pytest.raises(RuntimeError):
        config_mod.load_config()

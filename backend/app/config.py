"""MindGuard 全局配置。

来源优先级:默认值 < 可选配置文件(单层 YAML 风格 key: value)< 环境变量。
环境变量前缀 MINDGUARD_;嵌套字段(SmtpConfig)用 MINDGUARD_SMTP_* 前缀。

有意不依赖 pyyaml:配置只有一层嵌套,手写解析器足够,
避免给 vllm_deployment 环境再添依赖。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class SmtpConfig:
    host: str = ""
    port: int = 465
    username: str = ""
    password: str = ""
    from_addr: str = ""
    use_ssl: bool = True


@dataclass
class Config:
    host: str = "0.0.0.0"
    port: int = 8080
    vllm_base_url: str = "http://127.0.0.1:8000/v1"
    model_name: str = "Qwen3.5-4B-cpsy-W4A16"
    chroma_dir: Path = PROJECT_ROOT / "data" / "chroma"
    archives_dir: Path = PROJECT_ROOT / "data" / "archives"
    db_path: Path = PROJECT_ROOT / "data" / "mindguard.db"
    log_dir: Path = PROJECT_ROOT / "logs"
    embedding_model: str = "bge-m3"
    embedding_device: str = "auto"
    rag_top_k: int = 4
    rag_threshold: float = 0.35
    session_timeout_minutes: int = 30
    risk_window_turns: int = 6
    rate_limit_per_minute: int = 20
    admin_password: str = ""  # 必填,来自 env MINDGUARD_ADMIN_PASSWORD
    alert_email_to: str = ""
    smtp: SmtpConfig = field(default_factory=SmtpConfig)
    high_risk_keywords: list[str] = field(default_factory=list)


def _parse_flat_file(path: Path) -> dict:
    """解析单层 YAML 风格配置:`key: value`,支持 `smtp.host` 式一段嵌套,# 注释。"""
    data: dict = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        key, _, value = line.partition(":")
        value = value.strip().strip("'\"")
        node = data
        parts = key.strip().split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return data


def _convert(value: str, target: object) -> object:
    """把字符串按目标字段类型转换(bool/int/float/Path/str,list[str] 用逗号分隔)。"""
    if isinstance(target, bool):
        return value.lower() in ("true", "1", "yes", "on")
    if isinstance(target, int):
        return int(value)
    if isinstance(target, float):
        return float(value)
    if isinstance(target, Path):
        return Path(value)
    if isinstance(target, list):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


def load_config(path: Path | None = None) -> Config:
    """加载配置;MINDGUARD_ADMIN_PASSWORD 未提供时抛 RuntimeError。"""
    cfg = Config()
    file_data: dict = _parse_flat_file(path) if path and path.exists() else {}

    for field_name in Config.__dataclass_fields__:  # type: ignore[attr-defined]
        if field_name == "smtp":
            smtp_data = file_data.get("smtp", {})
            for sub in SmtpConfig.__dataclass_fields__:  # type: ignore[attr-defined]
                env_key = f"MINDGUARD_SMTP_{sub.upper()}"
                raw = os.environ.get(env_key) or smtp_data.get(sub)
                if raw is not None:
                    setattr(cfg.smtp, sub, _convert(str(raw), getattr(cfg.smtp, sub)))
            continue
        env_key = f"MINDGUARD_{field_name.upper()}"
        raw = os.environ.get(env_key) or file_data.get(field_name)
        if raw is not None:
            setattr(cfg, field_name, _convert(str(raw), getattr(cfg, field_name)))

    if not cfg.admin_password:
        raise RuntimeError(
            "管理员密码必填:请设置环境变量 MINDGUARD_ADMIN_PASSWORD")
    return cfg

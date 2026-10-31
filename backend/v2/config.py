# -*- coding: utf-8 -*-
"""
AgentDeck v2 统一配置模块（基于 pydantic-settings）。
兼容 Windows 含空格工作目录路径，禁止保存任何第三方模型供应商密钥或 CCSwitch 配置。
"""

from __future__ import annotations

from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from backend.core.logger import PROJECT_ROOT

# 默认允许的受控前端跨域来源正则：
# 仅允许本机环回 (127.0.0.1 / localhost)、Tailscale MagicDNS (*.ts.net)、
# Tailscale CGNAT (100.64.0.0/10) 及私有局域网段 (10.x / 192.168.x / 172.16-31.x)，彻底禁止 "*" + credentials
DEFAULT_CORS_ORIGIN_REGEX = (
    r"^https?://("
    r"localhost|127\.0\.0\.1|"
    r"[a-zA-Z0-9-]+(\.[a-zA-Z0-9-]+)*\.ts\.net|"
    r"100\.(6[4-9]|[7-9]\d|1[01]\d|12[0-7])\.\d{1,3}\.\d{1,3}|"
    r"10\.\d{1,3}\.\d{1,3}\.\d{1,3}|"
    r"192\.168\.\d{1,3}\.\d{1,3}|"
    r"172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}"
    r")(:\d{1,5})?$"
)


class AppV2Settings(BaseSettings):
    """AgentDeck v2 运行配置。"""

    model_config = SettingsConfigDict(
        env_prefix="AGENTDECK_",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(
        default_factory=lambda: PROJECT_ROOT / "data",
        description="本地运行时数据目录（含空格路径安全）",
    )
    db_filename: str = Field(
        default="agentdeck.db",
        description="主 SQLite 数据库文件名",
    )
    sqlite_busy_timeout_ms: int = Field(
        default=15000,
        ge=1000,
        le=60000,
        description="SQLite 写锁等待超时毫秒数",
    )
    cors_origin_regex: str = Field(
        default=DEFAULT_CORS_ORIGIN_REGEX,
        description="受控 CORS 允许来源正则表达式",
    )
    cors_extra_origins: list[str] = Field(
        default_factory=list,
        description="额外显式允许的受控前端 Origin 列表（严禁包含 '*'）",
    )

    @property
    def db_path(self) -> Path:
        """返回规范化的 SQLite 数据库绝对路径。"""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        return (self.data_dir / self.db_filename).resolve()

    @property
    def backup_dir(self) -> Path:
        """返回 SQLite 在线热备存储目录。"""
        b_dir = self.data_dir / "backups"
        b_dir.mkdir(parents=True, exist_ok=True)
        return b_dir.resolve()

    @property
    def sqlalchemy_url(self) -> str:
        """构造兼容 Windows 含空格路径的 SQLAlchemy SQLite 连接 URL。"""
        posix_path = self.db_path.as_posix()
        return f"sqlite:///{posix_path}"


def get_v2_settings() -> AppV2Settings:
    """获取当前运行配置实例。"""
    return AppV2Settings()


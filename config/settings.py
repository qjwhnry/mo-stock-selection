"""全局配置加载。

使用 pydantic-settings 从 .env / 环境变量读取，所有模块统一通过
`from config.settings import settings` 获取配置实例，便于测试时注入。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：config/settings.py → config/ → 项目根
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """应用全局配置。字段与 .env.example 一一对应。"""

    # ---------- Tushare ----------
    tushare_token: str = Field(default="", description="Tushare Pro API token")

    # ---------- Claude ----------
    anthropic_api_key: str = Field(default="", description="Anthropic API key")
    anthropic_model: str = Field(
        default="claude-sonnet-4-6",
        description="默认模型；周末深度复盘可切 claude-opus-4-7[1m]",
    )

    # ---------- 数据库 ----------
    db_url: str = Field(
        default="postgresql+psycopg2://mo_stock:mo_stock@localhost:5432/mo_stock",
        description="SQLAlchemy 连接串；默认 docker-compose 起的本地 PG",
    )

    # ---------- mo-skills 外部依赖定位 ----------
    mo_skills_root: Path = Field(
        default=Path("D:/QuantProjects/mo-skills"),
        description="邻目录 mo-skills 绝对路径（GTHT node skill 通过此路径定位）",
    )
    gtht_entry_json_path: Path = Field(
        default=PROJECT_ROOT / "gtht-skill-shared" / "gtht-entry.json",
        description="国泰海通 API Key 文件位置",
    )

    # ---------- 运行时 ----------
    log_level: str = Field(default="INFO", description="日志级别")
    report_dir: Path = Field(default=PROJECT_ROOT / "data" / "reports")
    cache_dir: Path = Field(default=PROJECT_ROOT / "data" / "cache")
    log_dir: Path = Field(default=PROJECT_ROOT / "data" / "logs")

    # ---------- 选股参数（可被 weights.yaml 覆盖）----------
    top_n_final: int = Field(default=20, description="最终报告输出的股票数量")
    top_n_after_filter: int = Field(default=50, description="规则层筛选后进入 AI 层的数量")
    history_keep_days: int = Field(default=180, description="原始数据滚动保留天数")

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    """获取单例配置；lru_cache 保证进程内只加载一次 .env。"""
    return Settings()


# 便捷别名，供 `from config.settings import settings` 使用
settings: Settings = get_settings()

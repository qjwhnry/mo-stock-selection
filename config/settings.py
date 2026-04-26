"""全局配置加载。

使用 pydantic-settings 从 .env / 环境变量读取，所有模块统一通过
`from config.settings import settings` 获取配置实例，便于测试时注入。

**优先级策略**（v2.2 后）：
- `.env` 文件 **强制覆盖** 系统环境变量
- 即 `.env` 是唯一事实源；系统里残留的 `ANTHROPIC_API_KEY=''` 等空值不会污染配置
- 实施：在 Settings 实例化前 `load_dotenv(override=True)` 先把 .env 写入 os.environ
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# 项目根目录：config/settings.py → config/ → 项目根
PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent

# **关键**：在 Settings 类被读取前先用 dotenv 强制覆盖系统环境变量。
# 这样 OS 中残留的 ANTHROPIC_API_KEY='' 等空字符串不会盖掉 .env 里的真实值。
# 注意：仅本项目本进程生效；不修改 .env 文件本身、不污染其他进程。
load_dotenv(PROJECT_ROOT / ".env", override=True)


class Settings(BaseSettings):
    """应用全局配置。字段与 .env.example 一一对应。"""

    # ---------- Tushare ----------
    tushare_token: str = Field(default="", description="Tushare Pro API token")
    tushare_http_url: str = Field(
        default="",
        description="Tushare Pro API 自定义 HTTP 地址；留空则用官方默认域名",
    )

    # ---------- GTHT 灵犀 ----------
    gtht_api_key: str = Field(
        default="",
        description="国泰海通灵犀 API Key；首次使用时自动落盘到 gtht_entry_json_path",
    )
    # P2-16：研报全量查询有时超过 60s，留可配置入口避免硬编码
    gtht_skill_timeout: int = Field(
        default=60,
        description="GTHT skill 子进程调用超时（秒）；研报类查询可调到 120+",
    )
    gtht_authchecker_timeout: int = Field(
        default=30,
        description="GTHT authChecker 子进程超时（秒）；只看退出码，不需要太长",
    )

    # ---------- GTHT 辅助 LLM（OpenAI 兼容）----------
    gtht_llm_api_key: str = Field(default="", description="辅助 LLM 的 API Key")
    gtht_llm_base_url: str = Field(
        default="",
        description="OpenAI 兼容的 base_url，如 https://api.deepseek.com/v1",
    )
    gtht_llm_model: str = Field(
        default="",
        description="辅助 LLM 模型名，如 deepseek-chat / qwen-plus / moonshot-v1-8k",
    )

    # ---------- Claude ----------
    anthropic_api_key: str = Field(default="", description="Anthropic API key")
    anthropic_base_url: str = Field(
        default="",
        description="Anthropic API 自定义 base_url；留空则用官方默认（https://api.anthropic.com）",
    )
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
    # 默认指向项目内 vendor/mo-skills（已 vendor 进仓库，跨平台开箱即用）；
    # 若想用项目外的 mo-skills（例如本地开发同步最新版），通过 .env 的 MO_SKILLS_ROOT 覆盖即可。
    mo_skills_root: Path = Field(
        default=PROJECT_ROOT / "vendor" / "mo-skills",
        description="mo-skills 根目录；GTHT node skill 通过此路径定位（拼接 /gtht-skills/<skill>）",
    )
    # 注意：此路径必须落在 GTHT skill-entry.js 写死的三条搜索路径之一，
    # 否则即便 Python 落盘成功，node 端依然会报"未授权"。js 的查找规则是
    # 从 skill 目录向上推三级找 `gtht-skill-shared/gtht-entry.json`：
    #   1) <skill父>/gtht-skill-shared/...        = vendor/mo-skills/gtht-skills/gtht-skill-shared/...
    #   2) <skill父父>/gtht-skill-shared/...      = vendor/mo-skills/gtht-skill-shared/...   ← 默认选这条
    #   3) <skill父父父>/gtht-skill-shared/...    = vendor/gtht-skill-shared/...
    # 选第 2 条因为它跟原邻目录 mo-skills 的约定一致，且不污染 skill 代码目录。
    gtht_entry_json_path: Path = Field(
        default=PROJECT_ROOT / "vendor" / "mo-skills" / "gtht-skill-shared" / "gtht-entry.json",
        description="国泰海通 API Key 文件位置；必须位于 skill-entry.js 的三条搜索路径之一",
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

"""GTHT 灵犀客户端封装。

**设计原则**：
- 走 subprocess 调 `node <mo_skills_root>/gtht-skills/<skill>/skill-entry.js`
- API Key 配置入口在 `.env` 的 `GTHT_API_KEY`，懒加载落盘到 `gtht-entry.json`
- 不为每个 skill 写专属业务方法，统一 `call(skill, gateway, tool, **kwargs)`
- 返回值统一为 dict（`stdout` JSON 解析后）

详细设计：docs/superpowers/specs/2026-04-23-gtht-client-design.md
"""
from __future__ import annotations

import json

from loguru import logger

from config.settings import settings


class GthtError(Exception):
    """GTHT 调用失败。"""


class GthtClient:
    """GTHT 灵犀客户端（进程级单例）。"""

    _instance: GthtClient | None = None

    def __new__(cls) -> GthtClient:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    # ---------- auth 懒加载 ----------

    def ensure_auth(self) -> None:
        """检查并落盘 GTHT API Key。

        决策表见 spec §4.1：
        - .env 有值 + 文件缺/key 不一致 → 用 .env 覆写
        - .env 有值 + 文件存在且 key 相同 → 不动
        - .env 空 + 文件存在 → 用文件中已有 key（兜底）
        - .env 空 + 文件缺 → 抛 GthtError
        """
        entry_path = settings.gtht_entry_json_path
        env_key = (settings.gtht_api_key or "").strip()

        file_key = ""
        file_parse_failed = False
        if entry_path.exists():
            try:
                data = json.loads(entry_path.read_text(encoding="utf-8"))
                file_key = str(data.get("apiKey", "")).strip()
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("gtht-entry.json 解析失败，将视为缺失: {}", exc)
                file_parse_failed = True

            if not file_parse_failed:
                if env_key and env_key != file_key:
                    self._write_entry(env_key)
                return  # 文件可用（.env 同步或空兜底），结束

        # 文件不存在 或 解析失败
        if not env_key:
            raise GthtError(
                "GTHT_API_KEY 未配置；请检查 .env 或手动跑 "
                "`node skill-entry.js authChecker auth` 完成扫码授权"
            )
        self._write_entry(env_key)

    def _write_entry(self, key: str) -> None:
        """将 API Key 写入 gtht-entry.json 文件。"""
        entry_path = settings.gtht_entry_json_path
        entry_path.parent.mkdir(parents=True, exist_ok=True)
        entry_path.write_text(
            json.dumps({"apiKey": key}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("GTHT entry written -> {} (key={}***)", entry_path, key[:4])

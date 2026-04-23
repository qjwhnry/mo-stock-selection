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
import subprocess  # noqa: S404 - 调用本地 node skill 是设计要求
from typing import Any

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

    # ---------- 底层调用 ----------

    def call(
        self,
        skill: str,
        gateway: str,
        tool: str,
        **kwargs: Any,
    ) -> dict:
        """执行 `node skill-entry.js mcpClient call <gateway> <tool> k=v ...`。

        参数:
            skill: skill 目录名，如 "lingxi-researchreport-skill"
            gateway: 网关名（见 SKILL.md），如 "researchreport"
            tool: 工具名，如 "research"
            **kwargs: 工具参数，会按 `k=v` 格式拼到命令尾部

        返回 stdout 解析后的 dict；失败统一抛 GthtError。
        """
        self.ensure_auth()

        gtht_skills_root = settings.mo_skills_root / "gtht-skills"
        skill_dir = gtht_skills_root / skill
        try:
            skill_dir.resolve().relative_to(gtht_skills_root.resolve())
        except ValueError as exc:
            raise GthtError(f"非法 skill 路径（越界）: {skill}") from exc
        if not skill_dir.exists():
            raise GthtError(f"GTHT skill 目录不存在: {skill_dir}")

        cmd: list[str] = ["node", "skill-entry.js", "mcpClient", "call", gateway, tool]
        for k, v in kwargs.items():
            cmd.append(f"{k}={v}")

        logger.debug("GTHT call: cwd={} cmd={}", skill_dir, cmd)

        try:
            proc = subprocess.run(  # noqa: S603 - 命令为受信常量 + 校验过的 skill
                cmd,
                cwd=skill_dir,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GthtError("node 未安装或不在 PATH 中") from exc
        except subprocess.TimeoutExpired as exc:
            raise GthtError(f"GTHT 调用超时: {skill}/{gateway}/{tool}") from exc

        if proc.returncode != 0:
            raise GthtError(
                f"GTHT 调用失败 (exit {proc.returncode}): {proc.stderr.strip()}"
            )

        try:
            data = json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise GthtError(f"GTHT 返回非 JSON: {proc.stdout[:200]}") from exc

        if isinstance(data, dict) and "error" in data:
            raise GthtError(f"GTHT 错误: {data['error']}")

        return data

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
import re
import subprocess  # noqa: S404 - 调用本地 node skill 是设计要求
from typing import Any

from loguru import logger

from config.settings import settings


class GthtError(Exception):
    """GTHT 调用失败。"""


# ---------- skill stdout 解析（GthtClient.call() 的输出适配层）----------
#
# 实测下来 node skill-entry.js 的 stdout 至少有 3 种自然格式：
#   A. 纯文本（如 marketdata-tool 直接 print 给人看的中文展示）
#   B. 双层 JSON：外层 {"text": "<inner_json_str>"}，内层是合法 JSON dict 含 result 字段
#   C. 双层 JSON：外层合法，内层 text 字段值是"伪 JSON"（result 后面跟 markdown 或裸中文，
#                  非合法 JSON）
# 老实现 `json.loads(stdout)` 对三种都解析失败。本节统一抹平。

# bundle 漏到 stdout 的已知调试行前缀，新增噪声直接补到 tuple。
_DEBUG_LINE_PREFIXES: tuple[str, ...] = ("adeline-url ",)

# 用于格式 C：从内层伪 JSON 文本里宽松抽取 result 字段值。
# 匹配 `"result": <任意内容>` 后到末尾右花括号之前，DOTALL 让 . 跨行。
_RESULT_FIELD_RE = re.compile(r'"result"\s*:\s*(.*?)\s*\}\s*$', re.DOTALL)


def _strip_debug_lines(stdout: str) -> str:
    """剥离 bundle 漏到 stdout 的已知调试行（如 'adeline-url ...'），并 trim。"""
    kept = [
        line
        for line in stdout.splitlines()
        if not any(line.startswith(p) for p in _DEBUG_LINE_PREFIXES)
    ]
    return "\n".join(kept).strip()


def _extract_result_field(inner_text: str) -> str:
    """从非合法 JSON 的内层文本里宽松提取 `"result":` 后面的内容。

    GTHT 部分 skill 把 markdown 表格直接拼进伪 JSON 字符串里（result 后面没引号），
    所以走不到 json.loads 这条路，只能用正则兜底。提取失败时退回原文。
    """
    m = _RESULT_FIELD_RE.search(inner_text)
    if m is None:
        return inner_text.strip()
    return m.group(1).strip()


def _parse_skill_output(stdout: str) -> dict[str, Any]:
    """把 node skill-entry.js 的 stdout 解析成统一 dict。

    返回 dict 必含 `format` 字段，4 种取值：
      - "text":        非 JSON，整体是给人看的展示文本
      - "raw_json":    顶层是 JSON dict 但无 text 字段（兼容未来 skill）
      - "json_result": 双层 JSON，内层是合法 JSON dict
      - "text_result": 外层 JSON 合法但内层是伪 JSON，用正则提 result

    设计上永不抛异常（不合法输入也会落到 "text" 分支），错误检测留给上层 call()
    通过 `error` 字段检查或 returncode 判断完成。
    """
    text = _strip_debug_lines(stdout)

    # ----- 第 0 层：尝试外层 JSON -----
    try:
        outer = json.loads(text)
    except json.JSONDecodeError:
        # 完全不是 JSON → 格式 A
        return {"format": "text", "text": text}

    # JSON 顶层不是 dict（数组/标量）→ 包装一下，避免上层 isinstance 假设破裂
    if not isinstance(outer, dict):
        return {"format": "raw_json", "data": outer}

    # ----- 第 1 层：顶层无 text 字段 → 当未来扩展处理 -----
    if "text" not in outer or not isinstance(outer["text"], str):
        # 直接把外层 dict 摊平，便于上层兼容历史 GTHT 错误返回 {"error": "..."}
        return {"format": "raw_json", **outer}

    inner_str: str = outer["text"]

    # ----- 第 2 层：尝试内层 JSON -----
    try:
        inner = json.loads(inner_str)
    except json.JSONDecodeError:
        # 内层非合法 JSON → 格式 C，用正则兜底提 result
        return {"format": "text_result", "result": _extract_result_field(inner_str)}

    # 内层合法 JSON 但不是 dict → 包装
    if not isinstance(inner, dict):
        return {"format": "json_result", "data": inner}

    # 标准化：把 result 字段拎到顶层；没有 result 就摊平 inner
    if "result" in inner:
        return {"format": "json_result", "result": inner["result"]}
    return {"format": "json_result", **inner}


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

        首次成功后会缓存 _auth_ok=True，后续 call() 直接跳过；如需强制重检，
        可手动 self._auth_ok = False 后再调用。
        """
        if getattr(self, "_auth_ok", False):
            return

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
                self._auth_ok = True
                return  # 文件可用（.env 同步或空兜底），结束

        # 文件不存在 或 解析失败
        if not env_key:
            raise GthtError(
                "GTHT_API_KEY 未配置；请检查 .env 或手动跑 "
                "`node skill-entry.js authChecker auth` 完成扫码授权"
            )
        self._write_entry(env_key)
        self._auth_ok = True

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

        # P2-16：超时改可配（settings.gtht_skill_timeout，默认 60s）
        timeout_s = settings.gtht_skill_timeout
        try:
            proc = subprocess.run(  # noqa: S603 - 命令为受信常量 + 校验过的 skill
                cmd,
                cwd=skill_dir,
                capture_output=True,
                text=True,
                # 显式使用 UTF-8 解码并容错，避免 Windows 默认 gbk 解码导致线程报错。
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GthtError("node 未安装或不在 PATH 中") from exc
        except subprocess.TimeoutExpired as exc:
            raise GthtError(
                f"GTHT 调用超时（{timeout_s}s）: {skill}/{gateway}/{tool}；"
                f"如需放宽，调高 settings.gtht_skill_timeout"
            ) from exc

        if proc.returncode != 0:
            raise GthtError(
                f"GTHT 调用失败 (exit {proc.returncode}): {proc.stderr.strip()}"
            )

        # 老实现假设 stdout 是顶层 JSON dict，但实测三种格式都见过：
        # 纯文本 / 双层 JSON 合法内层 / 双层 JSON 伪 JSON 内层。
        # 统一交给 _parse_skill_output 抹平，永远拿到带 format 字段的 dict。
        data = _parse_skill_output(proc.stdout)

        # 历史上 GTHT 通过顶层 {"error": "..."} 上报业务错误，仅 raw_json 走这条；
        # 其他格式（text / json_result / text_result）的 error 语义靠 returncode 判断。
        if data.get("format") == "raw_json" and "error" in data:
            raise GthtError(f"GTHT 错误: {data['error']}")

        return data

    # ---------- 授权状态查询 ----------

    _DEFAULT_SKILL_FOR_AUTH = "lingxi-researchreport-skill"

    def _run_authchecker(self, subcommand: str) -> subprocess.CompletedProcess[str]:
        """运行 `node skill-entry.js authChecker <subcommand>`。

        用于 check_auth / clear_auth 等不返回业务数据、只看退出码的子命令。
        """
        skill_dir = (
            settings.mo_skills_root / "gtht-skills" / self._DEFAULT_SKILL_FOR_AUTH
        )
        if not skill_dir.exists():
            raise GthtError(f"GTHT skill 目录不存在: {skill_dir}")
        # P2-16：authChecker 超时单独可配（默认 30s，只看退出码不需要太长）
        try:
            return subprocess.run(  # noqa: S603
                ["node", "skill-entry.js", "authChecker", subcommand],
                cwd=skill_dir,
                capture_output=True,
                text=True,
                # authChecker 只看退出码，但仍需安全解码 stdout/stderr，避免后台 reader 线程异常。
                encoding="utf-8",
                errors="replace",
                timeout=settings.gtht_authchecker_timeout,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GthtError("node 未安装或不在 PATH 中") from exc

    def check_auth(self) -> bool:
        """透传到 node skill-entry.js authChecker check。返回 True 表示已授权。"""
        return self._run_authchecker("check").returncode == 0

    def clear_auth(self) -> None:
        """透传到 node skill-entry.js authChecker clear（fire-and-forget）。"""
        self._run_authchecker("clear")

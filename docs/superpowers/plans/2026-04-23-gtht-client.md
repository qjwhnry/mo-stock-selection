# GTHT 客户端实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 [src/mo_stock/data_sources/](../../../src/mo_stock/data_sources/) 下新增 GTHT 客户端（`gtht_client.py` + `gtht_agent.py`），打通 ingest/filter 层调用国泰海通灵犀 6 个 skill 的链路；通过 `.env` 的 `GTHT_API_KEY` 自动落盘 `gtht-entry.json`，并新增 OpenAI 兼容辅助 LLM 用于开放式查询。

**Architecture:** 双层结构 —— `GthtClient` 走 `subprocess` 调 `node skill-entry.js`（含 auth 懒加载落盘）；`GthtAgent` 用 OpenAI SDK + function calling，让辅助 LLM 自动选 skill tool 与参数。两者共享 `GthtError` 异常。混合模式：固定任务（如研报抓取）直连 `GthtClient.call()`，开放式查询走 `GthtAgent.ask()`。

**Tech Stack:** Python 3.10+、`subprocess`、`openai>=1.50`（OpenAI SDK 用于 OpenAI 兼容 LLM）、pytest + pytest-mock、loguru、pydantic-settings。

**关联 spec:** [docs/superpowers/specs/2026-04-23-gtht-client-design.md](../specs/2026-04-23-gtht-client-design.md)

---

## 文件结构

| 路径 | 操作 | 职责 |
|---|---|---|
| `pyproject.toml` | 修改 | 增加 `openai>=1.50` 依赖 |
| `config/settings.py` | 修改 | 新增 4 个字段（`gtht_api_key` + 3 个 LLM 字段） |
| `.env.example` | 修改 | 新增 GTHT 配置段 |
| `src/mo_stock/data_sources/gtht_client.py` | 新建 | subprocess 封装 + auth 落盘 + `GthtError` |
| `src/mo_stock/data_sources/gtht_agent.py` | 新建 | OpenAI SDK + function calling + tools schema |
| `src/mo_stock/data_sources/__init__.py` | 修改 | 文档字符串保持现状（已提及 GTHT），无需改 |
| `tests/unit/test_gtht_client.py` | 新建 | mock subprocess，覆盖 auth 5 case 与 call 异常路径 |
| `tests/unit/test_gtht_agent.py` | 新建 | mock OpenAI client + GthtClient，覆盖 function calling 循环 |

---

## Task 1: 添加 openai 依赖

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 在 dependencies 列表追加 openai**

打开 [pyproject.toml](../../../pyproject.toml)，在 `dependencies` 列表中（`anthropic>=0.40` 同段附近）追加一行：

```toml
    # AI
    "anthropic>=0.40",
    "openai>=1.50",
```

- [ ] **Step 2: 安装新依赖**

Run: `pip install -e ".[dev]"`
Expected: openai 安装成功，无版本冲突

- [ ] **Step 3: 验证 import 可用**

Run: `python -c "from openai import OpenAI; print(OpenAI)"`
Expected: 打印 `<class 'openai.OpenAI'>`，无 ImportError

- [ ] **Step 4: 提交**

```bash
git add pyproject.toml
git commit -m "chore(deps): 新增 openai SDK 用于 GTHT 辅助 LLM"
```

---

## Task 2: 扩展配置 —— settings.py 与 .env.example

**Files:**
- Modify: `config/settings.py:21-26`（在 Tushare 段后面、Claude 段前面插入 GTHT 段）
- Modify: `.env.example`（在 Claude 段后面追加 GTHT 段）

- [ ] **Step 1: 在 settings.py 中新增 GTHT 字段**

在 [config/settings.py](../../../config/settings.py) 的 `Settings` 类内部，紧跟 Tushare 段之后插入：

```python
    # ---------- GTHT 灵犀 ----------
    gtht_api_key: str = Field(
        default="",
        description="国泰海通灵犀 API Key；首次使用时自动落盘到 gtht_entry_json_path",
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
```

注意：保留原有的 `gtht_entry_json_path` 与 `mo_skills_root` 字段，**不要动**。

- [ ] **Step 2: 在 .env.example 追加 GTHT 段**

在 [.env.example](../../../.env.example) 的 `# ---------- mo-skills 依赖定位 ----------` 段之前插入：

```bash
# ---------- GTHT 灵犀（必填）----------
# 来源：灵犀/君弘 APP → API KEY 管理 → 复制生效中的 key
# Python 端会自动把此 key 写入 gtht_entry_json_path 指向的 JSON 文件
GTHT_API_KEY=your_gtht_api_key_here

# ---------- GTHT 辅助 LLM（OpenAI 兼容，开放式查询用）----------
# 用于 ingest/filter 层走自然语言调用 GTHT skill 时的 LLM 决策器
# 推荐选项（任选其一）：
#   DeepSeek:  base=https://api.deepseek.com/v1                        model=deepseek-chat
#   通义 Qwen:  base=https://dashscope.aliyuncs.com/compatible-mode/v1   model=qwen-plus
#   Kimi:      base=https://api.moonshot.cn/v1                         model=moonshot-v1-8k
GTHT_LLM_API_KEY=
GTHT_LLM_BASE_URL=
GTHT_LLM_MODEL=
```

- [ ] **Step 3: 验证配置加载不报错**

Run: `python -c "from config.settings import Settings; s = Settings(); print(s.gtht_api_key, s.gtht_llm_model)"`
Expected: 打印两个空字符串（默认值），无 ValidationError

- [ ] **Step 4: 提交**

```bash
git add config/settings.py .env.example
git commit -m "feat(config): 新增 GTHT_API_KEY 与辅助 LLM 配置项"
```

---

## Task 3: GthtClient 骨架 + 异常类 + auth 落盘

**Files:**
- Create: `src/mo_stock/data_sources/gtht_client.py`
- Create: `tests/unit/test_gtht_client.py`

- [ ] **Step 1: 写 test_gtht_client.py 的 ensure_auth 5 case**

```python
"""GthtClient 的 subprocess 封装与 auth 落盘测试。"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mo_stock.data_sources.gtht_client import GthtClient, GthtError


@pytest.fixture
def tmp_entry_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """临时 gtht-entry.json 路径，并清空 GthtClient 单例。"""
    entry = tmp_path / "gtht-skill-shared" / "gtht-entry.json"
    monkeypatch.setattr(
        "mo_stock.data_sources.gtht_client.settings.gtht_entry_json_path",
        entry,
    )
    # 清空单例，让每个用例独立
    monkeypatch.setattr(GthtClient, "_instance", None)
    return entry


def _set_env_key(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setattr(
        "mo_stock.data_sources.gtht_client.settings.gtht_api_key",
        value,
    )


class TestEnsureAuth:
    def test_env_empty_and_file_missing_raises(
        self, tmp_entry_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """case: .env 空 + 文件不存在 → 抛 GthtError"""
        _set_env_key(monkeypatch, "")
        client = GthtClient()
        with pytest.raises(GthtError, match="GTHT_API_KEY 未配置"):
            client.ensure_auth()

    def test_env_set_and_file_missing_writes_file(
        self, tmp_entry_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """case: .env 有值 + 文件不存在 → 自动落盘"""
        _set_env_key(monkeypatch, "MY_NEW_KEY")
        client = GthtClient()
        client.ensure_auth()
        assert tmp_entry_path.exists()
        assert json.loads(tmp_entry_path.read_text())["apiKey"] == "MY_NEW_KEY"

    def test_env_set_and_file_same_key_no_write(
        self, tmp_entry_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """case: .env 与文件相同 → 不应触发 _write_entry（避免无谓 I/O）"""
        tmp_entry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_entry_path.write_text(json.dumps({"apiKey": "SAME_KEY"}))

        _set_env_key(monkeypatch, "SAME_KEY")
        client = GthtClient()
        write_spy = MagicMock()
        monkeypatch.setattr(client, "_write_entry", write_spy)

        client.ensure_auth()
        write_spy.assert_not_called()

    def test_env_set_and_file_different_key_overwrites(
        self, tmp_entry_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """case: .env 与文件不同 → 用 .env 覆盖（.env 是权威源）"""
        tmp_entry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_entry_path.write_text(json.dumps({"apiKey": "OLD_KEY"}))

        _set_env_key(monkeypatch, "NEW_KEY")
        client = GthtClient()
        client.ensure_auth()
        assert json.loads(tmp_entry_path.read_text())["apiKey"] == "NEW_KEY"

    def test_env_empty_but_file_exists_uses_file(
        self, tmp_entry_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """case: .env 空 + 文件存在 → 兜底使用文件，不抛错"""
        tmp_entry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_entry_path.write_text(json.dumps({"apiKey": "EXISTING_KEY"}))

        _set_env_key(monkeypatch, "")
        client = GthtClient()
        client.ensure_auth()  # 不应抛错
        # 文件内容保持
        assert json.loads(tmp_entry_path.read_text())["apiKey"] == "EXISTING_KEY"
```

- [ ] **Step 2: 运行测试，验证 import 失败**

Run: `pytest tests/unit/test_gtht_client.py -v`
Expected: ModuleNotFoundError: No module named 'mo_stock.data_sources.gtht_client'

- [ ] **Step 3: 创建 gtht_client.py 实现**

新建 [src/mo_stock/data_sources/gtht_client.py](../../../src/mo_stock/data_sources/gtht_client.py)：

```python
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
        if entry_path.exists():
            try:
                data = json.loads(entry_path.read_text(encoding="utf-8"))
                file_key = str(data.get("apiKey", "")).strip()
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("gtht-entry.json 解析失败，将尝试覆写: {}", exc)

            if env_key and env_key != file_key:
                self._write_entry(env_key)
            # 其余情况：文件已可用，或 .env 空兜底用文件，均不写
            return

        # 文件不存在
        if not env_key:
            raise GthtError(
                "GTHT_API_KEY 未配置；请检查 .env 或手动跑 "
                "`node skill-entry.js authChecker auth` 完成扫码授权"
            )
        self._write_entry(env_key)

    def _write_entry(self, key: str) -> None:
        entry_path = settings.gtht_entry_json_path
        entry_path.parent.mkdir(parents=True, exist_ok=True)
        entry_path.write_text(
            json.dumps({"apiKey": key}, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("GTHT entry written -> {} (key={}***)", entry_path, key[:4])
```

- [ ] **Step 4: 运行测试，验证 5 case 全绿**

Run: `pytest tests/unit/test_gtht_client.py::TestEnsureAuth -v`
Expected: 5 passed

- [ ] **Step 5: 提交**

```bash
git add src/mo_stock/data_sources/gtht_client.py tests/unit/test_gtht_client.py
git commit -m "feat(data_sources): GthtClient 骨架与 auth 懒加载落盘"
```

---

## Task 4: GthtClient.call —— subprocess 调用层

**Files:**
- Modify: `src/mo_stock/data_sources/gtht_client.py`
- Modify: `tests/unit/test_gtht_client.py`

- [ ] **Step 1: 在 test_gtht_client.py 追加 TestCall 测试类**

在 `test_gtht_client.py` 顶部已有的 `from unittest.mock import MagicMock` 后再加一行 `import subprocess`，然后在文件末尾追加：

```python
@pytest.fixture
def authed_client(
    tmp_entry_path: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> GthtClient:
    """已授权的 GthtClient + mo_skills_root 指到 tmp 目录，并预建 skill 目录。"""
    tmp_entry_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_entry_path.write_text(json.dumps({"apiKey": "TEST_KEY"}))
    _set_env_key(monkeypatch, "TEST_KEY")

    skills_root = tmp_path / "mo-skills"
    monkeypatch.setattr(
        "mo_stock.data_sources.gtht_client.settings.mo_skills_root", skills_root
    )
    skill_dir = skills_root / "gtht-skills" / "lingxi-researchreport-skill"
    skill_dir.mkdir(parents=True, exist_ok=True)
    return GthtClient()


def _make_completed(
    stdout: str = "{}", stderr: str = "", returncode: int = 0
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=["node"], returncode=returncode, stdout=stdout, stderr=stderr
    )


class TestCall:
    def test_success_returns_parsed_dict(
        self, authed_client: GthtClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_run = MagicMock(return_value=_make_completed(stdout='{"data": [1, 2, 3]}'))
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = authed_client.call(
            "lingxi-researchreport-skill",
            "researchreport",
            "research",
            query="600519",
        )
        assert result == {"data": [1, 2, 3]}

        # 验证命令拼接：node skill-entry.js mcpClient call <gateway> <tool> k=v
        cmd = mock_run.call_args.args[0]
        assert cmd[:6] == [
            "node",
            "skill-entry.js",
            "mcpClient",
            "call",
            "researchreport",
            "research",
        ]
        assert "query=600519" in cmd

    def test_nonzero_exit_raises(
        self, authed_client: GthtClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(return_value=_make_completed(returncode=1, stderr="boom")),
        )
        with pytest.raises(GthtError, match="GTHT 调用失败"):
            authed_client.call("lingxi-researchreport-skill", "researchreport", "research")

    def test_node_not_found_raises(
        self, authed_client: GthtClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            subprocess, "run", MagicMock(side_effect=FileNotFoundError("node"))
        )
        with pytest.raises(GthtError, match="node 未安装"):
            authed_client.call("lingxi-researchreport-skill", "researchreport", "research")

    def test_timeout_raises(
        self, authed_client: GthtClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(side_effect=subprocess.TimeoutExpired(cmd="node", timeout=60)),
        )
        with pytest.raises(GthtError, match="GTHT 调用超时"):
            authed_client.call("lingxi-researchreport-skill", "researchreport", "research")

    def test_invalid_json_raises(
        self, authed_client: GthtClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(return_value=_make_completed(stdout="not json at all")),
        )
        with pytest.raises(GthtError, match="GTHT 返回非 JSON"):
            authed_client.call("lingxi-researchreport-skill", "researchreport", "research")

    def test_stdout_error_field_raises(
        self, authed_client: GthtClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(return_value=_make_completed(stdout='{"error": "Unauthorized"}')),
        )
        with pytest.raises(GthtError, match="Unauthorized"):
            authed_client.call("lingxi-researchreport-skill", "researchreport", "research")

    def test_skill_dir_missing_raises(
        self,
        tmp_entry_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        tmp_entry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_entry_path.write_text(json.dumps({"apiKey": "K"}))
        _set_env_key(monkeypatch, "K")
        monkeypatch.setattr(
            "mo_stock.data_sources.gtht_client.settings.mo_skills_root",
            tmp_path / "nonexistent",
        )
        with pytest.raises(GthtError, match="skill 目录不存在"):
            GthtClient().call("lingxi-researchreport-skill", "researchreport", "research")
```

- [ ] **Step 2: 运行新增测试，验证全部失败**

Run: `pytest tests/unit/test_gtht_client.py::TestCall -v`
Expected: 7 failed（`call` 方法尚未实现）

- [ ] **Step 3: 在 GthtClient 实现 call 方法**

在 `gtht_client.py` 中 `_write_entry` 之后追加：

```python
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

        skill_dir = settings.mo_skills_root / "gtht-skills" / skill
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
```

- [ ] **Step 4: 运行测试，验证全绿**

Run: `pytest tests/unit/test_gtht_client.py -v`
Expected: 12 passed（5 ensure_auth + 7 call）

- [ ] **Step 5: 提交**

```bash
git add src/mo_stock/data_sources/gtht_client.py tests/unit/test_gtht_client.py
git commit -m "feat(data_sources): GthtClient.call subprocess 调用层"
```

---

## Task 5: GthtClient.check_auth + clear_auth

**Files:**
- Modify: `src/mo_stock/data_sources/gtht_client.py`
- Modify: `tests/unit/test_gtht_client.py`

- [ ] **Step 1: 追加 TestAuthSubcommands 测试类**

在 `test_gtht_client.py` 末尾追加：

```python
class TestAuthSubcommands:
    def test_check_auth_true_when_node_exit_zero(
        self, authed_client: GthtClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            subprocess, "run", MagicMock(return_value=_make_completed(returncode=0))
        )
        assert authed_client.check_auth() is True

    def test_check_auth_false_when_node_exit_nonzero(
        self, authed_client: GthtClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            subprocess, "run", MagicMock(return_value=_make_completed(returncode=1))
        )
        assert authed_client.check_auth() is False

    def test_clear_auth_invokes_authchecker_clear(
        self, authed_client: GthtClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        mock_run = MagicMock(return_value=_make_completed())
        monkeypatch.setattr(subprocess, "run", mock_run)
        authed_client.clear_auth()
        cmd = mock_run.call_args.args[0]
        assert cmd == ["node", "skill-entry.js", "authChecker", "clear"]
```

- [ ] **Step 2: 运行测试，验证 3 case 失败**

Run: `pytest tests/unit/test_gtht_client.py::TestAuthSubcommands -v`
Expected: 3 failed

- [ ] **Step 3: 在 GthtClient 实现 check_auth 与 clear_auth**

在 `gtht_client.py` 末尾追加：

```python
    # ---------- 授权状态查询 ----------

    _DEFAULT_SKILL_FOR_AUTH = "lingxi-researchreport-skill"

    def check_auth(self) -> bool:
        """透传到 node skill-entry.js authChecker check。"""
        skill_dir = (
            settings.mo_skills_root / "gtht-skills" / self._DEFAULT_SKILL_FOR_AUTH
        )
        try:
            proc = subprocess.run(  # noqa: S603
                ["node", "skill-entry.js", "authChecker", "check"],
                cwd=skill_dir,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GthtError("node 未安装或不在 PATH 中") from exc
        return proc.returncode == 0

    def clear_auth(self) -> None:
        """透传到 node skill-entry.js authChecker clear。"""
        skill_dir = (
            settings.mo_skills_root / "gtht-skills" / self._DEFAULT_SKILL_FOR_AUTH
        )
        try:
            subprocess.run(  # noqa: S603
                ["node", "skill-entry.js", "authChecker", "clear"],
                cwd=skill_dir,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except FileNotFoundError as exc:
            raise GthtError("node 未安装或不在 PATH 中") from exc
```

- [ ] **Step 4: 运行测试，验证全绿**

Run: `pytest tests/unit/test_gtht_client.py -v`
Expected: 15 passed

- [ ] **Step 5: 提交**

```bash
git add src/mo_stock/data_sources/gtht_client.py tests/unit/test_gtht_client.py
git commit -m "feat(data_sources): GthtClient 增加 check_auth/clear_auth 透传"
```

---

## Task 6: GthtAgent —— 模块骨架 + tools schema 常量

**Files:**
- Create: `src/mo_stock/data_sources/gtht_agent.py`
- Create: `tests/unit/test_gtht_agent.py`

- [ ] **Step 1: 写 test_gtht_agent.py 的 schema 自检测试**

新建 [tests/unit/test_gtht_agent.py](../../../tests/unit/test_gtht_agent.py)：

```python
"""GthtAgent 测试：tools schema 与 function calling 循环。"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from mo_stock.data_sources.gtht_agent import _TOOL_DISPATCH, _TOOLS, GthtAgent
from mo_stock.data_sources.gtht_client import GthtError


class TestToolsSchema:
    def test_tools_have_required_openai_fields(self) -> None:
        """每个 tool 必须有 type=function + function.name/description/parameters"""
        for t in _TOOLS:
            assert t["type"] == "function"
            fn = t["function"]
            assert isinstance(fn["name"], str) and fn["name"]
            assert isinstance(fn["description"], str) and fn["description"]
            assert fn["parameters"]["type"] == "object"
            assert "query" in fn["parameters"]["properties"]

    def test_dispatch_table_covers_all_tools(self) -> None:
        """_TOOLS 中每个 tool 必须在 _TOOL_DISPATCH 中有 (skill, gateway, tool) 映射"""
        tool_names = {t["function"]["name"] for t in _TOOLS}
        assert tool_names == set(_TOOL_DISPATCH.keys())

    def test_dispatch_targets_are_known_skills(self) -> None:
        """所有 dispatch 目标 skill 都应在 PLAN.md 用到的 3 个里"""
        expected_skills = {
            "lingxi-researchreport-skill",
            "lingxi-ranklist-skill",
            "lingxi-smartstockselection-skill",
        }
        actual_skills = {skill for skill, _, _ in _TOOL_DISPATCH.values()}
        assert actual_skills == expected_skills
```

- [ ] **Step 2: 运行测试，验证 import 失败**

Run: `pytest tests/unit/test_gtht_agent.py::TestToolsSchema -v`
Expected: ModuleNotFoundError

- [ ] **Step 3: 创建 gtht_agent.py 骨架（含 schema 与映射，不含 ask）**

新建 [src/mo_stock/data_sources/gtht_agent.py](../../../src/mo_stock/data_sources/gtht_agent.py)：

```python
"""GTHT 辅助 LLM 代理。

使用 OpenAI 兼容 LLM（DeepSeek / Qwen / Kimi 等）+ function calling，
让 LLM 自动选择合适的 GTHT skill tool 并填参，再交给 GthtClient 执行。

详细设计：docs/superpowers/specs/2026-04-23-gtht-client-design.md §4.2
"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger
from openai import OpenAI

from config.settings import settings
from mo_stock.data_sources.gtht_client import GthtClient, GthtError

# ---------------------------------------------------------------------------
# Tools schema：OpenAI function calling 格式
# 每个 tool 对应一个 GTHT skill 的核心入口
# ---------------------------------------------------------------------------

_TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "research_report",
            "description": (
                "查询国泰海通券商研究报告。适用场景："
                "（1）某只股票/板块最近 N 天的研报评级与目标价；"
                "（2）特定主题/概念的研报观点汇总。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "自然语言查询，如 '600519 最近30天研报' 或 'AI 算力研报'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "rank_list",
            "description": (
                "查询资金/涨跌幅/换手率等热榜（国泰海通灵犀热榜）。"
                "适用于交叉验证主力资金动向、热门板块识别等。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "自然语言查询，如 '今日资金净流入 TOP 20' 或 '近5日涨幅榜'",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "financial_search",
            "description": (
                "自然语言查询 A 股行情、技术指标、F10 财务数据、公司基本信息。"
                "适用于多指标筛股、个股深度数据查询。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "自然语言查询，如 '涨幅大于5%且换手率大于3%的股票'",
                    },
                },
                "required": ["query"],
            },
        },
    },
]

# tool name → (skill_dir_name, gateway_name, tool_name)
_TOOL_DISPATCH: dict[str, tuple[str, str, str]] = {
    "research_report": ("lingxi-researchreport-skill", "researchreport", "research"),
    "rank_list": ("lingxi-ranklist-skill", "ranklist", "ranklist"),
    "financial_search": (
        "lingxi-smartstockselection-skill",
        "financial",
        "financial-search",
    ),
}


class GthtAgent:
    """GTHT 辅助 LLM 代理。

    用法：
        agent = GthtAgent()
        result = agent.ask("查 600519 最近研报评级和热榜表现")
        print(result["answer"])
        print(result["tool_trace"])
    """

    def __init__(self, client: GthtClient | None = None) -> None:
        if not (
            settings.gtht_llm_api_key
            and settings.gtht_llm_base_url
            and settings.gtht_llm_model
        ):
            raise GthtError(
                "GTHT_LLM_API_KEY / GTHT_LLM_BASE_URL / GTHT_LLM_MODEL 必须全部配置"
            )
        self._client = client or GthtClient()
        self._llm = OpenAI(
            api_key=settings.gtht_llm_api_key,
            base_url=settings.gtht_llm_base_url,
        )
        self._model = settings.gtht_llm_model
```

- [ ] **Step 4: 运行测试，验证 schema 测试全绿**

Run: `pytest tests/unit/test_gtht_agent.py::TestToolsSchema -v`
Expected: 3 passed

- [ ] **Step 5: 提交**

```bash
git add src/mo_stock/data_sources/gtht_agent.py tests/unit/test_gtht_agent.py
git commit -m "feat(data_sources): GthtAgent 骨架与 OpenAI tools schema"
```

---

## Task 7: GthtAgent.ask —— function calling 循环

**Files:**
- Modify: `src/mo_stock/data_sources/gtht_agent.py`
- Modify: `tests/unit/test_gtht_agent.py`

- [ ] **Step 1: 在 test_gtht_agent.py 追加 TestAsk 测试类**

在 `test_gtht_agent.py` 末尾追加：

```python
@pytest.fixture
def configured_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """让 GthtAgent 构造不抛错。"""
    monkeypatch.setattr(
        "mo_stock.data_sources.gtht_agent.settings.gtht_llm_api_key", "sk-test"
    )
    monkeypatch.setattr(
        "mo_stock.data_sources.gtht_agent.settings.gtht_llm_base_url",
        "https://api.example.com/v1",
    )
    monkeypatch.setattr(
        "mo_stock.data_sources.gtht_agent.settings.gtht_llm_model", "test-model"
    )


def _llm_message(content: str | None = None, tool_calls: list | None = None) -> MagicMock:
    """构造 openai 风格的 ChatCompletionMessage 假对象。"""
    msg = MagicMock()
    msg.content = content
    msg.tool_calls = tool_calls
    msg.model_dump = lambda exclude_none=False: {  # noqa: ARG005
        "role": "assistant",
        "content": content,
        "tool_calls": tool_calls,
    }
    return msg


def _llm_response(message: MagicMock) -> MagicMock:
    resp = MagicMock()
    resp.choices = [MagicMock(message=message)]
    return resp


def _tool_call(call_id: str, name: str, args: dict) -> MagicMock:
    tc = MagicMock()
    tc.id = call_id
    tc.function.name = name
    tc.function.arguments = json.dumps(args)
    return tc


class TestAsk:
    def test_no_tool_calls_returns_direct_answer(
        self, configured_env: None
    ) -> None:
        """LLM 直接回答（不调 tool）→ 返回 answer，tool_trace 为空"""
        client = MagicMock()
        agent = GthtAgent(client=client)

        agent._llm = MagicMock()
        agent._llm.chat.completions.create.return_value = _llm_response(
            _llm_message(content="直接回答")
        )

        result = agent.ask("hello")
        assert result == {"answer": "直接回答", "tool_trace": []}
        client.call.assert_not_called()

    def test_single_tool_call_dispatches_to_client(
        self, configured_env: None
    ) -> None:
        """LLM 选 research_report → 应调 GthtClient.call(researchreport-skill, ...)"""
        client = MagicMock()
        client.call.return_value = {"data": "研报内容"}

        agent = GthtAgent(client=client)
        agent._llm = MagicMock()

        # 第一轮：LLM 返回 tool_call
        first = _llm_response(
            _llm_message(
                tool_calls=[_tool_call("c1", "research_report", {"query": "600519"})]
            )
        )
        # 第二轮：LLM 看到 tool 结果后返回最终答案
        second = _llm_response(_llm_message(content="研报概要：..."))
        agent._llm.chat.completions.create.side_effect = [first, second]

        result = agent.ask("查 600519 研报")
        assert result["answer"] == "研报概要：..."
        client.call.assert_called_once_with(
            "lingxi-researchreport-skill", "researchreport", "research", query="600519"
        )
        assert len(result["tool_trace"]) == 1
        assert result["tool_trace"][0]["tool"] == "research_report"

    def test_unknown_tool_returns_error_to_llm(
        self, configured_env: None
    ) -> None:
        """LLM 调了不在 dispatch 表里的 tool → 错误回传给 LLM 继续推理"""
        client = MagicMock()
        agent = GthtAgent(client=client)
        agent._llm = MagicMock()

        first = _llm_response(
            _llm_message(tool_calls=[_tool_call("c1", "unknown_tool", {})])
        )
        second = _llm_response(_llm_message(content="无法调用"))
        agent._llm.chat.completions.create.side_effect = [first, second]

        result = agent.ask("?")
        client.call.assert_not_called()
        assert "未知工具" in result["tool_trace"][0]["result"]["error"]

    def test_client_error_returned_to_llm(self, configured_env: None) -> None:
        """GthtClient.call 抛 GthtError → 错误回传 LLM 继续推理（不向上抛）"""
        client = MagicMock()
        client.call.side_effect = GthtError("Network down")
        agent = GthtAgent(client=client)
        agent._llm = MagicMock()

        first = _llm_response(
            _llm_message(
                tool_calls=[_tool_call("c1", "research_report", {"query": "x"})]
            )
        )
        second = _llm_response(_llm_message(content="服务暂不可用"))
        agent._llm.chat.completions.create.side_effect = [first, second]

        result = agent.ask("?")
        assert result["answer"] == "服务暂不可用"
        assert result["tool_trace"][0]["result"] == {"error": "Network down"}

    def test_max_iters_exceeded_raises(self, configured_env: None) -> None:
        """LLM 一直返回 tool_calls，超过 max_iters → 抛 GthtError"""
        client = MagicMock(**{"call.return_value": {"ok": True}})
        agent = GthtAgent(client=client)
        agent._llm = MagicMock()

        always_tool = _llm_response(
            _llm_message(
                tool_calls=[_tool_call("c1", "research_report", {"query": "x"})]
            )
        )
        agent._llm.chat.completions.create.return_value = always_tool

        with pytest.raises(GthtError, match="max_iters"):
            agent.ask("?", max_iters=2)
```

- [ ] **Step 2: 运行测试，验证全部失败**

Run: `pytest tests/unit/test_gtht_agent.py::TestAsk -v`
Expected: 5 failed（`ask` 方法尚未实现）

- [ ] **Step 3: 实现 GthtAgent.ask**

在 `gtht_agent.py` 的 `__init__` 之后追加：

```python
    def ask(self, query: str, max_iters: int = 5) -> dict:
        """让辅助 LLM 自主选择并调用 GTHT skill tool 回答 query。

        返回 {"answer": str, "tool_trace": list[dict]}。
        超过 max_iters 抛 GthtError 防止死循环。
        """
        messages: list[dict[str, Any]] = [{"role": "user", "content": query}]
        tool_trace: list[dict[str, Any]] = []

        for it in range(max_iters):
            response = self._llm.chat.completions.create(
                model=self._model,
                messages=messages,
                tools=_TOOLS,
            )
            msg = response.choices[0].message

            # 无 tool_calls → 终止，返回最终答案
            if not msg.tool_calls:
                return {"answer": msg.content or "", "tool_trace": tool_trace}

            # 把 assistant 的 tool_calls 消息回写到 messages
            messages.append(msg.model_dump(exclude_none=True))

            for tc in msg.tool_calls:
                tool_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                if tool_name not in _TOOL_DISPATCH:
                    tool_result: Any = {"error": f"未知工具: {tool_name}"}
                else:
                    skill, gateway, tool = _TOOL_DISPATCH[tool_name]
                    try:
                        tool_result = self._client.call(skill, gateway, tool, **args)
                    except GthtError as exc:
                        tool_result = {"error": str(exc)}

                tool_trace.append(
                    {"tool": tool_name, "args": args, "result": tool_result}
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": json.dumps(tool_result, ensure_ascii=False),
                    }
                )

            logger.debug("GthtAgent iter {} done, {} tool_calls", it + 1, len(msg.tool_calls))

        raise GthtError(f"LLM tool loop exceeded max_iters={max_iters}")
```

- [ ] **Step 4: 运行测试，验证全绿**

Run: `pytest tests/unit/test_gtht_agent.py -v`
Expected: 8 passed（3 schema + 5 ask）

- [ ] **Step 5: 提交**

```bash
git add src/mo_stock/data_sources/gtht_agent.py tests/unit/test_gtht_agent.py
git commit -m "feat(data_sources): GthtAgent.ask function calling 循环实现"
```

---

## Task 8: 最终静态检查 + 全量测试 + 文档校对

**Files:** （根据检查结果可能涉及任意文件的小修）

- [ ] **Step 1: 跑全量单元测试，确认无回归**

Run: `pytest tests/unit -q`
Expected: 所有原有测试 + 新增 23 个 GTHT 测试全部通过

- [ ] **Step 2: ruff 静态检查**

Run: `ruff check src tests`
Expected: All checks passed.（如有 lint 错则原地修复）

- [ ] **Step 3: mypy 类型检查**

Run: `mypy src`
Expected: Success.（项目 strict=false，新文件不应引入新 error）

- [ ] **Step 4: 真机 sanity check（可选 — 需配好 .env）**

Run（仅在 `.env` 配了 `GTHT_API_KEY` 且 node 可用时）：
```bash
python -c "from mo_stock.data_sources.gtht_client import GthtClient; print(GthtClient().check_auth())"
```
Expected: 打印 `True`（或 `False` 如果 token 失效，提示重新配 .env）

- [ ] **Step 5: 如有任何修复，提交**

```bash
git status
# 若有改动：
git add <files>
git commit -m "chore: GTHT 客户端最终静态检查修复"
# 若无改动：跳过
```

---

## 完成验收清单

- [ ] [src/mo_stock/data_sources/gtht_client.py](../../../src/mo_stock/data_sources/gtht_client.py) 存在，含 `GthtClient` + `GthtError`
- [ ] [src/mo_stock/data_sources/gtht_agent.py](../../../src/mo_stock/data_sources/gtht_agent.py) 存在，含 `GthtAgent` + `_TOOLS` + `_TOOL_DISPATCH`
- [ ] [config/settings.py](../../../config/settings.py) 含 `gtht_api_key / gtht_llm_api_key / gtht_llm_base_url / gtht_llm_model` 4 个新字段
- [ ] [.env.example](../../../.env.example) 新增 GTHT 配置段（API_KEY + 3 LLM 占位）
- [ ] [pyproject.toml](../../../pyproject.toml) 含 `openai>=1.50`
- [ ] `tests/unit/test_gtht_client.py`：15 个用例全绿
- [ ] `tests/unit/test_gtht_agent.py`：8 个用例全绿
- [ ] `pytest tests/unit -q` 全绿，无回归
- [ ] `ruff check src tests` 无 error
- [ ] `mypy src` 无新增 error

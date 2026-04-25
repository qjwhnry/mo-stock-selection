"""GthtClient 的 subprocess 封装与 auth 落盘测试。"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from mo_stock.data_sources.gtht_client import (
    GthtClient,
    GthtError,
    _extract_result_field,
    _parse_skill_output,
    _strip_debug_lines,
)

# 真实 skill 的 stdout 样本（提取自 4 个端到端调用），见 README 同名目录。
FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "gtht_skill_outputs"


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

    def test_corrupt_file_and_env_empty_raises(
        self, tmp_entry_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """case: 文件存在但内容损坏 + .env 空 → 抛 GthtError（不静默成功）"""
        tmp_entry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_entry_path.write_text("{ this is not valid json")

        _set_env_key(monkeypatch, "")
        client = GthtClient()
        with pytest.raises(GthtError, match="GTHT_API_KEY 未配置"):
            client.ensure_auth()

    def test_corrupt_file_and_env_set_overwrites(
        self, tmp_entry_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """case: 文件存在但内容损坏 + .env 有值 → 用 .env 覆盖"""
        tmp_entry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_entry_path.write_text("{ broken json")

        _set_env_key(monkeypatch, "FRESH_KEY")
        client = GthtClient()
        client.ensure_auth()
        # 损坏文件应被 .env 值覆盖
        assert json.loads(tmp_entry_path.read_text())["apiKey"] == "FRESH_KEY"


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
        self,
        authed_client: GthtClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        mock_run = MagicMock(return_value=_make_completed(stdout='{"data": [1, 2, 3]}'))
        monkeypatch.setattr(subprocess, "run", mock_run)

        result = authed_client.call(
            "lingxi-researchreport-skill",
            "researchreport",
            "research",
            query="600519",
        )
        # 新契约：顶层 dict 但无 text 字段 → 走 raw_json 分支，原字段透传。
        assert result["format"] == "raw_json"
        assert result["data"] == [1, 2, 3]

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

        # 验证 cwd 指向 skill 目录（spec §9 关键，跨平台 node 解析依赖）
        expected_cwd = (
            tmp_path / "mo-skills" / "gtht-skills" / "lingxi-researchreport-skill"
        )
        assert mock_run.call_args.kwargs["cwd"] == expected_cwd
        # 固定子进程解码策略，避免 Windows 默认编码（如 gbk）引发 UnicodeDecodeError。
        assert mock_run.call_args.kwargs["encoding"] == "utf-8"
        assert mock_run.call_args.kwargs["errors"] == "replace"

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

    def test_non_json_stdout_returned_as_text_format(
        self, authed_client: GthtClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """case: stdout 非 JSON（如 marketdata-tool 直接 print 中文展示）→ 不抛，
        而是按 text 格式返回。这是新契约：GTHT 多 skill 本来就返回纯文本。
        """
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(return_value=_make_completed(stdout="贵州茅台 1458.49")),
        )
        result = authed_client.call(
            "lingxi-researchreport-skill", "researchreport", "research"
        )
        assert result == {"format": "text", "text": "贵州茅台 1458.49"}

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

    def test_skill_path_traversal_raises(
        self, authed_client: GthtClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """case: skill 名带 ../ 试图越界 → 抛 GthtError（防御性检查）"""
        # subprocess 不应被调到（path 校验在前）
        monkeypatch.setattr(subprocess, "run", MagicMock())
        with pytest.raises(GthtError, match="非法 skill 路径"):
            authed_client.call("../../../etc", "researchreport", "research")

    def test_top_level_json_array_wrapped_as_data(
        self, authed_client: GthtClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """case: stdout 顶层是 JSON 数组 → 不抛，包装成 raw_json + data 字段。"""
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(return_value=_make_completed(stdout="[1, 2, 3]")),
        )
        result = authed_client.call(
            "lingxi-researchreport-skill", "researchreport", "research"
        )
        assert result == {"format": "raw_json", "data": [1, 2, 3]}


class TestAuthSubcommands:
    def test_check_auth_true_when_node_exit_zero(
        self,
        authed_client: GthtClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        mock_run = MagicMock(return_value=_make_completed(returncode=0))
        monkeypatch.setattr(subprocess, "run", mock_run)
        assert authed_client.check_auth() is True
        # cwd 必须指向默认 auth skill 目录
        expected_cwd = (
            tmp_path / "mo-skills" / "gtht-skills" / "lingxi-researchreport-skill"
        )
        assert mock_run.call_args.kwargs["cwd"] == expected_cwd
        # check_auth 路径同样应显式指定 UTF-8 解码策略。
        assert mock_run.call_args.kwargs["encoding"] == "utf-8"
        assert mock_run.call_args.kwargs["errors"] == "replace"

    def test_check_auth_false_when_node_exit_nonzero(
        self,
        authed_client: GthtClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        mock_run = MagicMock(return_value=_make_completed(returncode=1))
        monkeypatch.setattr(subprocess, "run", mock_run)
        assert authed_client.check_auth() is False
        # cwd 必须指向默认 auth skill 目录
        expected_cwd = (
            tmp_path / "mo-skills" / "gtht-skills" / "lingxi-researchreport-skill"
        )
        assert mock_run.call_args.kwargs["cwd"] == expected_cwd

    def test_clear_auth_invokes_authchecker_clear(
        self,
        authed_client: GthtClient,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        mock_run = MagicMock(return_value=_make_completed())
        monkeypatch.setattr(subprocess, "run", mock_run)
        authed_client.clear_auth()
        cmd = mock_run.call_args.args[0]
        assert cmd == ["node", "skill-entry.js", "authChecker", "clear"]
        # cwd 必须指向默认 auth skill 目录
        expected_cwd = (
            tmp_path / "mo-skills" / "gtht-skills" / "lingxi-researchreport-skill"
        )
        assert mock_run.call_args.kwargs["cwd"] == expected_cwd

    def test_check_auth_raises_when_skill_dir_missing(
        self,
        tmp_entry_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """case: _DEFAULT_SKILL_FOR_AUTH 目录不存在 → 抛 GthtError（与 call 对齐）"""
        tmp_entry_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_entry_path.write_text(json.dumps({"apiKey": "K"}))
        _set_env_key(monkeypatch, "K")
        # 故意不创建 skill_dir
        monkeypatch.setattr(
            "mo_stock.data_sources.gtht_client.settings.mo_skills_root",
            tmp_path / "no-skills",
        )
        with pytest.raises(GthtError, match="skill 目录不存在"):
            GthtClient().check_auth()


# =====================================================================
# _parse_skill_output 单元测试（不依赖 subprocess，纯纯纯输入→输出对照）
# =====================================================================


class TestStripDebugLines:
    """剥离 bundle 漏到 stdout 的 'adeline-url ...' 等已知调试行。"""

    def test_strips_adeline_url_prefix(self) -> None:
        raw = "adeline-url https://x.com/a\n实际数据"
        assert _strip_debug_lines(raw) == "实际数据"

    def test_keeps_lines_without_known_prefix(self) -> None:
        raw = "line1\nline2\nline3"
        assert _strip_debug_lines(raw) == "line1\nline2\nline3"

    def test_trims_trailing_whitespace(self) -> None:
        raw = "data\n\n\n"
        assert _strip_debug_lines(raw) == "data"


class TestExtractResultField:
    """从伪 JSON 文本里宽松提取 `"result":` 后面的内容。"""

    def test_extracts_markdown_table(self) -> None:
        text = '{\n  "result": | a | b |\n| --- | --- |\n| 1 | 2 |\n}'
        assert "| a | b |" in _extract_result_field(text)
        assert "| 1 | 2 |" in _extract_result_field(text)

    def test_returns_input_when_no_match(self) -> None:
        text = "no result key here"
        assert _extract_result_field(text) == "no result key here"

    def test_extracts_empty_result(self) -> None:
        text = '{\n  "result": \n}'
        assert _extract_result_field(text) == ""


class TestParseSkillOutput:
    """对照 _parse_skill_output 的 4 种返回 format 行为，含真实 fixture 与边界。"""

    # ---------- format = "text" ----------

    def test_pure_text_returns_text_format(self) -> None:
        result = _parse_skill_output("plain text without any json")
        assert result == {"format": "text", "text": "plain text without any json"}

    def test_text_with_debug_prefix_strips_it(self) -> None:
        raw = "adeline-url https://x.com\n贵州茅台 1458.49"
        result = _parse_skill_output(raw)
        assert result["format"] == "text"
        assert "adeline-url" not in result["text"]
        assert result["text"] == "贵州茅台 1458.49"

    def test_real_marketdata_fixture_is_text(self) -> None:
        """真实样本：marketdata-tool 的 stdout（含 adeline-url 调试行 + 中文展示）。"""
        raw = (FIXTURES_DIR / "text_marketdata.txt").read_text(encoding="utf-8")
        result = _parse_skill_output(raw)
        assert result["format"] == "text"
        # 调试行应该被剥离
        assert "adeline-url" not in result["text"]
        # 业务关键字段应该都在
        assert "贵州茅台" in result["text"]
        assert "1,458.49" in result["text"]

    # ---------- format = "raw_json" ----------

    def test_top_level_dict_without_text_field(self) -> None:
        result = _parse_skill_output('{"foo": 1, "bar": [2, 3]}')
        assert result == {"format": "raw_json", "foo": 1, "bar": [2, 3]}

    def test_top_level_array_wrapped_as_data(self) -> None:
        result = _parse_skill_output("[1, 2, 3]")
        assert result == {"format": "raw_json", "data": [1, 2, 3]}

    def test_top_level_scalar_wrapped_as_data(self) -> None:
        result = _parse_skill_output("42")
        assert result == {"format": "raw_json", "data": 42}

    # ---------- format = "json_result" ----------

    def test_well_formed_double_json_with_result(self) -> None:
        """外层 JSON + 内层合法 JSON dict + 含 result 字段 → 标准提取。"""
        inner = json.dumps({"result": "hello"})
        outer = json.dumps({"text": inner})
        result = _parse_skill_output(outer)
        assert result == {"format": "json_result", "result": "hello"}

    def test_well_formed_double_json_without_result(self) -> None:
        """内层合法 dict 但无 result → 摊平到顶层。"""
        inner = json.dumps({"foo": "bar", "n": 1})
        outer = json.dumps({"text": inner})
        result = _parse_skill_output(outer)
        assert result == {"format": "json_result", "foo": "bar", "n": 1}

    def test_real_backtest_fixture_text_result(self) -> None:
        """真实样本：smartstockselection backtest。

        外层 JSON 合法，但内层 text 字段值是伪 JSON：
        `"result": "{"年化收益":0.0378,...,"回测状态":成功}"` —— "成功" 缺引号导致
        内层 json.loads 也挂，所以走 text_result 分支用正则兜底。
        """
        raw = (FIXTURES_DIR / "text_result_backtest.txt").read_text(encoding="utf-8")
        result = _parse_skill_output(raw)
        assert result["format"] == "text_result"
        assert "年化" in result["result"] or "回测" in result["result"]

    # ---------- format = "text_result" ----------

    def test_outer_json_inner_pseudo_json_falls_back_to_regex(self) -> None:
        """外层 JSON 合法、内层伪 JSON（result 后跟裸文本）→ 正则提取。"""
        inner_pseudo = '{\n  "result": markdown 表格| a | b |\n}'
        outer = json.dumps({"text": inner_pseudo})
        result = _parse_skill_output(outer)
        assert result["format"] == "text_result"
        assert "markdown 表格" in result["result"]

    def test_real_financial_fixture_text_result(self) -> None:
        """真实样本：financial-search，内层 result 后跟 markdown 表格非合法 JSON。"""
        raw = (FIXTURES_DIR / "text_result_financial.txt").read_text(encoding="utf-8")
        result = _parse_skill_output(raw)
        assert result["format"] == "text_result"
        assert "贵州茅台" in result["result"]
        assert "600519" in result["result"]

    def test_real_empty_research_fixture_text_result(self) -> None:
        """真实样本：researchreport 无结果，result 字段为空。"""
        raw = (FIXTURES_DIR / "empty_result_researchreport.txt").read_text(encoding="utf-8")
        result = _parse_skill_output(raw)
        assert result["format"] == "text_result"
        # 空结果 → result 为空字符串，但调用本身不算失败
        assert result["result"] == ""


# =====================================================================
# call() + 真实 fixture 集成测试（mock subprocess.run，验证整链路）
# =====================================================================


class TestCallWithRealFixtures:
    """用真实 GTHT 输出 fixture mock subprocess，验 call() 返回 dict 结构正确。"""

    @pytest.mark.parametrize(
        "fixture_name, expected_format, must_contain_in_value",
        [
            ("text_marketdata.txt", "text", "贵州茅台"),
            # backtest 实测也是 text_result（内层 result 字符串里有未转义引号导致内层 JSON 挂）
            ("text_result_backtest.txt", "text_result", "年化"),
            ("text_result_financial.txt", "text_result", "600519"),
            ("empty_result_researchreport.txt", "text_result", ""),
        ],
    )
    def test_call_parses_each_format(
        self,
        authed_client: GthtClient,
        monkeypatch: pytest.MonkeyPatch,
        fixture_name: str,
        expected_format: str,
        must_contain_in_value: str,
    ) -> None:
        raw = (FIXTURES_DIR / fixture_name).read_text(encoding="utf-8")
        monkeypatch.setattr(
            subprocess, "run", MagicMock(return_value=_make_completed(stdout=raw))
        )
        result = authed_client.call(
            "lingxi-researchreport-skill", "researchreport", "research"
        )
        assert result["format"] == expected_format
        # 找出值字段（text / result），校验关键字
        value = result.get("text") or result.get("result") or ""
        assert must_contain_in_value in value

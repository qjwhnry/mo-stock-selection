"""GthtClient 的 subprocess 封装与 auth 落盘测试。"""
from __future__ import annotations

import json
import subprocess
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

    def test_skill_path_traversal_raises(
        self, authed_client: GthtClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """case: skill 名带 ../ 试图越界 → 抛 GthtError（防御性检查）"""
        # subprocess 不应被调到（path 校验在前）
        monkeypatch.setattr(subprocess, "run", MagicMock())
        with pytest.raises(GthtError, match="非法 skill 路径"):
            authed_client.call("../../../etc", "researchreport", "research")

    def test_top_level_json_array_raises(
        self, authed_client: GthtClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """case: stdout 顶层是 JSON 数组 → 抛 GthtError（GTHT call 契约要求 dict）"""
        monkeypatch.setattr(
            subprocess,
            "run",
            MagicMock(return_value=_make_completed(stdout='[1, 2, 3]')),
        )
        with pytest.raises(GthtError, match="GTHT 返回非 dict"):
            authed_client.call("lingxi-researchreport-skill", "researchreport", "research")


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

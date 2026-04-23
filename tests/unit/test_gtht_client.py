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

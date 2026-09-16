"""run-once 手工触发入口的回归测试。"""
from __future__ import annotations

from importlib import import_module

from click.testing import CliRunner


def test_run_once_skips_ai_by_default() -> None:
    """AI 评分暂停期间，手动执行必须默认走纯规则模式。"""
    cli_module = import_module("mo_stock.cli")
    skip_ai_option = next(
        param for param in cli_module.run_once.params if param.name == "skip_ai"
    )

    assert skip_ai_option.default is True


def test_run_once_checks_lhb_availability(monkeypatch) -> None:
    """手动执行当日选股也必须经过龙虎榜发布时间保护。"""
    cli_module = import_module("mo_stock.cli")

    monkeypatch.setattr(cli_module, "_ensure_trade_date", lambda *args, **kwargs: None)

    def block_before_lhb(*args, **kwargs) -> None:
        raise RuntimeError("龙虎榜数据尚未发布")

    monkeypatch.setattr(cli_module, "assert_lhb_data_available", block_before_lhb)

    result = CliRunner().invoke(
        cli_module.cli,
        ["run-once", "--date", "2026-05-11", "--skip-ingest"],
    )

    assert result.exit_code != 0
    assert isinstance(result.exception, RuntimeError)
    assert "龙虎榜数据尚未发布" in str(result.exception)


def test_run_once_uses_shared_filter_builder(monkeypatch) -> None:
    """手动执行不能再维护一份独立、易漂移的过滤器清单。"""
    cli_module = import_module("mo_stock.cli")
    calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(cli_module, "_ensure_trade_date", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        cli_module,
        "assert_lhb_data_available",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(cli_module, "load_weights_yaml", lambda *args, **kwargs: {})

    def stop_after_build(strategy: str, cfg: dict):
        calls.append((strategy, cfg))
        raise RuntimeError("已调用共享过滤器构建器")

    monkeypatch.setattr(cli_module, "build_filters", stop_after_build)

    result = CliRunner().invoke(
        cli_module.cli,
        ["run-once", "--date", "2026-05-11", "--skip-ingest"],
    )

    assert calls == [("short", {})]
    assert isinstance(result.exception, RuntimeError)
    assert "已调用共享过滤器构建器" in str(result.exception)

"""Tushare 客户端封装。

**设计原则**：
- 直接使用 `import tushare as ts`（用户明确要求用 Python 包）
- 只包一层：重试（tenacity）+ 节流（速率限制）+ 日志
- 不改变 Tushare 接口契约，调用方直接拿 DataFrame
- 批量接口统一接受 `trade_date` 或 `start_date/end_date`

**Tushare 积分限制参考**：
- 基础接口（daily / trade_cal）：120 次/分钟
- 高频接口（limit_list_d / top_list / moneyflow）：建议控制在 60 次/分钟
- 新闻 / 公告：30 次/分钟
"""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import pandas as pd
import tushare as ts
from loguru import logger
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from config.settings import settings


class TushareError(Exception):
    """Tushare 调用失败。"""


# ---------------------------------------------------------------------------
# 节流器：保证单位时间内调用次数不超过上限
# ---------------------------------------------------------------------------

class RateLimiter:
    """简易令牌桶式节流器。

    用法：
        limiter = RateLimiter(calls_per_minute=60)
        limiter.acquire()  # 会阻塞到有配额为止
    """

    def __init__(self, calls_per_minute: int) -> None:
        self.interval = 60.0 / calls_per_minute  # 两次调用的最小间隔
        self._last_call: float = 0.0

    def acquire(self) -> None:
        now = time.monotonic()
        wait = self.interval - (now - self._last_call)
        if wait > 0:
            time.sleep(wait)
        self._last_call = time.monotonic()


# 默认节流配置（所有实例共享）：两档，高频接口更严格
_default_limiter = RateLimiter(calls_per_minute=120)
_strict_limiter = RateLimiter(calls_per_minute=60)


# ---------------------------------------------------------------------------
# TushareClient
# ---------------------------------------------------------------------------

class TushareClient:
    """Tushare Pro 接口封装。

    单例（进程级）：所有 ingest 模块共享同一个 pro_api 句柄，
    避免重复 set_token 开销。

    用法：
        client = TushareClient()
        df = client.daily(trade_date="20260422")
    """

    _instance: TushareClient | None = None

    def __new__(cls) -> TushareClient:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()  # noqa: SLF001
        return cls._instance

    def _init(self) -> None:
        """首次实例化时初始化 pro_api。"""
        token = settings.tushare_token
        if not token:
            raise TushareError("TUSHARE_TOKEN 未配置；请检查 .env 文件")

        ts.set_token(token)
        self._pro = ts.pro_api()

        # 可选：覆盖 HTTP 地址（走内网/镜像时使用）
        # 注意：_DataApi__http_url 为 name-mangled 私有属性，tushare 无公开 setter
        http_url = settings.tushare_http_url
        if http_url:
            self._pro._DataApi__http_url = http_url  # noqa: SLF001
            logger.info("TushareClient http_url overridden -> {}", http_url)

        logger.info("TushareClient initialized (token={}***)", token[:4])

    # ---------- 底层调用器 ----------

    @retry(
        retry=retry_if_exception_type(Exception),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=20),
        reraise=True,
    )
    def _call(
        self,
        api_name: str,
        limiter: RateLimiter = _default_limiter,
        **kwargs: Any,
    ) -> pd.DataFrame:
        """底层带重试+节流的调用。"""
        limiter.acquire()

        # 过滤掉值为 None 的参数
        clean_kwargs = {k: v for k, v in kwargs.items() if v is not None}
        logger.debug("Tushare call {} {}", api_name, clean_kwargs)

        method: Callable[..., pd.DataFrame] = getattr(self._pro, api_name)
        df = method(**clean_kwargs)

        if df is None:
            # Tushare 在无数据时可能返回 None，统一成空 DataFrame
            return pd.DataFrame()
        return df

    # ---------- 基础表 ----------

    def stock_basic(self, list_status: str = "L") -> pd.DataFrame:
        """全 A 股基础信息。"""
        return self._call(
            "stock_basic",
            list_status=list_status,
            fields="ts_code,symbol,name,area,industry,list_date",
        )

    def trade_cal(self, start_date: str, end_date: str) -> pd.DataFrame:
        """交易日历。日期格式 'YYYYMMDD'。"""
        return self._call(
            "trade_cal",
            exchange="SSE",
            start_date=start_date,
            end_date=end_date,
            fields="exchange,cal_date,is_open,pretrade_date",
        )

    # ---------- 行情 ----------

    def daily(
        self,
        trade_date: str | None = None,
        ts_code: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """日线 OHLCV。支持按日或按股拉取。"""
        return self._call(
            "daily",
            trade_date=trade_date,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            fields="ts_code,trade_date,open,high,low,close,pre_close,pct_chg,vol,amount",
        )

    def daily_basic(
        self,
        trade_date: str | None = None,
        ts_code: str | None = None,
    ) -> pd.DataFrame:
        """每日基础指标：换手率 / PE / PB / 市值。"""
        return self._call(
            "daily_basic",
            trade_date=trade_date,
            ts_code=ts_code,
            fields="ts_code,trade_date,turnover_rate,turnover_rate_f,volume_ratio,pe_ttm,pb,total_mv,circ_mv",
        )

    # ---------- 异动：涨停 / 龙虎榜 / 资金流 ----------

    def limit_list_d(self, trade_date: str, limit_type: str = "U") -> pd.DataFrame:
        """涨跌停列表。limit_type: U 涨停 / D 跌停 / Z 炸板。"""
        return self._call(
            "limit_list_d",
            trade_date=trade_date,
            limit_type=limit_type,
            limiter=_strict_limiter,
            fields=(
                "ts_code,trade_date,industry,name,close,pct_chg,amount,"
                "limit_amount,float_mv,total_mv,turnover_ratio,fd_amount,"
                "first_time,last_time,open_times,up_stat,limit_times,limit"
            ),
        )

    def top_list(self, trade_date: str) -> pd.DataFrame:
        """龙虎榜上榜股。"""
        return self._call(
            "top_list",
            trade_date=trade_date,
            limiter=_strict_limiter,
            fields=(
                "trade_date,ts_code,name,close,pct_change,turnover_rate,amount,"
                "l_sell,l_buy,l_amount,net_amount,net_rate,amount_rate,"
                "float_values,reason"
            ),
        )

    def top_inst(self, trade_date: str) -> pd.DataFrame:
        """龙虎榜机构席位明细。"""
        return self._call(
            "top_inst",
            trade_date=trade_date,
            limiter=_strict_limiter,
            fields="trade_date,ts_code,exalter,side,buy,buy_rate,sell,sell_rate,net_buy,reason",
        )

    def moneyflow(
        self,
        trade_date: str | None = None,
        ts_code: str | None = None,
    ) -> pd.DataFrame:
        """个股资金流向。"""
        return self._call(
            "moneyflow",
            trade_date=trade_date,
            ts_code=ts_code,
            limiter=_strict_limiter,
            fields=(
                "ts_code,trade_date,"
                "buy_sm_amount,sell_sm_amount,"
                "buy_md_amount,sell_md_amount,"
                "buy_lg_amount,sell_lg_amount,"
                "buy_elg_amount,sell_elg_amount,"
                "net_mf_amount"
            ),
        )

    # ---------- 板块 ----------

    def sw_daily(
        self,
        trade_date: str | None = None,
        ts_code: str | None = None,
    ) -> pd.DataFrame:
        """申万板块日线。ts_code 为板块代码（如 801010.SI）。"""
        return self._call(
            "sw_daily",
            trade_date=trade_date,
            ts_code=ts_code,
            limiter=_strict_limiter,
            fields=(
                "ts_code,name,trade_date,open,high,low,close,"
                "pct_change,vol,amount,turnover_rate"
            ),
        )

    # ---------- 情绪：新闻 / 公告 ----------

    def major_news(self, start_date: str, end_date: str) -> pd.DataFrame:
        """长篇重大新闻。日期格式 'YYYY-MM-DD HH:MM:SS'。"""
        return self._call(
            "major_news",
            start_date=start_date,
            end_date=end_date,
            limiter=_strict_limiter,
            fields="title,content,pub_time,src",
        )

    def anns_d(
        self,
        ts_code: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """A 股公告。"""
        return self._call(
            "anns_d",
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            limiter=_strict_limiter,
            fields="ts_code,name,ann_date,title,url,ann_type",
        )


# ---------------------------------------------------------------------------
# 便捷函数
# ---------------------------------------------------------------------------

def date_to_tushare(d: date | datetime) -> str:
    """Python date/datetime → Tushare 常用的 'YYYYMMDD' 格式。"""
    if isinstance(d, datetime):
        d = d.date()
    return d.strftime("%Y%m%d")


def tushare_to_date(s: str) -> date:
    """Tushare 'YYYYMMDD' → Python date。"""
    return datetime.strptime(s, "%Y%m%d").date()

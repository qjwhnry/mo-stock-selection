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

import socket
import time
from collections.abc import Callable
from datetime import date, datetime
from typing import Any

import pandas as pd
import requests
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


# P1-10：仅对网络层 / 临时性错误重试。
# Tushare SDK 在积分不足、参数错误时抛 ValueError / Exception，重试只是浪费配额。
# 注意 tushare-pro 偶尔抛通用 Exception，但根因是网络的子集会通过下面这些类被 tenacity 命中。
_RETRYABLE_EXCEPTIONS: tuple[type[BaseException], ...] = (
    requests.RequestException,   # 包含 ConnectionError / Timeout / HTTPError 等
    ConnectionError,             # 内置 socket 层
    TimeoutError,                # 内置 timeout
    socket.timeout,
)


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
        retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
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

    def ths_index(
        self,
        type: str = "N",  # noqa: A002
        exchange: str = "A",
    ) -> pd.DataFrame:
        """同花顺概念/行业板块元数据（doc_id=259）。

        默认拉 A 股的概念板块（type='N'，约 408 个）。
        type 取值：N=概念 / I=行业 / R=地域 / S=特色 / ST=风格 / TH=主题 / BB=宽基。
        积分要求：6000。
        """
        return self._call(
            "ths_index",
            type=type,
            exchange=exchange,
            limiter=_default_limiter,
            fields="ts_code,name,count,exchange,list_date,type",
        )

    def ths_member(self, ts_code: str) -> pd.DataFrame:
        """同花顺概念/行业成分（doc_id=261）。

        按概念 ts_code 拉成分股。weight / in_date / out_date 接口当前暂无数据，
        但 fields 里保留以便将来接口完善后自动有数据。
        积分要求：6000，每分钟 200 次。
        """
        return self._call(
            "ths_member",
            ts_code=ts_code,
            limiter=_default_limiter,
            fields="ts_code,con_code,con_name,weight,in_date,out_date,is_new",
        )

    def index_classify(
        self,
        level: str = "L1",
        src: str = "SW2021",
    ) -> pd.DataFrame:
        """申万行业分类元数据（doc_id=181）。

        默认拿申万 2021 版一级行业列表（约 31 个）。返回字段含 index_code（如 801080.SI）、
        industry_name（电子）、level、parent_code 等。
        """
        return self._call(
            "index_classify",
            level=level,
            src=src,
            limiter=_default_limiter,
            fields="index_code,industry_name,parent_code,level,industry_code,is_pub,src",
        )

    def index_member_all(
        self,
        ts_code: str | None = None,
        l1_code: str | None = None,
        is_new: str = "Y",
    ) -> pd.DataFrame:
        """申万行业成分（doc_id=335）。默认只取 is_new='Y' 的当前最新成分。

        - 不传 ts_code / l1_code → 拉全市场最新归属（约 5400 行）
        - 单次最大 2000 行；如全市场超限需按 l1_code 分页（当前未实现）
        - 积分要求：2000
        """
        return self._call(
            "index_member_all",
            ts_code=ts_code,
            l1_code=l1_code,
            is_new=is_new,
            limiter=_default_limiter,
            fields=(
                "l1_code,l1_name,l2_code,l2_name,l3_code,l3_name,"
                "ts_code,name,in_date,out_date,is_new"
            ),
        )

    def sw_daily(
        self,
        trade_date: str | None = None,
        ts_code: str | None = None,
    ) -> pd.DataFrame:
        """申万板块日线（doc_id=327）。ts_code 为板块代码（如 801010.SI）。

        字段说明：
        - 接口默认是申万 2021 版（31 一级 / 134 二级 / 346 三级）
        - 注意：sw_daily 接口**没有** turnover_rate 字段（不要再传）
        """
        return self._call(
            "sw_daily",
            trade_date=trade_date,
            ts_code=ts_code,
            limiter=_strict_limiter,
            fields=(
                "ts_code,name,trade_date,open,high,low,close,change,pct_change,"
                "vol,amount,pe,pb,float_mv,total_mv"
            ),
        )

    # ---------- 题材/概念维度（v2.1 新增）----------

    def ths_daily(
        self,
        ts_code: str | None = None,
        trade_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """同花顺概念/行业指数日行情（doc_id=260）。

        ts_code 为 THS 板块代码（如 885806.TI）；trade_date 拉当日全市场板块。
        ThemeFilter 用 pct_change 排出题材热度 TOP N。
        """
        return self._call(
            "ths_daily",
            ts_code=ts_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            limiter=_strict_limiter,
            fields=(
                "ts_code,trade_date,close,open,high,low,pre_close,avg_price,"
                "change,pct_change,vol,turnover_rate,total_mv,float_mv"
            ),
        )

    def limit_cpt_list(
        self,
        trade_date: str | None = None,
        ts_code: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """涨停最强板块统计（doc_id=357）。

        每天涨停股票最多最强的概念板块，rank 字段为热点排名（1 最强）。
        ThemeFilter 用作"短线涨停情绪"信号。
        """
        return self._call(
            "limit_cpt_list",
            trade_date=trade_date,
            ts_code=ts_code,
            start_date=start_date,
            end_date=end_date,
            limiter=_strict_limiter,
            fields="ts_code,name,trade_date,days,up_stat,cons_nums,up_nums,pct_chg,rank",
        )

    def moneyflow_cnt_ths(
        self,
        ts_code: str | None = None,
        trade_date: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """同花顺概念板块每日资金流向（doc_id=371）。

        v2.1 修法：fields 同时拉 net_buy_amount / net_sell_amount / net_amount
        三个净额字段，与 ThsConceptMoneyflow 表完全对齐。
        """
        return self._call(
            "moneyflow_cnt_ths",
            ts_code=ts_code,
            trade_date=trade_date,
            start_date=start_date,
            end_date=end_date,
            limiter=_strict_limiter,
            fields=(
                "trade_date,ts_code,name,lead_stock,pct_change,company_num,"
                "pct_change_stock,net_buy_amount,net_sell_amount,net_amount"
            ),
        )

    # ---------- 龙虎榜席位 / 游资（v2.1 新增）----------

    def hm_list(self, name: str | None = None) -> pd.DataFrame:
        """游资名录（doc_id=311）。

        返回经验型分类（赵老哥 / 章盟主 等），orgs 字段是其关联营业部
        分号/逗号分隔字符串。LhbFilter 用作"是否知名游资席位"的识别基础。
        """
        return self._call(
            "hm_list",
            name=name,
            limiter=_default_limiter,
            fields="name,desc,orgs",
        )

    def hm_detail(
        self,
        trade_date: str | None = None,
        ts_code: str | None = None,
        hm_name: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> pd.DataFrame:
        """游资每日交易明细（doc_id=312）。数据从 2022-08 起。

        与 top_inst 互补：top_inst 看席位金额；hm_detail 直接给"哪位游资买了什么"。
        """
        return self._call(
            "hm_detail",
            trade_date=trade_date,
            ts_code=ts_code,
            hm_name=hm_name,
            start_date=start_date,
            end_date=end_date,
            limiter=_strict_limiter,
            fields="trade_date,ts_code,ts_name,buy_amount,sell_amount,net_amount,hm_name,hm_orgs,tag",
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

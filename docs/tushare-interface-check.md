# Tushare 接口实测记录

> 所有新增 / 关键 Tushare 接口在接入前后的实测结果（积分、权限、返回字段、行数样例）。
> 用于：①确认账号配额够用 ②冻结字段名供后续 fields 配置 ③做接口重大变更回归对照。

**测试账号信息**：项目 `.env.TUSHARE_TOKEN` 对应账号
**积分等级**：10000（详见 [CLAUDE.md](../CLAUDE.md) §一、关键事实）

---

## v2.1 plan Task 1 实测（2026-04-26）

测试日期参数：`trade_date=20260424`

| 接口 | 行数 | 状态 | 备注 |
|------|------|------|------|
| `ths_daily` | 1232 | ✅ OK | 同花顺概念/行业指数日行情，覆盖 1200+ 板块 |
| `limit_cpt_list` | 20 | ✅ OK | 涨停最强概念榜单，固定输出 TOP 20（rank 1-20） |
| `moneyflow_cnt_ths` | 386 | ✅ OK | 概念板块资金流向，386 个有数据概念 |
| `hm_list` | 109 | ✅ OK | 游资名录，109 个分类（赵老哥 / 章盟主 等） |
| `hm_detail` | 262 | ✅ OK | 当日游资交易明细，262 条 |

### 字段验证（与 v2.1 plan §2 表定义对齐）

#### `ths_daily`
```
ts_code, trade_date, close, open, high, low, pre_close, avg_price,
change, pct_change, vol, turnover_rate, total_mv, float_mv
```
✓ 与 `ThsDaily` 表字段完全对齐

#### `limit_cpt_list`
```
ts_code, name, trade_date, days, up_stat, cons_nums, up_nums, pct_chg, rank
```
✓ 与 `LimitConceptDaily` 表字段完全对齐

#### `moneyflow_cnt_ths`
```
trade_date, ts_code, name, lead_stock, pct_change, company_num,
pct_change_stock, net_buy_amount, net_sell_amount, net_amount
```
✓ 与 `ThsConceptMoneyflow` 表字段完全对齐（v2 → v2.1 修法已验证：3 个净额字段都在）

#### `hm_list`
```
name, desc, orgs
```
✓ 与 `HotMoneyList` 表字段对齐（v2.1 已删除多余的 aliases 字段）

#### `hm_detail`
```
trade_date, ts_code, ts_name, buy_amount, sell_amount, net_amount,
hm_name, hm_orgs, tag
```
✓ 与 `HotMoneyDetail` 表字段完全对齐

### 积分结论

- **5 个新接口全部可调，10000 积分足够**
- 无 403 / 权限拒绝
- 调用速度正常（hm_detail 单次 ~30s 略慢，符合"游资明细"批量查询特性）

### 频次注意

按已知 Tushare 限速：
- `ths_daily` / `limit_cpt_list` / `moneyflow_cnt_ths` / `hm_detail`：建议归入 `_strict_limiter`（60 次/分钟）
- `hm_list`：低频元数据（refresh-basics 调用），归入 `_default_limiter`（120 次/分钟）

---

## 历史记录

> 当 Tushare 接口字段口径变化或新增其它接口时，在此追加。

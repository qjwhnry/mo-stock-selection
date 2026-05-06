# 每日同步与数据刷新前端接入计划

## 背景

当前项目已经有 Web 前端和任务执行页，但页面主要面向“选股执行”和“调度器管理”。后端实际存在多类数据入口：

| 类型 | 现有入口 | 作用 | 推荐频率 |
|------|----------|------|----------|
| 单日同步 | `DailyIngestor.ingest_one_day()` / `mo-stock run-once` | 拉取指定交易日的日频行情、资金流、龙虎榜、题材等数据 | 每个交易日 15:30 后 |
| 完整选股 | `run_daily_pipeline()` / `mo-stock run-once` | 单日同步 + 规则打分 + AI + 报告 | 每个交易日 15:30 后 |
| 历史回填 | `mo-stock backfill --days 180` | 首次部署或缺口修复，回填最近 N 天日频数据 | 一次性 / 按需 |
| 指数补齐 | `mo-stock refresh-index --days 180` | 只补沪深 300 / 上证指数日线，不重跑全市场个股接口 | 按需 |
| 基础元数据 | `mo-stock refresh-basics` | 刷新股票基础信息、申万行业映射 | 每周 |
| 题材/游资元数据 | `mo-stock refresh-basics --with-ths --with-hm-list` | 刷新同花顺概念、概念成分、游资名录 | 每月 / 每周 |
| 交易日历 | `mo-stock refresh-cal --start ...` | 刷新 A 股交易日历 | 每年末 / 首次部署 |

所以前端不应该只接一个“同步”按钮，而应该设计成“数据维护控制台”，区分日常同步、历史回填和低频元数据刷新。

## 目标

1. 在前端提供可操作的数据同步入口，覆盖日常使用和初始化使用。
2. 后端任务状态统一，前端可以看到运行中、成功、失败，并在任务完成后看到每步写入行数。
3. 避免把耗时任务放在 HTTP 请求内同步等待，全部通过后台线程执行。
4. 保留现有 `/execute` 页面能力，逐步扩展，不大改前端架构。

## 非目标

1. 不在第一版实现任务持久化历史表。
2. 不在第一版实现任务取消。
3. 不把 Tushare 调用做成每一步可单独点击的复杂编排。
4. 不改变现有 CLI 命令行为。

## 页面设计

建议把当前 `/execute` 页面改为三个 Tab：

```text
数据同步 | 选股执行 | 定时调度
```

### Tab 1：数据同步

面向数据维护，提供四类操作。

#### 1. 单日同步

用于日常补跑某个交易日的数据。

表单字段：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| 交易日期 | 今日 | `YYYY-MM-DD` |
| 同步增强数据 | 开启 | 关闭时等价于 `skip_enhanced=true`，只跑 CORE 数据 |

按钮：

```text
开始单日同步
```

后端执行：

```python
DailyIngestor().ingest_one_day(trade_date, skip_enhanced=skip_enhanced)
```

展示结果：

| 步骤 | 含义 |
|------|------|
| `daily_kline` | 个股日线 |
| `index_daily` | 指数日线 |
| `daily_basic` | 日基础指标 |
| `limit_list` | 涨停数据 |
| `moneyflow` | 主力资金流 |
| `lhb` | 龙虎榜 |
| `sw_daily` | 申万板块日线 |
| `ths_daily` | 同花顺概念/行业行情 |
| `limit_concept` | 涨停最强概念 |
| `concept_moneyflow` | 概念资金流 |
| `top_inst` | 龙虎榜席位明细 |
| `hm_detail` | 游资交易明细 |

#### 2. 历史回填

用于首次部署或补历史数据，覆盖现有：

```bash
mo-stock backfill --days 180
```

这里的 `180` 不是固定值，而是可配置参数。前端应允许用户传入任意合法天数，例如 10 天、30 天、180 天。

表单字段：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| 回填天数 | 180 | 最近 N 天，前端可传 `10` / `30` / `180` 等 |
| 截止日期 | 今日 | 不填时后端取当天 |

按钮：

```text
开始历史回填
```

后端执行：

```python
end_d = req.end_date or date.today()
start_d = end_d - timedelta(days=req.days)
ingestor = DailyIngestor()
ingestor.refresh_stock_basic()
ingestor.refresh_trade_cal(start_d, end_d + timedelta(days=30))
stats = ingestor.backfill(start_d, end_d)
```

注意：

- 这是耗时任务，180 天可能 30-60 分钟。
- 第一版只允许后台单任务运行，避免和日常同步、选股任务同时抢 Tushare 限流。
- 前端需要明确显示“耗时较长”。
- Phase 1 不展示运行中的增量 `stats`。`backfill()` 只有整体返回后才能拿到汇总结果，运行中只展示 `message`。

#### 3. 指数补齐

对应现有：

```bash
mo-stock refresh-index --days 180
```

表单字段：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| 补齐天数 | 180 | 最近 N 天 |
| 截止日期 | 今日 | 不填时后端取当天 |

按钮：

```text
补齐指数日线
```

适用场景：

- `swing` 策略的 `market_regime` 缺指数数据。
- 历史回填已做过，但指数日线缺失。

实现要求：

- 当前 `refresh-index` 的逐日循环逻辑在 `cli.py` 中，尚未沉到 `DailyIngestor`。
- 接 API 前先新增 `DailyIngestor.refresh_index_range(start, end) -> int`。
- CLI 的 `refresh-index` 和 Web API 都调用这个新方法，避免后端接口复制 CLI 循环逻辑。

#### 4. 元数据刷新

对应现有：

```bash
mo-stock refresh-basics
mo-stock refresh-basics --with-ths
mo-stock refresh-basics --with-hm-list
mo-stock refresh-cal --start ...
```

表单字段：

| 字段 | 默认值 | 说明 |
|------|--------|------|
| 刷新股票基础/申万行业 | 开启 | `stock_basic` + `index_member` |
| 刷新同花顺概念 | 关闭 | `ths_index` + `ths_member`，耗时 3-4 分钟 |
| 刷新游资名录 | 开启 | `hot_money_list` |
| 刷新交易日历 | 关闭 | 可选，首次部署或年末使用 |
| 日历开始日期 | 当年 01-01 | 刷新交易日历时必填 |
| 日历结束日期 | 今日 + 365 天 | 刷新交易日历时使用 |

按钮：

```text
刷新元数据
```

后端执行时必须包含 `refresh_index_member()`。`refresh-basics` 当前实际语义是：

```python
ingestor = DailyIngestor()
stats = {
    "stock_basic": ingestor.refresh_stock_basic(),
    "index_member": ingestor.refresh_index_member(),
}
if req.with_ths:
    ths_index, ths_member = ingestor.refresh_ths_concept()
    stats["ths_index"] = ths_index
    stats["ths_member"] = ths_member
if req.with_hm_list:
    stats["hot_money_list"] = ingestor.refresh_hot_money_list()
if req.refresh_calendar:
    stats["trade_cal"] = ingestor.refresh_trade_cal(calendar_start, calendar_end)
```

`stats` 统一返回 `dict[str, int]`，不要直接透出 `refresh_ths_concept()` 的 tuple。

## 后端 API 设计

继续使用 `src/mo_stock/web/routers/tasks.py`，新增几个任务入口。

### 1. 单日同步

```text
POST /api/tasks/sync-one-day
```

请求：

```json
{
  "trade_date": "2026-05-05",
  "skip_enhanced": false
}
```

### 2. 历史回填

```text
POST /api/tasks/backfill
```

请求：

```json
{
  "days": 10,
  "end_date": "2026-05-05"
}
```

### 3. 指数补齐

```text
POST /api/tasks/refresh-index
```

请求：

```json
{
  "days": 180,
  "end_date": "2026-05-05"
}
```

### 4. 元数据刷新

```text
POST /api/tasks/refresh-metadata
```

请求：

```json
{
  "refresh_basics": true,
  "with_ths": false,
  "with_hm_list": true,
  "refresh_calendar": false,
  "calendar_start": "2026-01-01",
  "calendar_end": "2027-05-05"
}
```

### 5. 统一任务状态

复用并扩展：

```text
GET /api/tasks/status
```

响应：

```json
{
  "task_id": "6f3a9c2b",
  "task_type": "backfill",
  "status": "running",
  "strategy": null,
  "trade_date": null,
  "started_at": "2026-05-05T16:00:00+08:00",
  "finished_at": null,
  "error": null,
  "message": "正在回填最近 180 天数据",
  "stats": {}
}
```

说明：

- Phase 1 中，`running` 状态不承诺返回增量 `stats`，因为 `backfill()` 只有整体执行完后才返回汇总。
- `success` / `error` 状态可以返回最终或部分 `stats`。单步失败沿用现有约定，使用 `-1` 表示该步骤失败。

完成后响应示例：

```json
{
  "task_id": "6f3a9c2b",
  "task_type": "backfill",
  "status": "success",
  "strategy": null,
  "trade_date": null,
  "started_at": "2026-05-05T16:00:00+08:00",
  "finished_at": "2026-05-05T16:42:00+08:00",
  "error": null,
  "message": "回填最近 180 天数据完成",
  "stats": {
    "daily_kline": 123456,
    "index_daily": 240,
    "daily_basic": 123000
  }
}
```

状态枚举：

| 状态 | 含义 |
|------|------|
| `idle` | 空闲 |
| `running` | 运行中 |
| `success` | 最近一次任务成功 |
| `error` | 最近一次任务失败 |

任务类型枚举：

| 类型 | 含义 |
|------|------|
| `sync_one_day` | 单日同步 |
| `backfill` | 历史回填 |
| `refresh_index` | 指数补齐 |
| `refresh_metadata` | 元数据刷新 |
| `run_pipeline` | 完整选股流程 |

## 后端实现要点

### 统一后台任务包装

当前 `_run_in_background()` 只服务 `run_daily_pipeline()`。建议改成通用包装：

```python
def _start_background_task(
    task_type: str,
    message: str,
    target: Callable[[], dict[str, int] | None],
) -> TaskStatusResponse:
    ...
```

要求：

1. 启动前检查 `_task_state["status"] == "running"`，运行中返回 `409`。
2. 启动时写入 `task_id`、`task_type`、`started_at`、`message`。
3. 运行中不展示增量 `stats`，除非目标函数主动更新共享状态；Phase 1 不做主动更新。
4. 成功后写入 `status="success"`、`finished_at`、`stats`。
5. 失败后写入 `status="error"`、`finished_at`、`error`，如目标函数已经产出部分结果可保留 `stats`。

读写 `_task_state` 都必须持锁。状态接口不能直接读取共享 dict，应复制快照后返回：

```python
@router.get("/tasks/status", response_model=TaskStatusResponse)
async def get_task_status() -> TaskStatusResponse:
    with _task_lock:
        state = dict(_task_state)
    return TaskStatusResponse(**state)
```

### 单进程假设

Phase 1 的 `_task_state` 和 `_task_lock` 都是进程内状态，只适合单 FastAPI 进程 / 单 uvicorn worker。

MVP 明确假设：

```text
WEB_CONCURRENCY=1
```

如果生产部署多个 worker，会出现：

- 每个 worker 各有一个 `_task_state`，前端轮询可能打到不同进程。
- 全局单任务锁只在单进程内有效，无法阻止跨进程并发任务。
- API 启动的 APScheduler 线程也可能在多个 worker 内重复启动。

多 worker 或多实例部署应放到 Phase 3，通过 DB 任务表、Redis 锁或外部任务队列解决。

### 现有 /tasks/run 兼容与参数清理

扩展任务 API 时要同步清理现有 `/tasks/run` 的参数语义：

- `RunTaskRequest.force` 当前已定义，但 `_run_in_background()` 没有使用，`run_daily_pipeline()` 也没有 `force` 参数。Phase 1 不在前端暴露 `force`，实现时要么移除 schema 字段，要么明确标记为暂不生效，避免误导。
- CLI `run-once` 支持 `--skip-enhanced`，Web 端当前没有。若“选股执行”Tab 需要只跑 CORE ingest，应给 `/tasks/run` 增加 `skip_enhanced` 并透传到 `run_daily_pipeline()`。
- `/tasks/run` 是否校验交易日，应与 CLI `run-once` 对齐；否则同一个功能在 CLI 和 Web 上行为不同。

兼容性测试必须覆盖：

1. 旧请求体只传 `strategy` 时仍可运行。
2. 新增字段缺省时有后端默认值。
3. `force` 不暴露或不生效时不能造成前端误判。

### 不建议直接 shell 调 CLI

后端 API 应直接调用 Python 函数，不要通过 `subprocess` 调：

```bash
mo-stock backfill --days 180
```

原因：

- 复用类型和异常处理更清晰。
- 测试时可以 mock `DailyIngestor`。
- 避免环境变量、虚拟环境、路径问题。

### 提取 refresh_index_range

`refresh-index` 的可复用逻辑应从 CLI 下沉到 `DailyIngestor`：

```python
def refresh_index_range(self, start: date, end: date) -> int:
    """补齐 [start, end] 区间内交易日的指数日线。"""
    from mo_stock.storage import repo

    self.refresh_trade_cal(start, end + timedelta(days=30))
    total = 0
    current = start
    while current <= end:
        with get_session() as session:
            is_open = repo.is_trade_date(session, current)
        if is_open:
            total += self.ingest_index_daily(current)
        current += timedelta(days=1)
    return total
```

调整后：

- CLI `refresh-index` 调用 `DailyIngestor().refresh_index_range(start_d, end_d)`。
- Web API `POST /api/tasks/refresh-index` 也调用同一个方法。
- 测试只需要 mock `refresh_index_range()`。

### 统一 stats 格式

所有任务状态里的 `stats` 均使用 `dict[str, int]`：

```json
{
  "stock_basic": 5500,
  "index_member": 5700,
  "ths_index": 408,
  "ths_member": 70000,
  "hot_money_list": 109,
  "trade_cal": 365
}
```

转换规则：

- 返回 `int` 的方法直接写到对应 key。
- 返回 `tuple[int, int]` 的方法拆成多个 key。
- 单步异常用 `-1` 标记，并在日志中记录完整异常。

### 交易日校验

第一版建议：

- `sync-one-day`：允许用户指定任意日期，但后端可以通过 `repo.is_trade_date()` 提示非交易日。
- `backfill`：按现有逻辑跳过非交易日。
- `refresh-index`：按现有逻辑跳过非交易日。
- `refresh-metadata`：不需要交易日校验。

后续可以加 `force` 字段，但第一版不要暴露，避免语义不清。

### 默认日期与时区

所有“默认今日”的后端逻辑都应使用 A 股交易时区：

```python
today = datetime.now(CN_TZ).date()
```

避免直接使用 `date.today()`，尤其是：

- `trade_date` 默认值
- `end_date` 默认值
- `_task_state["trade_date"]`
- `started_at` / `finished_at`

前端日期默认值也继续使用 `Asia/Shanghai`，保持与后端一致。

### 参数范围控制

后端必须对耗时任务参数做硬校验，不能只依赖前端控件。

建议范围：

| 参数 | 接口 | 范围 | 说明 |
|------|------|------|------|
| `days` | `backfill` | `1 <= days <= 730` | 最多允许回填 2 年，避免误填过大 |
| `days` | `refresh-index` | `1 <= days <= 1825` | 指数数据量较小，可允许最多 5 年 |
| `calendar_start` / `calendar_end` | `refresh-metadata` | 跨度不超过 5 年 | 防止一次刷新过大日期范围 |

前端默认值：

| 场景 | 默认值 |
|------|--------|
| 历史回填 | `180` 天 |
| 指数补齐 | `180` 天 |

前端建议交互：

1. `days > 180` 时弹二次确认，提示任务可能运行较久。
2. `days > 730` 时不允许提交，直接提示“后端最多支持 730 天”。
3. 后端仍必须返回 `400`，防止绕过前端直接调用 API。

`days` 语义必须明确为“包含截止日在内的最近 N 个自然日窗口”。实现时建议：

```python
start_d = end_d - timedelta(days=req.days - 1)
```

这样 `days=10, end_date=2026-05-05` 表示 `2026-04-26` 到 `2026-05-05`。若为了兼容现有 CLI 继续使用 `end_d - timedelta(days=days)`，文档和前端文案必须明确它会覆盖 `days + 1` 个自然日。

Pydantic 校验建议：

```python
class BackfillTaskRequest(BaseModel):
    days: int = Field(default=180, ge=1, le=730)
    end_date: str | None = None


class RefreshIndexTaskRequest(BaseModel):
    days: int = Field(default=180, ge=1, le=1825)
    end_date: str | None = None


class SyncOneDayTaskRequest(BaseModel):
    trade_date: str | None = None
    skip_enhanced: bool = False


class RefreshMetadataTaskRequest(BaseModel):
    refresh_basics: bool = True
    with_ths: bool = False
    with_hm_list: bool = True
    refresh_calendar: bool = False
    calendar_start: str | None = None
    calendar_end: str | None = None
```

`RefreshMetadataTaskRequest` 额外校验：

1. 至少启用一个刷新项，否则返回 `400`。
2. `refresh_calendar=true` 时，`calendar_start` 必填。
3. `calendar_end >= calendar_start`。
4. `calendar_start` 到 `calendar_end` 跨度不超过 5 年。

## 前端实现要点

### API 类型

在 `frontend/src/api/index.ts` 增加：

```ts
export interface TaskStatusResponse {
  task_id: string | null
  task_type?: string | null
  status: 'idle' | 'running' | 'success' | 'error'
  strategy?: string | null
  trade_date?: string | null
  started_at?: string | null
  finished_at?: string | null
  error?: string | null
  message?: string | null
  stats?: Record<string, number>
}
```

迁移策略：

- 新增字段在前端全部按 optional 处理，保证现有 `/tasks/run` 和旧状态响应不被破坏。
- 后端响应尽量补齐默认值：`task_type=null`、`message=null`、`stats={}`、`finished_at=null`。
- “选股执行”Tab 和“数据同步”Tab 共用 `GET /tasks/status`，根据 `task_type` 显示不同文案。

新增方法：

```ts
runSyncOneDay(payload)
runBackfill(payload)
runRefreshIndex(payload)
runRefreshMetadata(payload)
```

### 页面组件

建议先在现有 `Execute.vue` 内完成，不新建页面：

1. 顶部增加 `van-tabs`。
2. 当前“手动选股”移动到“选股执行”Tab。
3. 新增“数据同步”Tab。
4. 定时调度保留为第三个 Tab。

### 状态展示

统一使用轮询：

```ts
setInterval(pollStatus, 3000)
```

运行中时：

- 禁用所有任务触发按钮。
- 显示 `message`。
- Phase 1 不展示运行中增量 `stats`。

成功后：

- 显示最近任务类型、开始时间、结束时间。
- 展示 `stats` 表格。
- `stats` 值为 `-1` 的步骤用红色文字标注为“失败”。

失败后：

- 展示错误信息。
- 如果后端返回部分 `stats`，照常展示；`-1` 步骤用红色标注。

## 任务优先级

### Phase 1：MVP

目标：前端可以触发单日同步和 180 天回填。

后端：

1. 扩展 `_task_state` 和 `TaskStatusResponse`，并保持现有 `/tasks/run` 兼容。
2. 新增 `POST /api/tasks/sync-one-day`。
3. 新增 `POST /api/tasks/backfill`。
4. 保留现有 `POST /api/tasks/run`。
5. 补充 `tests/test_web_tasks.py`，优先覆盖 `/tasks/run` 兼容性和新状态字段默认值。

前端：

1. `api/index.ts` 增加同步和回填接口。
2. `Execute.vue` 增加“数据同步”Tab。
3. 展示任务状态和 stats。

验收：

```bash
.venv/bin/python -m pytest tests/test_web_tasks.py
cd frontend && npm run build
```

### Phase 2：补齐刷新能力

目标：把 CLI 里低频刷新入口也接入前端。

后端：

1. 新增 `DailyIngestor.refresh_index_range(start, end) -> int`，并让 CLI / API 共同调用。
2. 新增 `POST /api/tasks/refresh-index`。
3. 新增 `POST /api/tasks/refresh-metadata`。
4. 增加参数校验：`days` 范围、日期格式、日历起止日期。

前端：

1. 增加“指数补齐”表单。
2. 增加“元数据刷新”表单。
3. 对耗时操作加二次确认。

### Phase 3：任务历史持久化

目标：任务执行记录可追溯。

新增表建议：

```text
task_run
```

核心字段：

| 字段 | 说明 |
|------|------|
| `id` | 主键 |
| `task_id` | 对外展示 ID |
| `task_type` | 任务类型 |
| `status` | 状态 |
| `params` | JSON 参数 |
| `stats` | JSON 结果 |
| `error` | 错误信息 |
| `started_at` | 开始时间 |
| `finished_at` | 结束时间 |

新增接口：

```text
GET /api/tasks/history
GET /api/tasks/history/{task_id}
```

第一版可以不做，先用内存状态满足当前前端联调。

## 风险与处理

| 风险 | 影响 | 处理 |
|------|------|------|
| 180 天回填耗时长 | 前端误以为卡死 | 后台任务 + 状态轮询 + 耗时提示 |
| 多任务并发抢 Tushare 限流 | 数据失败率上升 | 第一版全局单任务锁 |
| `force` 字段已有但后端未使用 | 前端语义混乱 | 第一版不暴露 `force` |
| 浏览器刷新后任务状态丢失 | 用户看不到历史 | Phase 3 做任务历史表 |
| 单步失败但整体继续 | 用户难判断数据完整性 | `stats=-1` 表示失败步骤，前端高亮 |

## 建议交付顺序

1. 后端统一任务状态。
2. 接 `sync-one-day`。
3. 接 `backfill --days 180` 等价能力。
4. 前端 `Execute.vue` 加“数据同步”Tab。
5. 补测试和构建。
6. 再接 `refresh-index` 和 `refresh-metadata`。

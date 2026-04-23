# GTHT 客户端设计

**日期**：2026-04-23
**状态**：已通过 brainstorm 阶段，待写实施计划
**相关 PLAN.md 章节**：§2 目录结构、§3.1 数据拉取、§6 关键文件与跨语言桥、§7 mo-skills 复用清单

---

## 1. Context

[PLAN.md](../../../PLAN.md) 已设计了 GTHT 灵犀作为辅助数据源（研报、热榜、金融查询），但 [src/mo_stock/data_sources/](../../../src/mo_stock/data_sources/) 目录下尚未实现 `gtht_client.py`。当前只有 `tushare_client.py`、`calendar.py`。

[config/settings.py](../../../config/settings.py) 仅有 `gtht_entry_json_path` 和 `mo_skills_root` 两个相关字段，**缺少 GTHT API Key 的配置入口** —— 用户必须手动跑 `node skill-entry.js authChecker auth` 完成扫码授权才能得到 `gtht-entry.json`，对自动化场景不友好。

本设计补齐 GTHT 客户端，并新增一个 OpenAI 兼容的辅助 LLM，作为 ingest/filter 层的开放式查询入口。

---

## 2. Decisions（已与用户对齐）

| # | 决策点 | 选择 |
|---|---|---|
| 1 | 调用方式 | Python `subprocess` 调 `node skill-entry.js`（不直连 HTTP MCP 网关） |
| 2 | API Key 配置 | `.env` 配 `GTHT_API_KEY`，Python 自动落盘到 `gtht-skill-shared/gtht-entry.json` |
| 3 | 封装粒度 | 底层通用 `call(skill, gateway, tool, **kwargs)`，不为每个 skill 写专属业务方法 |
| 4 | 辅助 LLM 供应商 | **不绑定**，`.env` 给 `GTHT_LLM_API_KEY` / `GTHT_LLM_BASE_URL` / `GTHT_LLM_MODEL` 三个占位变量，用户自填（DeepSeek / Qwen / Kimi 任选） |
| 5 | 触发入口 | 按 PLAN.md 既定设计，由 `ingest/*` 与 `filters/*` 层按需调用，**不**新增 CLI 子命令 |
| 6 | LLM 使用粒度 | **混合模式** —— 固定任务（研报抓取）走 `GthtClient.call()` 直连；开放式查询走 `GthtAgent.ask()` |

---

## 3. Architecture

```
┌─────────────────────────────────────────────────────┐
│  调用方                                              │
│  ingest_research.py / sentiment_filter / ranklist   │
└──────────────┬─────────────────────┬────────────────┘
               │                     │
        固定任务（直连）          开放式查询
               │                     │
               │                     ▼
               │            ┌──────────────────┐
               │            │  GthtAgent.ask() │  OpenAI SDK
               │            │  + tools schema  │  + function calling
               │            └────────┬─────────┘
               │                     │ 由 LLM 决定 tool + 参数
               ▼                     ▼
        ┌──────────────────────────────────┐
        │  GthtClient.call(skill, gw, tool)│
        │  - ensure_auth()  懒加载，按需落盘  │
        │  - subprocess 调 node skill-entry │
        │  - stdout JSON → dict             │
        │  - 异常 → GthtError               │
        └──────────────┬───────────────────┘
                       │
                       ▼
        ┌──────────────────────────────────┐
        │  node <mo_skills_root>/gtht-skills│
        │     /<skill>/skill-entry.js       │
        │  → gtht-skill-shared/gtht-entry.json│
        │  → MCP 网关（HTTPS）              │
        └──────────────────────────────────┘
```

**数据流**：调用方按场景选直连或走 agent → `GthtClient.call()` 拼命令 → subprocess 启 node → node 读 `gtht-entry.json` 拿 API Key → 请求 MCP 网关 → 响应回 stdout → Python 解析为 dict。

---

## 4. Components

### 4.1 `src/mo_stock/data_sources/gtht_client.py`

**职责**：subprocess 底层封装 + auth bootstrap。

**对外接口**：

```python
class GthtError(Exception):
    """GTHT 调用失败。"""

class GthtClient:
    def __init__(self) -> None: ...

    def ensure_auth(self) -> None:
        """懒加载授权。第一次 call() 时触发，按下表决策：

        | .env GTHT_API_KEY | gtht-entry.json | 行为                            |
        |-------------------|-----------------|---------------------------------|
        | 有值              | 不存在          | 用 .env 值落盘                  |
        | 有值              | 存在，key 不同  | 用 .env 值覆写（.env 为权威源） |
        | 有值              | 存在，key 相同  | 不动                            |
        | 空                | 存在            | 使用文件中已有的 key（兜底）    |
        | 空                | 不存在          | 抛 GthtError 提示用户配 .env 或手动跑 authChecker |
        """

    def call(
        self,
        skill: str,        # e.g. "lingxi-researchreport-skill"
        gateway: str,      # e.g. "researchreport"
        tool: str,         # e.g. "research"
        **kwargs: Any,     # tool 参数，会拼成 k=v 形式
    ) -> dict:
        """执行 node skill-entry.js mcpClient call <gateway> <tool> k=v ...
        - 启动前调 ensure_auth()
        - subprocess.run(timeout=60)，捕获 stdout/stderr
        - 退出码 != 0 或 stdout 含 error 字段 → 抛 GthtError
        - 否则 json.loads(stdout) 返回 dict
        """

    def check_auth(self) -> bool:
        """透传到 node skill-entry.js authChecker check"""

    def clear_auth(self) -> None:
        """透传到 node skill-entry.js authChecker clear"""
```

**实现要点**：
- 单例模式（参考 [tushare_client.py:82-88](../../../src/mo_stock/data_sources/tushare_client.py)）
- 模块 import 时**不**做 I/O，避免污染测试
- subprocess 的工作目录设为对应 skill 目录（node 需要相对路径解析 `gateway-config.json`）
- Windows 兼容：subprocess 不显式调 shell，直接传 args list

### 4.2 `src/mo_stock/data_sources/gtht_agent.py`

**职责**：OpenAI SDK + function calling，把 GTHT skill 暴露为 tools 给辅助 LLM。

**对外接口**：

```python
class GthtAgent:
    def __init__(self, client: GthtClient | None = None) -> None:
        """client 默认 GthtClient()；测试时注入 mock。"""

    def ask(self, query: str, max_iters: int = 5) -> dict:
        """
        - 用 settings.gtht_llm_* 配置初始化 OpenAI 客户端
        - 注册 _TOOLS（见下方）
        - 进入 function calling 循环：
          1. LLM 返回 tool_calls → 解析 → self._client.call(...)
          2. 把结果作为 tool message 喂回 LLM
          3. 直到 LLM 返回 finish_reason == "stop"
        - 返回 {"answer": str, "tool_trace": list[dict]}
        - 超过 max_iters 抛 GthtError
        """
```

**`_TOOLS` 内联 schema**（覆盖 PLAN.md 用到的 3 个）：

| skill | gateway | tool | 用途 |
|---|---|---|---|
| `lingxi-researchreport-skill` | `researchreport` | `research` | 研报全文检索 |
| `lingxi-ranklist-skill` | `ranklist` | `ranklist` | 资金净流入热榜（交叉验证） |
| `lingxi-smartstockselection-skill` | `financial` | `financial-search` | 自然语言金融数据查询 |

每个 tool 在 `_TOOLS` 中按 OpenAI function calling 格式定义 `name / description / parameters` JSON Schema。其余 3 个 skill（watchlist / realtimemarketdata / financialsearch 顶层）暂不纳入（YAGNI），后续按需扩展。

### 4.3 配置层 [config/settings.py](../../../config/settings.py) 新增字段

```python
# ---------- GTHT ----------
gtht_api_key: str = Field(
    default="",
    description="国泰海通灵犀 API Key；首次使用时自动落盘到 gtht_entry_json_path",
)
# 已有：gtht_entry_json_path（保留，path 不变）
# 已有：mo_skills_root（保留，gtht_client 需要定位 skill 目录）

# ---------- GTHT 辅助 LLM（OpenAI 兼容）----------
gtht_llm_api_key: str = Field(default="", description="辅助 LLM 的 API Key")
gtht_llm_base_url: str = Field(
    default="",
    description="OpenAI 兼容的 base_url，如 https://api.deepseek.com/v1",
)
gtht_llm_model: str = Field(
    default="",
    description="辅助 LLM 的模型名，如 deepseek-chat / qwen-plus / moonshot-v1-8k",
)
```

### 4.4 配置层 [.env.example](../../../.env.example) 新增

```bash
# ---------- GTHT 灵犀（必填）----------
# 来源：灵犀/君弘 APP → API KEY 管理 → 复制生效中的 key
GTHT_API_KEY=your_gtht_api_key_here

# ---------- GTHT 辅助 LLM（OpenAI 兼容，可选）----------
# 用于 ingest/filter 层的开放式 skill 调用（自然语言 → tool 选择）
# 推荐选项（任选其一）：
#   DeepSeek:  base=https://api.deepseek.com/v1            model=deepseek-chat
#   通义 Qwen:  base=https://dashscope.aliyuncs.com/compatible-mode/v1   model=qwen-plus
#   Kimi:      base=https://api.moonshot.cn/v1             model=moonshot-v1-8k
GTHT_LLM_API_KEY=
GTHT_LLM_BASE_URL=
GTHT_LLM_MODEL=
```

### 4.5 依赖 [pyproject.toml](../../../pyproject.toml)

新增 `openai>=1.50`（仅 GthtAgent 使用，不影响主 anthropic 流程）。

---

## 5. ingest/filter 层使用范例

### 5.1 [ingest_research.py](../../../src/mo_stock/ingest/ingest_research.py) — 直连

```python
from mo_stock.data_sources.gtht_client import GthtClient

def fetch_research(ts_code: str, days: int = 90) -> list[dict]:
    client = GthtClient()
    result = client.call(
        skill="lingxi-researchreport-skill",
        gateway="researchreport",
        tool="research",
        query=f"{ts_code} 最近{days}天研报",
    )
    return result.get("data", [])
```

### 5.2 [sentiment_filter.py](../../../src/mo_stock/filters/sentiment_filter.py) — 走 LLM Agent（开放式）

```python
from mo_stock.data_sources.gtht_agent import GthtAgent

def get_sentiment_signal(ts_code: str, name: str) -> dict:
    agent = GthtAgent()
    return agent.ask(f"查 {name}({ts_code}) 最近研报评级和热榜表现，给出情绪倾向")
```

---

## 6. Testing Strategy

| 测试文件 | 覆盖 | 关键 mock |
|---|---|---|
| `tests/unit/test_gtht_client.py` | 命令拼接、auth 落盘、subprocess 错误处理、JSON 解析 | `subprocess.run`、`Path.exists`、`Path.write_text` |
| `tests/unit/test_gtht_agent.py` | function calling 循环、tool dispatch、max_iters 兜底 | OpenAI client（`openai.OpenAI`）、`GthtClient` |

**测试边界**：
- 不起真实 node 进程
- 不发真实 LLM 请求
- `gtht-entry.json` 文件操作走 `tmp_path` fixture

---

## 7. 错误处理

| 场景 | 行为 |
|---|---|
| `.env` 未配 `GTHT_API_KEY` 且 `gtht-entry.json` 不存在 | `ensure_auth()` 抛 `GthtError("GTHT_API_KEY 未配置；请检查 .env 或手动跑 node skill-entry.js authChecker auth")` |
| `.env` 未配 `GTHT_API_KEY` 但 `gtht-entry.json` 已存在 | 使用文件中已有的 key（兜底场景：用户已手动跑过授权流程） |
| `.env` 中 key 与文件中 key 不一致 | 静默用 `.env` 值覆写落盘（`.env` 是权威源） |
| node 不在 PATH | `subprocess` 抛 `FileNotFoundError` → 包成 `GthtError("node not found in PATH")` |
| MCP 网关返回 401 | node stdout 含 `error` 字段 → 抛 `GthtError`，提示"API Key 失效，请更新 .env 中 GTHT_API_KEY" |
| LLM 调用失败 | 不兜底，原样 raise（OpenAI SDK 的 APIError） |
| function calling 超过 max_iters | 抛 `GthtError("LLM tool loop exceeded max_iters")` |

---

## 8. Out of Scope（YAGNI）

- 不实现 `lingxi-watchlist-skill` / `lingxi-realtimemarketdata-skill` / `lingxi-financialsearch-skill`（顶层）的 tool schema —— 当前 PLAN.md 用不到，后续按需补
- 不做扫码授权流程的 Python 包装 —— 用户首次仍可手动跑 `node skill-entry.js authChecker auth` 拿 key，再填到 `.env`
- 不做 LLM 响应缓存 —— Phase 1 流量小，没必要
- 不做 GthtClient 的 HTTP 直连模式 —— 决策 1 已选 subprocess

---

## 9. 风险

| 风险 | 缓解 |
|---|---|
| node skill-entry.js stdout 中可能混入授权流程的提示日志，污染 JSON 解析 | `call()` 只在 `mcpClient call` 子命令上运行，已确认该路径 stdout 是纯 JSON（参见 `skill-entry.js` 行 `console.log(JSON.stringify(s, null, 2))`） |
| 辅助 LLM 选错 tool 或填错参数导致网关报错 | tool schema 的 `description` 字段写清楚业务语义；GthtClient 的错误返回会回传给 LLM 触发重试 |
| Windows 下 subprocess 的 `cwd` 与 node 的相对路径解析行为 | 测试覆盖；GthtClient 显式 `cwd=skill_dir`，避免依赖 PWD |
| `gtht-skill-shared/gtht-entry.json` 路径在 6 个 skill 之间的"漂移"（skill-entry.js 内有 3 级路径回退逻辑） | 由 [config/settings.py:50-53](../../../config/settings.py) 的 `gtht_entry_json_path` 强制锚定一份 |

---

## 10. 验收标准

- [ ] `pytest tests/unit/test_gtht_client.py tests/unit/test_gtht_agent.py -q` 全绿
- [ ] `mo-stock` CLI 现有命令不受影响
- [ ] 在配置好 `.env` 的真机上：第一次 `python -c "from mo_stock.data_sources.gtht_client import GthtClient; print(GthtClient().call('lingxi-researchreport-skill', 'researchreport', 'research', query='600519 研报'))"` 能拿到非空 dict
- [ ] `ruff check src tests && mypy src` 无新增 error

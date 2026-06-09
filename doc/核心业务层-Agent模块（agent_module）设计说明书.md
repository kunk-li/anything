# 核心业务层-Agent模块（agent_module）设计说明书

| 文档版本 | v2.1 |
| :--- | :--- |
| 最后更新 | 2026-05-29 |
| 维护责任人 | Agent模块开发负责人 |
| 状态 | 修订版 |

> 本修订版对齐《RAG与Agent系统架构设计说明书》v1.1、接口层-请求响应处理模块修订版、应用层-API服务模块修订版、协同调度模块修订版，重点修正 session_id / trace_id 透传规则、Hybrid 模式协作定义、工具调用边界、请求级配置覆盖、统一响应结构与状态存储链路。

> **v2.1 (2026-05-29) 架构变更**:
> - **KK (#71)**: `SimpleAgent` god class (1764 行) → 多继承 4 mixin (`core/components/`):
>   `ReActEngineMixin` / `ToolExecutorMixin` / `StreamingMixin` / `PromptBuilderMixin`.
>   `impl.py` 缩到 723 行 (-59%). `SimpleAgent` 公共 API 不变.
> - **MM (#73)**: 16 个 builtin tools 从单文件 `tools/builtin_tools.py` (1655 行) 拆到
>   `tools/tools_impl/<tool>.py`. 老 import 通过 re-export shim 保留.
> - **YY (#85)**: `ReActEngineMixin` 走 `self.deps.hook_registry` DI (PP #76 落地),
>   支持单测注入隔离 HookRegistry. 详见 `CHANGELOG.md`.

---

## v2.2 能力实现状态校准 (2026-06-03)

> 本节由"代码↔设计文档审计"补入。下方正文(第 1–13 章)定格在**早期形态**(规则式单轮 +
> dataclass 模型描述), 而代码已演进为 **LLM 驱动多轮 ReAct + 自我验证自纠正 + 长期记忆/画像 +
> 流式 + 工具治理 + 自主维护(方向4 建议性自主)**。**真相源优先级**: `core/base.py` + `core/impl.py` + `core/components/*` +
> `CHANGELOG.md` > 本文档正文。深度逐章回灌是独立工程, 在那之前以本校准节为准。

### A. 已实现但正文未覆盖的能力 (代码位置为准)

| 能力 | 代码位置 | 开关/默认 |
|---|---|---|
| **执行策略分发** single_shot / react / reflect | `core/impl.py` `execute()` + `core/components/react_engine.py` | `agent.execution_strategy` 默认 `single_shot` |
| **ReAct 多轮规划** observe→reflect→next + Plan Mode 早出 | `react_engine.py` `_react_execute()` | `=react`; `max_react_iterations` 默 15 |
| **自我验证闭环** 五验证器 + 自纠正递归 | `core/components/verifier.py` + `impl.py:_post_verify()` | `agent.enable_self_verify` 默认 **off**; `verify_mode` off/auto/ask |
| ├ ToolSuccess / Execution(pytest·sql·shell·lint) / Task 终态 / Compliance | `verifier.py` | `extra_params.verify=[...]` |
| └ **GoalVerifier 子目标级验收** | `verifier.py:GoalVerifier` | `extra_params.verify_goals=True` opt-in |
| **Reflection 反思环** critique→revise | `impl.py:_reflect_revise()` | `=reflect` 或 `extra_params.enable_reflection` |
| **长期记忆** 注入 + 抽取落盘 | `impl.py:_inject_long_term_memory()` / `_extract_and_store_memory()` | `agent.memory_enabled` |
| **用户画像 always-on 注入** 5 维度 | `impl.py:_inject_user_profile()` | 有 memory 即注入 |
| **query refinement** 含糊问题基于画像改写 | `impl.py:_refine_query()` | `agent.enable_query_refine` 默认 **off** |
| **工具审批白名单** | `impl.py` + `core/components/tool_executor.py:_needs_approval()` | `tool_approval_required` |
| **工具结果 LRU 缓存** | `tool_executor.py` (`_tool_cache_*`) | `cacheable_tools` 默 10 只读工具 |
| **真流式 run_stream** generator | `core/components/streaming.py:run_stream()` | event: thought/action/observation/chunk/meta/plan/done/error |
| **多轮历史注入 + 状态 events merge** | `impl.py:_history_prefix()` / `_save_state_safe()` | — |
| **自主维护·行为自反思** (方向4) 元级反思审计→改进提议 | `core/components/self_reflection.py` + `impl.py:self_reflect()` / `apply_reflection_proposals()` | `agent.enable_self_reflection` 默认 **off** |
| **自主维护·记忆健康** (方向4扩域) 确定性提议→复用算子 | `impl.py:propose_memory_maintenance()` / `apply_memory_maintenance()` | 同上; apply 仅人审批项映射 prune/degrade/reconcile/consolidate |
| **自主维护·代码文档** (方向4扩域, advisory) | `self_reflection.py:scan_code_doc_health` + `impl.py:propose_code_doc_maintenance()` | 同上; **只读无 apply, 绝不自动改代码/文档** |
| **自主维护·定时提议+通知** (方向4) | `impl.py:run_maintenance_scan()` + `execute` 的 `maintenance_scan` 钩子(可 TaskScheduler 触发) + `_notify_maintenance` 审计 | 同上 |
| **更高自主档·预授权自动** (方向4) | `impl.py:auto_approve_maintenance` + `run_maintenance_scan(auto_apply)` | `agent.auto_approve_maintenance` 名单**默认空**=零自动; 仅 `{run_prune,run_degrade}` 可自动 |
| **raw 结构化输出→自然语言合成** 工具直出 dict/JSON 时兜底转散文 | `impl.py:_looks_like_raw_json` + `_synthesize_natural_answer` (在 `aggregate_results` 末尾) | always-on; 仅检测命中且有 LLM 通道时触发, 合成失败/无通道保留 raw (fail-open)。详见 §6.8.1 |
| **authoritative 工具结果直达** 可枚举清单工具确定性渲染完整文本直接作答 | `impl.py:_authoritative_answer` (aggregate / 流式·同步 ReAct 三处收尾消费) + 工具侧 `data.authoritative=True`+`data.answer` (如 `software_info` list) | 工具标记即生效; 跳过 LLM 复述/合成, 防 max_tokens 截断/漏项。详见 §6.8.2 |

补充错误码(正文第 13 章错误码表未列): `TOOL_APPROVAL_REQUIRED`、`PLAN_PENDING`、`STREAM_INTERRUPTED`;
补充状态事件: `verify_failed` / `self_correct` / `react_*` / `plan_generated`; 审计事件 `maintenance_scan`。

### B. 正文与代码的已知矛盾 (读正文时请以代码为准)

1. **§6.3 执行流程**: 正文写死"parse_task→逐步执行→aggregate"单一线性流程; 实际是**策略分发**(react/reflect 优先, 失败降级 single_shot)。
2. **第 4 / 5 章数据模型**: 正文用 `@dataclass`(AgentRequest/AgentResponse/TaskPlan/TaskStep/StateEvent); 实际**统一 dict 风格**(见 `base.py` 注释), `parse_task` 返回 dict, 响应信封多 `cost_time` 字段。
3. **§6.2 构造函数**: 正文 5 参数; 实际 9 参数(多 `llm_planner` / `deps` / `long_term_memory` / `llm_client`)。
4. **§6.5 规划**: 正文称 LLM 规划"后续可替换"; 实际 **LLM 规划已是默认主路径**(`use_llm_planner` 默认 True), 规则式仅 fallback。
5. **README.md 实现说明表**称"与设计书完全匹配/dataclass"已过时, 4.1 示例(`SimpleAgent(tools=...)` + `AgentRequest`)与真实签名不符。

### C. 建议(独立任务): 深度回灌时新增 §6.11 自我验证 / §6.12 ReAct 引擎 / §6.13 记忆与个性化 / §6.14 流式 / §6.15 自主维护(方向4: 行为反思·记忆/代码文档自维护·定时提议·预授权自动); 扩写 §6.7 工具治理; 对齐第 4/5/6 章数据模型与构造函数。

---

# 1. 文档概述

## 1.1 文档目的

本文档为 RAG 与 Agent 系统核心业务层-Agent模块（`agent_module`）的独立设计说明书。

本模块负责系统中的智能代理能力，是任务解析、步骤规划、工具调用、状态记录与结果聚合的核心执行模块。模块在系统中的职责包括：

- 接收标准化 Agent 请求；
- 根据任务生成内部执行计划；
- 按步骤调用工具或能力；
- 记录状态、事件与中间结果；
- 聚合最终结果并输出系统统一响应；
- 在 Hybrid 模式下，作为主执行器调用 RAG 能力完成知识增强任务。

本文档作为本模块开发、测试、联调与后续替换实现的唯一标准依据。

## 1.2 适用人群

适用于 Agent 模块开发人员、协同调度模块开发人员、接口层开发人员、测试人员、架构设计人员及后续维护人员。

## 1.3 核心需求回顾

| 需求类型 | 具体要求 |
| :--- | :--- |
| 模块功能 | 提供任务解析、步骤规划、工具调用、状态记录、结果汇总能力，并支持 Hybrid 模式下的 RAG 协同。 |
| 开发语言 | Python 3.10+，最低 3.10，推荐 3.12，与系统整体保持一致。 |
| 开发模式 | 独立开发、可替换实现、通过抽象接口集成。 |
| 文档要求 | 与系统总设计 v1.1 及接口层 / 应用层 / 协同调度模块子设计保持一致。 |
| 模块约束 | 本模块不负责 HTTP 协议处理；不重新生成新的业务 trace_id；session_id 必须全流程一致；工具调用必须可注册、可替换、可测试。 |

# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于系统**核心业务层**，是系统中负责智能代理执行的核心模块。模块通过“任务解析 -> 执行计划 -> 工具调用 -> 状态记录 -> 结果聚合”的流程完成复杂任务。

本模块职责如下：

- 接收标准化任务请求并执行；
- 将自然语言任务解析为内部任务计划；
- 根据任务计划调用注册工具；
- 对工具调用过程进行重试与异常处理；
- 将执行过程写入状态存储；
- 聚合工具输出，形成最终统一结果；
- 在 Hybrid 模式下，基于工具能力调用 RAG 检索或知识增强能力。

本模块不负责：

- 不负责 HTTP/HTTPS 协议处理；
- 不负责业务语义级参数校验（由接口层负责）；
- 不负责统一请求标准化；
- 不直接访问应用层框架对象；
- 不在模块内部硬编码实例化外部工具实现；
- 不重新生成新的业务 `trace_id`。

## 2.2 模块边界

### 2.2.1 本模块负责

- 任务解析与计划生成；
- 工具注册与调用；
- 执行中间状态保存；
- 最终结果聚合与统一响应；
- Hybrid 模式下以 Agent 主导调用 RAG 工具能力。

### 2.2.2 本模块不负责

- 不判断 `type` 是否合法；
- 不决定 HTTP 状态码；
- 不直接控制上传、索引、路由、中间件、鉴权；
- 不自己实现 RAG 检索链路，而是通过工具/能力调用下游模块；
- 不自己创建新的 session_id 作为主路径，只接收或兼容兜底补齐。

## 2.3 依赖关系

### 2.3.1 上游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `orchestrator_module` | 由协同调度模块路由到 Agent 执行器。 |

### 2.3.2 下游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `state_store_module` | 保存执行状态、事件与中间结果。 |
| `llm_adapter_module`（可选） | 作为文本生成/总结能力来源。 |
| `rag_module`（通过工具能力间接依赖） | 在 Hybrid 模式或工具调用场景中提供知识增强能力。 |

### 2.3.3 基础依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `config_module` | 模块配置读取 |
| `log_module` | 执行日志记录 |
| `exception_module` | 异常封装 |
| `common_utils_module` | 通用辅助函数 |

说明：

- 本模块优先通过工具注册表或抽象接口依赖外部能力；
- `rag_module` 不作为硬编码内部实现直接耦合到 Agent 核心逻辑；
- `state_store_module` 允许替换为本地版、Redis 版等不同实现。

# 3. 统一项目结构规范

本模块遵循系统总设计 v1.1 的统一目录规范。

## 3.1 必选目录与文件

```text
agent_module/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── base.py
│   └── impl.py
├── model/
│   ├── __init__.py
│   └── data_model.py
├── tools/
│   ├── __init__.py
│   └── tool_registry.py
├── utils/
│   ├── __init__.py
│   └── tool_functions.py
├── config/
│   ├── __init__.py
│   └── config.py
├── tests/
│   ├── __init__.py
│   └── test_impl.py
├── README.md
└── requirements.txt
```

## 3.2 可选扩展目录

本模块按复杂度与演进阶段，可选增加：

- `strategies/`：不同任务规划策略
- `prompts/`：任务解析与总结模板
- `examples/`：示例任务、脚本
- `docs/`：补充说明材料

说明：

- 当前阶段可采用 `core/impl.py` 单文件实现；
- 若任务规划复杂度增高，建议拆出 `strategies/`；
- 新增扩展目录必须在 `README.md` 中说明职责与边界。

# 4. 核心数据模型设计

## 4.1 AgentRequest

```python
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

@dataclass
class AgentRequest:
    task: str
    session_id: Optional[str] = None
    trace_id: Optional[str] = None
    timeout: Optional[int] = None
    max_retries: Optional[int] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)
```

说明：

- `task` 为核心输入；
- `session_id` 由接口层补齐并透传，Agent 模块不应重新生成新的 session_id 作为主路径；
- `trace_id` 由应用层生成并经接口层、调度层透传；
- `timeout / max_retries` 为请求级配置，可覆盖模块默认配置；
- Hybrid 模式扩展信息统一通过 `extra_params` 传入。

## 4.2 AgentResponse

```python
from dataclasses import dataclass
from typing import Any, Dict, Optional

@dataclass
class AgentResponse:
    code: str
    message: str
    data: Optional[Dict[str, Any]] = None
    trace_id: str = ""
    retryable: bool = False
    details: Optional[Dict[str, Any]] = None
```

说明：

- 输出必须兼容系统统一响应结构；
- `data` 中建议包含最终答案、执行步骤摘要、工具结果摘要、会话标识等内容；
- 所有成功与失败响应都必须返回 `trace_id`。

## 4.3 TaskPlan / TaskStep / ToolResult / StateEvent

```python
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class TaskStep:
    step_id: str
    tool_name: str
    input_data: Dict[str, Any] = field(default_factory=dict)
    description: str = ""

@dataclass
class TaskPlan:
    session_id: str
    task: str
    steps: List[TaskStep] = field(default_factory=list)

@dataclass
class ToolResult:
    step_id: str
    tool_name: str
    success: bool
    output: Any = None
    error: Optional[str] = None

@dataclass
class StateEvent:
    session_id: str
    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
```

约束：

- `TaskPlan.session_id` 必须与请求中的 `session_id` 一致；
- `ToolResult` 必须能表达成功与失败；
- `StateEvent` 必须可直接写入状态存储。

# 5. 核心接口设计（抽象基类）

## 5.1 BaseAgent

```python
from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseAgent(ABC):
    @abstractmethod
    def execute(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行 Agent 任务
        :param request: 标准化请求字典
        :return: 统一响应字典
        """
        pass

    @abstractmethod
    def register_tool(self, name: str, tool: Any) -> None:
        """
        注册工具
        """
        pass
```

约束：

- `execute()` 接收的必须是接口层已标准化、调度层已透传的请求；
- `execute()` 不负责做业务语义校验；
- 工具注册必须可替换，不得依赖硬编码全局单例。

# 6. 核心实现设计（SimpleAgent）

## 6.1 类职责说明

`SimpleAgent` 是系统默认 Agent 实现，负责：

- 接收标准化任务请求；
- 基于任务解析规则生成任务计划；
- 按步骤调用工具；
- 将执行过程写入状态存储；
- 根据工具结果聚合最终输出；
- 在 Hybrid 模式下调用 RAG 工具能力完成知识增强。

本实现必须保持：

- 无 HTTP 依赖；
- 可并发复用；
- `session_id` 全流程一致；
- `trace_id` 全流程透传；
- 可通过工具注册表替换工具实现。

## 6.2 构造函数建议

```python
from state_store_module.core.base import BaseStateStore

class SimpleAgent(BaseAgent):
    def __init__(
        self,
        state_store: BaseStateStore | None = None,
        tool_registry: Any | None = None,
        timeout: int = 60,
        max_retries: int = 2,
        session_prefix: str = "session",
    ):
        self.state_store = state_store
        self.tool_registry = tool_registry
        self.timeout = timeout
        self.max_retries = max_retries
        self.session_prefix = session_prefix
```

说明：

- `state_store` 与 `tool_registry` 应通过构造函数注入；
- 不在构造函数中硬编码创建具体工具；
- `timeout / max_retries` 为模块默认值，可被请求级参数覆盖；
- `session_prefix` 仅用于兼容兜底生成，不作为主路径依赖。

## 6.3 execute() 处理顺序（强制）

```text
1. 从 request 中读取 task / session_id / trace_id / extra_params
2. 确定有效 session_id（优先使用入参，仅在兼容兜底场景下补齐）
3. 计算有效 timeout / max_retries（请求级优先于模块默认值）
4. 写入“任务开始”状态事件
5. 调用 parse_task(task, session_id, trace_id, extra_params) 生成 TaskPlan
6. 逐步执行 TaskPlan.steps：
   - 调用 _call_tool_with_retry()
   - 写入步骤状态事件
7. 调用 aggregate_results() 聚合工具结果
8. 写入“任务完成”状态事件
9. 返回统一响应
10. 异常时调用统一异常处理逻辑
```

## 6.4 session_id / trace_id 规则（修订重点）

### 6.4.1 session_id

- `session_id` 主路径来源于接口层；
- `execute()` 中确定一次有效 session_id 后，必须贯穿：
  - `parse_task()`
  - `TaskPlan`
  - `ToolResult`
  - `StateEvent`
  - 最终响应
- 禁止 `parse_task()` 再生成新的 session_id；
- 仅允许在历史兼容场景下由 `execute()` 做一次兜底补齐。

### 6.4.2 trace_id

- `trace_id` 由应用层入口生成，经接口层与调度层透传到本模块；
- 本模块不得重新生成新的业务 `trace_id`；
- 工具调用日志、状态事件、异常日志都应附带同一 `trace_id`；
- 最终响应必须保留该 `trace_id`。

## 6.5 parse_task() 设计（必须改）

```python
def parse_task(
    self,
    task: str,
    session_id: str,
    trace_id: str | None = None,
    extra_params: dict | None = None,
) -> TaskPlan:
    """
    解析任务并生成执行计划
    """
```

约束：

- `parse_task()` 不得自己创建新的 session_id；
- 生成的 `TaskPlan.session_id` 必须与入参一致；
- Hybrid 模式下可根据 `extra_params["execution_mode"] == "hybrid"` 选择不同规则；
- 当前版本允许使用规则式解析，后续可替换为 LLM 规划器。

## 6.6 Hybrid 模式设计（修订重点）

### 6.6.1 Hybrid 定义

当前版本中：

**Hybrid = Agent 主导执行 + 通过工具能力调用 RAG**

说明：

- Hybrid 不是单独写一套 Agent 派生实现；
- Hybrid 不由协同调度模块手工拼接业务流程；
- Agent 在 Hybrid 模式下，优先生成包含 `rag_search` 或等价知识增强工具的计划步骤；
- 最终回答由 Agent 聚合输出，而不是直接透传 RAG 原始结果。

### 6.6.2 任务解析建议

对于 Hybrid 请求，`parse_task()` 应优先选择如下工具顺序之一：

1. `rag_search` -> `llm_generate`
2. `rag_search` -> `summarizer`
3. `rag_search` -> `planner` -> `llm_generate`

当前版本可先采用简化规则：

- 若 `execution_mode == "hybrid"`，默认在计划中优先加入 `rag_search`
- 若任务明显为计算类，可混用 `calculator`
- 若无检索需要，可退化为纯 Agent 模式

## 6.7 工具注册与调用

### 6.7.1 ToolRegistry 职责

工具注册表负责：

- 注册工具
- 注销工具
- 查询工具
- 列出当前可用工具

建议接口：

```python
class ToolRegistry:
    def register(self, name: str, tool: Any) -> None: ...
    def unregister(self, name: str) -> None: ...
    def get(self, name: str) -> Any: ...
    def list_tools(self) -> list[str]: ...
```

### 6.7.2 _call_tool_with_retry() 规则

```text
1. 根据 step.tool_name 从注册表取工具
2. 若工具不存在，返回 TOOL_NOT_FOUND
3. 调用工具时附带 trace_id / session_id / step_id 上下文（如支持）
4. 若失败且 retryable，可按 max_retries 重试
5. 记录每次调用的状态事件与日志
6. 输出 ToolResult
```

约束：

- 工具调用重试次数以请求级配置优先；
- 不得在内部吞掉所有错误后仅返回“执行完成”；
- 失败结果必须可进入聚合器。

## 6.8 aggregate_results() 设计（必须增强）

当前版本不应只返回“某工具执行完成”。聚合器至少应输出：

- 最终文本回答或摘要
- 工具调用结果摘要
- 失败步骤摘要（若有）
- 可选的后续建议
- `session_id`

建议输出结构：

```json
{
  "answer": "最终回答文本",
  "session_id": "session_001",
  "steps": [
    {"step_id": "s1", "tool_name": "rag_search", "success": true},
    {"step_id": "s2", "tool_name": "llm_generate", "success": true}
  ],
  "tool_results_summary": [
    {"tool_name": "rag_search", "summary": "检索到 5 条相关片段"}
  ]
}
```

## 6.8.1 raw 结构化输出 → 自然语言合成（answer synthesis）

部分工具（如 `calculator` / `datetime` / 结构化分析类）直接返回 dict/JSON。若该原始结构落到
`final_answer`，会把 `{...}` 直喷给用户而非可读散文。聚合末尾因此加一道兜底：

- **检测** `_looks_like_raw_json(s)`：`s` 去空白后以 `{` 或 `[` 开头，且能被解析为字面量。
  两种形态都要识别——
  - 严格 JSON（双引号）：`json.loads` 命中；
  - **Python dict/list repr（单引号）**：`str(dict)` 的产物，`json.loads` 解析不了，用
    `ast.literal_eval` 兜底（只解析字面量、不执行代码，安全）。
- **合成** `_synthesize_natural_answer(task, raw, trace_id)`：检测命中时用一次 LLM（复用
  `_resolve_llm_planner` 的通道）把 raw 转成回答用户问题的中文散文。
- **fail-open**：无 LLM 通道或合成异常 → 返回空串，调用方保留原始 `final_answer`，绝不因合成失败丢答案。

> 回归：单引号 Python repr 漏判曾导致字典串直喷用户，已修（加 `ast.literal_eval` 兜底）并由
> `tests/test_answer_synthesis.py`（10 例）锁定。

## 6.8.2 authoritative 工具结果直达（可枚举清单不经 LLM 复述）

"列清单"这类**可枚举的结构化结果**（如 `software_info` 的 list：本机已安装软件），若交给 LLM
逐条复述成最终答案，会同时踩三个坑：被 `max_tokens` 截断（中文约 1300 字即停）、可能漏项/编造、
多烧一次 token。机制：

- **工具侧**：工具自行**确定性渲染**完整可读文本放进 `data.answer`，并标 `data.authoritative=True`
  表示"此结果即最终答案，无需 LLM 再加工"。
- **消费侧**：`_authoritative_answer(tool_results)` 在三条收尾路径统一识别——`aggregate_results`
  （single_shot）、流式 ReAct（`streaming.py`）、同步 `_react_execute`（`react_engine.py`）——命中则
  **直接用工具的完整 answer 作答**，跳过 LLM 复述与 §6.8.1 的 raw 合成，绕过 `_summarize_tool_output`
  的长度上限。多工具时取最后一个 authoritative 结果。

> 背景：曾出现"列已安装软件只显示到第 32 项就中断"——ReAct 流式把 60 项 observation 复述时撞上
> 默认 `max_tokens`。本机制让清单完整直达；另把 `LLMParam` 默认 `max_tokens` 2000→4096 根治一般
> 长回答的中途截断。由 `tests/test_software_info.py`、`tests/test_answer_synthesis.py` 锁定。

## 6.9 状态存储规范

### 6.9.1 必须记录的事件

- `task_started`
- `task_parsed`
- `step_started`
- `step_finished`
- `step_failed`
- `task_completed`
- `task_failed`

### 6.9.2 写入要求

- 所有状态事件必须使用同一个 `session_id`
- 建议在 payload 中带上 `trace_id`
- 状态写入失败不应阻断主任务，但必须记录日志

## 6.10 错误处理与统一返回

### 6.10.1 错误码约定

| 错误码 | 说明 |
| :--- | :--- |
| `SUCCESS` | 执行成功 |
| `TOOL_NOT_FOUND` | 工具未注册 |
| `TOOL_CALL_FAILED` | 工具调用失败 |
| `AGENT_TIMEOUT` | Agent 执行超时 |
| `AGENT_RUN_FAILED` | Agent 执行过程失败 |
| `UNKNOWN_ERROR` | 未知异常兜底 |

### 6.10.2 返回约束

- 所有成功与失败响应都必须返回 `trace_id`
- 若失败且属于可重试错误，必须正确设置 `retryable`
- `details` 需尽量结构化，例如标出失败步骤、工具名、重试次数

# 7. 模块调用示例

## 7.1 基础组装示例

```python
from bootstrap import build_state_store, build_tools
from agent_module.core.impl import SimpleAgent
from agent_module.tools.tool_registry import ToolRegistry

state_store = build_state_store()
registry = ToolRegistry()

for name, tool in build_tools().items():
    registry.register(name, tool)

agent = SimpleAgent(
    state_store=state_store,
    tool_registry=registry,
    timeout=60,
    max_retries=2
)
```

## 7.2 标准 Agent 调用示例

```python
request = {
    "task": "请帮我总结这段内容并列出关键点",
    "session_id": "session_001",
    "trace_id": "trace_demo_001",
    "extra_params": {}
}

result = agent.execute(request)
```

## 7.3 Hybrid 调用示例

```python
request = {
    "task": "请基于知识库回答这个问题，并给出总结",
    "session_id": "session_002",
    "trace_id": "trace_demo_002",
    "extra_params": {
        "execution_mode": "hybrid"
    }
}

result = agent.execute(request)
```

# 8. 测试规范

## 8.1 测试范围（强制）

| 测试类型 | 测试内容 |
| :--- | :--- |
| session 一致性测试 | 同一次任务中 parse / step / state / response 是否使用同一 session_id |
| trace 透传测试 | `trace_id` 是否贯穿工具调用、状态事件与最终响应 |
| 任务解析测试 | 规则式 parse_task 是否生成合理 TaskPlan |
| 工具注册测试 | 工具注册、查询、注销是否正常 |
| 工具调用测试 | 成功调用、失败调用、未注册工具、重试逻辑 |
| Hybrid 语义测试 | `execution_mode=hybrid` 时是否优先选择 `rag_search` |
| 聚合器测试 | aggregate_results 是否输出结构化结果 |
| 状态存储测试 | 关键事件是否被正确记录 |
| 超时与异常测试 | timeout、未知异常、状态写入异常等场景 |

## 8.2 Mock 示例

```python
class MockStateStore:
    def append_event(self, session_id, event):
        return True

class MockTool:
    def __call__(self, payload):
        return {"ok": True, "payload": payload}
```

# 9. 模块配置管理

建议配置示例如下：

```yaml
agent:
  timeout: 60
  max_retries: 2
  session_prefix: "session"
  default_execution_mode: "agent"
```

说明：

- `timeout / max_retries` 为模块默认值；
- 请求级参数可覆盖默认配置；
- `session_prefix` 仅用于兼容兜底生成；
- `default_execution_mode` 可用于无显式模式时的默认行为。

# 10. 交付物清单（强制）

模块开发完成后，需提交以下交付物：

| 交付物 | 说明 |
| :--- | :--- |
| `core/base.py` | 抽象基类，定义 Agent 核心接口 |
| `core/impl.py` | 默认 Agent 实现 |
| `model/data_model.py` | Agent 数据模型 |
| `tools/tool_registry.py` | 工具注册表 |
| `utils/tool_functions.py` | 任务解析、聚合、错误码辅助函数 |
| `config/config.py` | 模块配置读取逻辑 |
| `tests/test_impl.py` | 核心测试用例 |
| `README.md` | 模块说明文档 |
| `requirements.txt` | 依赖包清单 |

可选扩展交付物（按复杂度选择）：

- `strategies/*`
- `prompts/*`
- `examples/*`
- `docs/*`

若使用可选扩展目录，必须在 `README.md` 中说明职责与边界，并纳入测试覆盖。

# 11. 可替换性约束

| 约束项 | 说明 |
| :--- | :--- |
| 上游调用 | 调度层只能依赖 `BaseAgent` 抽象接口 |
| 工具依赖 | 工具必须通过注册表或注入方式接入，禁止硬编码全局工具实现 |
| session 约束 | `session_id` 由接口层补齐，本模块不得在主路径中重新生成第二个 session_id |
| trace 约束 | `trace_id` 由应用层生成并经接口层、调度层透传，本模块不得重新生成 |
| Hybrid 约束 | Hybrid 采用 Agent 主导 + RAG 工具协作模式 |
| 统一结构 | 请求与响应结构必须严格遵循系统总设计 v1.1 |

# 12. 常见问题（FAQ）

| 问题 | 说明 |
| :--- | :--- |
| Agent 是否需要再做 query/task 的参数校验？ | 不需要。业务语义校验统一由接口层 `request_response_module` 负责。 |
| 为什么 session_id 不能在 parse_task() 里重新生成？ | 因为这会导致同一任务的状态链断裂，无法保证状态存储与日志追踪一致。 |
| Hybrid 为什么由 Agent 主导？ | 因为当前版本定义为“Agent 主导 + RAG 工具协作”，这样可避免重复实现另一套执行器。 |
| 工具失败后是否必须进入聚合器？ | 是。失败信息也应纳入最终结果与状态记录，便于调试与用户提示。 |

# 13. 附录：系统错误码关联

本模块直接使用或透传的核心错误码如下：

| 错误码 | 来源 | 适用场景 |
| :--- | :--- | :--- |
| `SUCCESS` | 本模块/下游 | 请求成功 |
| `TOOL_NOT_FOUND` | 本模块 | 工具未注册 |
| `TOOL_CALL_FAILED` | 本模块/下游 | 工具调用失败 |
| `AGENT_TIMEOUT` | 本模块/下游 | Agent 执行超时 |
| `AGENT_RUN_FAILED` | 本模块 | Agent 执行过程失败 |
| `UNKNOWN_ERROR` | 异常兜底 | 未知运行时异常 |

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
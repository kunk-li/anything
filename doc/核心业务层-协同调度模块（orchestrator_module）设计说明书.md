# 核心业务层-协同调度模块（orchestrator_module）设计说明书

| 文档版本 | v1.1 |
| :--- | :--- |
| 最后更新 | 2026-03-19 |
| 维护责任人 | 协同调度模块开发负责人 |
| 状态 | 修订版 |

> 本修订版对齐《RAG与Agent系统架构设计说明书》v1.1、接口层-请求响应处理模块修订版、应用层-API服务模块修订版，重点修正统一请求透传、trace_id/session_id 规则、hybrid 模式定义、模块依赖边界与统一响应格式。

# 1. 文档概述

## 1.1 文档目的

本文档为 RAG 与 Agent 系统核心业务层-协同调度模块（`orchestrator_module`）的独立设计说明书。

本模块位于**接口层**与**核心业务执行模块（RAG / Agent）**之间，是系统内部统一的业务路由与调度入口，负责：

- 接收接口层传入的统一业务请求；
- 根据 `type` 路由到 `rag / agent / hybrid` 对应执行链路；
- 透传 `trace_id / session_id / extra_params`；
- 对下游返回结果做统一封装与兜底处理；
- 保证不同执行模式下输出结构一致；
- 作为系统内部“业务分发中枢”，隔离接口层与具体业务执行器的耦合。

本文档作为本模块开发、测试、联调与后续替换实现的唯一标准依据。

## 1.2 适用人群

适用于本模块开发人员、接口层开发人员、应用层开发人员、测试人员、架构设计人员及后续维护人员。

## 1.3 核心需求回顾

| 需求类型 | 具体要求 |
| :--- | :--- |
| 模块功能 | 作为核心业务层统一调度入口，负责 `rag / agent / hybrid` 三类请求的路由、调用与统一返回。 |
| 开发语言 | Python 3.10+，最低 3.10，推荐 3.12，与系统整体保持一致。 |
| 开发模式 | 独立开发、可替换实现、通过抽象接口集成。 |
| 文档要求 | 与系统总设计 v1.1、接口层与应用层子设计保持一致。 |
| 模块约束 | 本模块不负责 HTTP 协议处理，不负责请求语义校验，不直接依赖应用层；应优先依赖抽象接口或统一输出结构。 |

# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于系统**核心业务层**，位于**接口层**与**RAG / Agent 执行模块**之间，是系统内部统一的业务路由与调度中枢。

本模块职责如下：

- 接收接口层标准化后的请求；
- 根据 `type` 进行业务模式分发；
- 统一调度 RAG 模块、Agent 模块或 Hybrid 协作链路；
- 向下游透传 `trace_id / session_id / extra_params / top_k` 等关键字段；
- 对下游结果统一补齐标准输出字段；
- 捕获调度级异常并转换为系统统一错误码；
- 隔离接口层与具体业务执行器实现细节。

本模块不负责：

- 不负责 HTTP/HTTPS 协议处理；
- 不负责业务语义级参数校验（由接口层 `request_response_module` 负责）；
- 不负责鉴权、中间件、路由注册；
- 不负责具体的向量检索、工具调用、Prompt 拼接与模型调用；
- 不重新生成新的业务 `trace_id`；
- 不在本模块内部硬编码实例化 RAG / Agent 默认实现。

## 2.2 模块边界

### 2.2.1 本模块负责

- `type=rag` 请求路由到 RAG 执行器；
- `type=agent` 请求路由到 Agent 执行器；
- `type=hybrid` 请求路由到 Hybrid 执行策略；
- 在统一响应结构下汇总执行结果；
- 将下游标准错误码透传或在必要时转为调度级错误码。

### 2.2.2 本模块不负责

- 不重新判断 `query / task / top_k` 是否为空或不合法；
- 不重新生成 `session_id`（除非文档明确指定的 hybrid 兼容兜底场景）；
- 不直接修改请求的业务语义；
- 不在本模块编写 RAG 细节流程或 Agent 规划逻辑；
- 不直接访问数据库、向量库、对象存储或 Web 框架对象。

## 2.3 依赖关系

### 2.3.1 上游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `request_response_module` | 接收接口层标准化请求，并返回统一业务响应。 |

### 2.3.2 下游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `rag_module` | 执行 `type=rag` 的检索增强生成流程。 |
| `agent_module` | 执行 `type=agent` 与 `type=hybrid` 的智能代理流程。 |

### 2.3.3 基础依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `config_module` | 模块配置读取 |
| `log_module` | 调度过程日志记录 |
| `exception_module` | 调度异常封装 |
| `common_utils_module` | 公共辅助函数 |

说明：

- 本模块应优先依赖 `BaseRAG`、`BaseAgent` 等抽象接口；
- 具体默认实现由 bootstrap 注入，不得在本模块内直接硬编码 `SimpleRAG()` 或 `SimpleAgent()`。

# 3. 统一项目结构规范

本模块遵循系统总设计 v1.1 的统一目录规范。

## 3.1 必选目录与文件

```text
orchestrator_module/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── base.py
│   └── impl.py
├── model/
│   ├── __init__.py
│   └── data_model.py
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

- `strategies/`：不同 hybrid 调度策略
- `examples/`：标准请求与返回示例
- `docs/`：补充说明材料

说明：

- 当前阶段可采用 `core/impl.py` 单文件实现；
- 当 Hybrid 策略复杂时，建议拆分 `strategies/`；
- 新增扩展目录必须在 `README.md` 中说明职责与边界。

# 4. 核心数据模型设计

## 4.1 OrchestratorRequest

```python
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

@dataclass
class OrchestratorRequest:
    type: str
    query: Optional[str] = None
    task: Optional[str] = None
    session_id: Optional[str] = None
    top_k: int = 5
    trace_id: Optional[str] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)
```

说明：

- 输入结构必须与系统统一请求结构保持一致；
- 本模块不新增破坏统一结构的专属字段；
- 扩展参数统一通过 `extra_params` 透传。

## 4.2 OrchestratorResponse

```python
from dataclasses import dataclass
from typing import Any, Dict, Optional

@dataclass
class OrchestratorResponse:
    code: str
    message: str
    data: Optional[Dict[str, Any]] = None
    trace_id: str = ""
    retryable: bool = False
    details: Optional[Dict[str, Any]] = None
```

说明：

- 本模块输出必须与系统统一响应结构兼容；
- 若下游返回已是标准结构，本模块只做最小补齐；
- 所有成功与失败响应都必须返回 `trace_id`。

## 4.3 HybridMode（可选枚举）

```python
from enum import Enum

class HybridMode(str, Enum):
    AGENT_DRIVEN = "agent_driven"
```

说明：

- 当前版本建议只支持 `agent_driven` 一种 hybrid 模式；
- 未来若扩展，可增加 `rag_first`、`planner_driven` 等策略，但必须保持统一请求/响应结构不变。

# 5. 核心接口设计（抽象基类）

## 5.1 BaseOrchestrator

```python
from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseOrchestrator(ABC):
    @abstractmethod
    def route(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        根据请求类型路由并执行对应业务链路
        :param request: 标准化请求字典
        :return: 统一响应字典
        """
        pass

    @abstractmethod
    def register_modules(self, rag_runner=None, agent_runner=None) -> None:
        """
        注入或注册下游业务执行器
        """
        pass
```

约束：

- `route()` 接收的必须是接口层已标准化后的请求；
- `route()` 不负责做业务语义校验；
- `register_modules()` 仅用于依赖注入，不负责实例化具体实现。

# 6. 核心实现设计（SimpleOrchestrator）

## 6.1 类职责说明

`SimpleOrchestrator` 是系统默认协同调度实现，负责：

- 接收接口层统一请求；
- 根据 `type` 分发执行；
- 透传并保持 `trace_id / session_id / extra_params` 一致；
- 统一封装 `rag / agent / hybrid` 返回结果；
- 将调度级异常转为标准错误码。

本实现必须保持：

- 无 HTTP 依赖；
- 无框架对象依赖；
- 可并发复用；
- 可通过构造函数注入下游执行器。

## 6.2 构造函数建议

```python
from rag_module.core.base import BaseRAG
from agent_module.core.base import BaseAgent

class SimpleOrchestrator(BaseOrchestrator):
    def __init__(
        self,
        rag_runner: BaseRAG | None = None,
        agent_runner: BaseAgent | None = None,
    ):
        self.rag_runner = rag_runner
        self.agent_runner = agent_runner
```

说明：

- 不在构造函数内硬编码实例化默认实现；
- 默认实现由 bootstrap 层注入；
- 若未注入对应执行器，执行对应类型请求时应返回标准错误。

## 6.3 route() 处理顺序（强制）

```text
1. 读取 request['type']
2. 透传 trace_id / session_id / top_k / extra_params
3. 根据 type 分发：
   - rag -> _execute_rag()
   - agent -> _execute_agent()
   - hybrid -> _execute_hybrid()
4. 对下游结果进行统一补齐：
   - 缺失 trace_id 时补齐透传值
   - 缺失 retryable 时根据 code 计算
5. 返回统一响应
6. 异常时调用调度级异常处理逻辑，返回 ORCHESTRATOR_RUN_FAILED
```

## 6.4 rag 路由规则

### 6.4.1 输入要求

- 由接口层保证 `query` 已合法；
- 本模块不重新校验 `query` 业务语义；
- `session_id` 在 `rag` 场景可为空。

### 6.4.2 执行方式

```python
def _execute_rag(self, request: Dict[str, Any]) -> Dict[str, Any]:
    return self.rag_runner.run(request)
```

说明：

- 本模块不负责改写 query；
- 不在此处做检索、重排、Prompt 拼装；
- 下游 RAG 模块应返回统一响应结构。

## 6.5 agent 路由规则

### 6.5.1 输入要求

- 由接口层保证 `task` 已合法；
- `session_id` 应已由接口层补齐；
- 本模块不重新生成新的 `session_id`。

### 6.5.2 执行方式

```python
def _execute_agent(self, request: Dict[str, Any]) -> Dict[str, Any]:
    return self.agent_runner.execute(request)
```

## 6.6 hybrid 路由规则（修订重点）

### 6.6.1 Hybrid 定义（强制）

当前版本中：

**hybrid = Agent 主导 + 可调用 RAG 工具能力的执行模式**

说明：

- Hybrid 不是独立的第三套业务执行器；
- Hybrid 不是调度层自己手工拼接“先 RAG 再 Agent”的硬编码流程；
- Hybrid 应由 Agent 作为主执行器，在其内部通过工具或能力调用 RAG；
- 协同调度模块只负责将 `type=hybrid` 路由到 Agent 执行器，并显式标明执行模式。

### 6.6.2 推荐执行方式

```python
def _execute_hybrid(self, request: Dict[str, Any]) -> Dict[str, Any]:
    hybrid_request = dict(request)
    hybrid_request.setdefault("extra_params", {})
    hybrid_request["extra_params"]["execution_mode"] = "hybrid"
    return self.agent_runner.execute(hybrid_request)
```

### 6.6.3 设计原因

采用该定义的原因：

- 保持调度层简单，不把复杂业务流程堆到 orchestrator；
- 与总设计中“Agent 可调用 RAG 工具能力”的方向一致；
- 避免出现“hybrid 等于另写一套执行器”的重复实现；
- 便于后续扩展不同 hybrid 策略。

## 6.7 trace_id / session_id 规则（强制）

### 6.7.1 trace_id

- `trace_id` 由应用层入口生成并透传到接口层；
- 接口层将 `trace_id` 传入本模块；
- 本模块只透传，不得重新生成新的业务 `trace_id`；
- 若下游遗漏 `trace_id`，本模块允许将当前请求中的 `trace_id` 补回响应。

### 6.7.2 session_id

- `rag`：可为空，不强制补齐；
- `agent / hybrid`：应由接口层补齐后传入；
- 本模块默认不重新生成新的 `session_id`；
- 仅在兼容历史实现时，允许做兜底补齐，但必须保留文档说明，不作为主路径。

## 6.8 错误处理与统一返回

### 6.8.1 错误码约定

| 错误码 | 说明 |
| :--- | :--- |
| `SUCCESS` | 执行成功 |
| `ORCHESTRATOR_RUN_FAILED` | 调度级执行失败或未知路由异常 |
| `BAD_REQUEST` | 请求类型无法识别（理论上应由接口层提前拦截） |
| `RAG_RUN_FAILED` | 下游 RAG 失败（透传） |
| `AGENT_TIMEOUT` | 下游 Agent 超时（透传） |
| `TOOL_CALL_FAILED` | 下游 Agent 工具调用失败（透传） |
| `UNKNOWN_ERROR` | 未知异常兜底 |

### 6.8.2 返回约束

- 所有成功与失败响应都必须返回 `trace_id`
- 本模块不决定 HTTP 状态码
- 若下游已经返回标准结构，本模块只做最小补齐
- `retryable` 可根据错误码表统一计算

# 7. 模块调用示例

## 7.1 基础组装示例

```python
from bootstrap import build_rag_runner, build_agent_runner
from orchestrator_module.core.impl import SimpleOrchestrator

rag_runner = build_rag_runner()
agent_runner = build_agent_runner()

orchestrator = SimpleOrchestrator(
    rag_runner=rag_runner,
    agent_runner=agent_runner
)
```

## 7.2 接口层调用示例

```python
request = {
    "type": "rag",
    "query": "RAG 系统架构是什么？",
    "top_k": 5,
    "trace_id": "trace_demo_001",
    "extra_params": {}
}

result = orchestrator.route(request)
```

## 7.3 hybrid 调用示例

```python
request = {
    "type": "hybrid",
    "task": "请基于知识库回答这个问题，并给出总结",
    "session_id": "session_001",
    "trace_id": "trace_demo_002",
    "extra_params": {}
}

result = orchestrator.route(request)
```

# 8. 测试规范

## 8.1 测试范围（强制）

| 测试类型 | 测试内容 |
| :--- | :--- |
| 路由测试 | `rag / agent / hybrid` 是否正确分发 |
| trace 透传测试 | `trace_id` 是否原样透传并在响应中保留 |
| session 规则测试 | `rag` 不强制要求 session；`agent/hybrid` 是否正确透传 session |
| hybrid 语义测试 | `hybrid` 是否经由 Agent 主导执行，且正确带上 `execution_mode=hybrid` |
| 异常处理测试 | 下游抛异常时是否返回 `ORCHESTRATOR_RUN_FAILED` |
| 注入测试 | 未注册 rag_runner / agent_runner 时是否返回明确错误 |
| 标准结构测试 | 返回值是否符合统一响应结构 |

## 8.2 Mock 测试示例

```python
class MockRAG:
    def run(self, request):
        return {
            "code": "SUCCESS",
            "message": "ok",
            "data": {"mode": "rag"},
            "trace_id": request.get("trace_id"),
            "retryable": False,
            "details": None
        }

class MockAgent:
    def execute(self, request):
        return {
            "code": "SUCCESS",
            "message": "ok",
            "data": {
                "mode": request.get("extra_params", {}).get("execution_mode", "agent")
            },
            "trace_id": request.get("trace_id"),
            "retryable": False,
            "details": None
        }
```

# 9. 模块配置管理

建议配置示例如下：

```yaml
orchestrator:
  default_type: "rag"
  enable_trace: true
  hybrid_strategy: "agent_driven"
  timeout: 60
```

说明：

- `default_type` 仅作兼容配置，主路径应由接口层保证传入合法 type；
- `hybrid_strategy` 当前建议固定为 `agent_driven`；
- `timeout` 为调度级兜底超时配置，具体业务超时仍应由下游执行器控制。

# 10. 交付物清单（强制）

模块开发完成后，需提交以下交付物：

| 交付物 | 说明 |
| :--- | :--- |
| `core/base.py` | 抽象基类，定义协同调度核心接口 |
| `core/impl.py` | 默认调度实现 |
| `model/data_model.py` | 调度层数据模型 |
| `utils/tool_functions.py` | 请求透传、统一响应、错误码辅助工具 |
| `config/config.py` | 模块配置读取逻辑 |
| `tests/test_impl.py` | 核心测试用例 |
| `README.md` | 模块说明文档 |
| `requirements.txt` | 依赖包清单 |

可选扩展交付物（按复杂度选择）：

- `strategies/*`
- `examples/*`
- `docs/*`

若使用可选扩展目录，必须在 `README.md` 中说明职责与边界，并纳入测试覆盖。

# 11. 可替换性约束

| 约束项 | 说明 |
| :--- | :--- |
| 上游调用 | 接口层只能依赖 `BaseOrchestrator` 抽象接口 |
| 下游调用 | 本模块优先依赖 `BaseRAG`、`BaseAgent`，不得依赖其私有实现细节 |
| trace 约束 | `trace_id` 由应用层生成并经接口层透传，本模块不得重新生成 |
| session 约束 | `session_id` 由接口层补齐，本模块仅透传 |
| hybrid 约束 | Hybrid 采用 Agent 主导模式，不在调度层重写一套执行器 |
| 统一结构 | 请求与响应结构必须严格遵循系统总设计 v1.1 |

# 12. 常见问题（FAQ）

| 问题 | 说明 |
| :--- | :--- |
| orchestrator 是否需要再做参数校验？ | 不需要。业务语义校验统一由接口层 `request_response_module` 负责。 |
| hybrid 为什么不单独写一套执行器？ | 当前版本的 hybrid 定义为“Agent 主导 + 可调用 RAG 工具能力”，调度层只负责路由，不负责重写一套业务链路。 |
| orchestrator 能否直接实例化 SimpleRAG / SimpleAgent？ | 不建议。默认实现应由 bootstrap 注入，以满足可替换性约束。 |
| 为什么响应里必须保留 trace_id？ | 为了保证应用层、接口层、调度层、业务层日志能串成同一条链路。 |

# 13. 附录：系统错误码关联

本模块直接使用或透传的核心错误码如下：

| 错误码 | 来源 | 适用场景 |
| :--- | :--- | :--- |
| `SUCCESS` | 本模块/下游 | 请求成功 |
| `BAD_REQUEST` | 理论上由接口层拦截 | type 非 rag/agent/hybrid |
| `ORCHESTRATOR_RUN_FAILED` | 本模块 | 调度级未知异常或路由执行失败 |
| `RAG_RUN_FAILED` | 下游透传 | RAG 执行失败 |
| `AGENT_TIMEOUT` | 下游透传 | Agent 执行超时 |
| `TOOL_CALL_FAILED` | 下游透传 | Agent 工具调用失败 |
| `UNKNOWN_ERROR` | 异常兜底 | 未知运行时异常 |

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
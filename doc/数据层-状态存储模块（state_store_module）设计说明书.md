# 数据层-状态存储模块（state_store_module）设计说明书

| 文档版本 | v1.1 |
| :--- | :--- |
| 最后更新 | 2026-03-19 |
| 维护责任人 | 状态存储模块开发负责人 |
| 状态 | 修订版 |

> 本修订版对齐《RAG与Agent系统架构设计说明书》v1.1、Agent 模块修订版、协同调度模块修订版与统一请求/响应约束，重点修正 session_id 作为状态主键、状态事件标准结构、TTL/清理策略、状态写入与主任务解耦、示例实现与生产实现边界及统一错误码。

# 1. 文档概述

## 1.1 文档目的

本文档为 RAG 与 Agent 系统数据层-状态存储模块（`state_store_module`）的独立设计说明书。

本模块负责系统中的会话状态与执行事件存储能力，是“状态初始化 -> 状态读取 -> 事件追加 -> 状态更新 -> 过期清理”的核心数据层模块。模块在系统中的职责包括：

- 以 `session_id` 为主键维护会话状态；
- 存储 Agent / Hybrid 执行过程中的状态事件与中间结果；
- 提供状态读取、覆盖、追加、删除与过期清理能力；
- 为多轮任务调试、排障、会话追踪和执行恢复提供数据基础；
- 作为可替换的数据存储抽象层支持本地文件、Redis、数据库等实现。

本文档作为本模块开发、测试、联调与后续替换实现的唯一标准依据。

## 1.2 适用人群

适用于状态存储模块开发人员、Agent 模块开发人员、协同调度模块开发人员、测试人员、架构设计人员及后续维护人员。

## 1.3 核心需求回顾

| 需求类型 | 具体要求 |
| :--- | :--- |
| 模块功能 | 提供按 `session_id` 的状态保存、读取、事件追加、清理与过期控制能力。 |
| 开发语言 | Python 3.10+，最低 3.10，推荐 3.12，与系统整体保持一致。 |
| 开发模式 | 独立开发、可替换实现、通过抽象接口集成。 |
| 文档要求 | 与系统总设计 v1.1、Agent / 协同调度模块设计保持一致。 |
| 模块约束 | 本模块不负责 HTTP 协议处理；不重新生成 session_id；不承载业务规划逻辑；状态写入失败不应阻断主任务主路径。 |

# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于系统**数据层**，是系统中负责会话状态与执行事件存储的数据能力模块。

本模块职责如下：

- 以 `session_id` 为唯一会话主键存储状态；
- 保存任务执行上下文、步骤事件与中间结果；
- 提供按会话读取与删除能力；
- 支持会话过期时间（TTL）与清理策略；
- 为 Agent 多轮任务与执行追踪提供稳定的数据落点；
- 通过抽象接口屏蔽底层存储差异。

本模块不负责：

- 不负责 HTTP/HTTPS 协议处理；
- 不负责业务语义级参数校验；
- 不负责任务解析、工具调用、结果聚合；
- 不直接决定 session_id 生成策略；
- 不直接决定 HTTP 状态码；
- 不重新生成新的业务 `trace_id` 或 `session_id`。

## 2.2 模块边界

### 2.2.1 本模块负责

- 状态保存（save_state）
- 状态读取（get_state）
- 事件追加（append_event）
- 状态清空/删除（clear_state / delete_state）
- 过期清理（cleanup_expired）
- 存储容量基础控制（可选）

### 2.2.2 本模块不负责

- 不负责 session_id 生成；
- 不负责 Agent 计划逻辑；
- 不负责任务补偿或恢复策略编排；
- 不负责向量索引、文档存储、模型调用；
- 不负责应用层会话管理协议。

## 2.3 依赖关系

### 2.3.1 上游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `agent_module` | 写入与读取执行状态、事件与上下文 |
| `orchestrator_module`（可选） | 兼容性状态写入或路由跟踪 |
| 管理类服务（可选） | 会话清理、审计与诊断 |

### 2.3.2 下游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| 本地文件系统 / Redis / 数据库 | 实际状态持久化载体 |

### 2.3.3 基础依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `config_module` | 模块配置读取 |
| `log_module` | 状态写入与清理日志记录 |
| `exception_module` | 异常封装 |
| `common_utils_module` | 通用辅助函数 |

说明：

- 本模块必须通过统一抽象接口屏蔽底层存储差异；
- 示例实现可使用本地 JSON 文件；
- 生产实现可替换为 Redis、KV 存储或数据库。

# 3. 统一项目结构规范

本模块遵循系统总设计 v1.1 的统一目录规范。

## 3.1 必选目录与文件

```text
state_store_module/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── base.py
│   └── impl.py
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

- `providers/`：不同状态存储后端实现
- `examples/`：调用示例
- `docs/`：补充说明材料

说明：

- 当前阶段可采用 `core/impl.py` 单文件实现；
- 当 provider 类型增多时，建议拆分 `providers/`；
- 新增扩展目录必须在 `README.md` 中说明职责与边界。

# 4. 核心数据模型设计

## 4.1 SessionState

```python
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class SessionState:
    session_id: str
    state: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    expires_at: Optional[str] = None
```

说明：

- `session_id` 是状态主键；
- `state` 用于保存当前会话聚合状态；
- `events` 用于保存按时间顺序追加的执行事件；
- `created_at / updated_at / expires_at` 用于状态生命周期管理。

## 4.2 StateEvent（强制标准结构）

```python
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

@dataclass
class StateEvent:
    session_id: str
    event_type: str
    payload: Dict[str, Any] = field(default_factory=dict)
    trace_id: Optional[str] = None
    created_at: Optional[str] = None
```

说明：

- `event_type` 建议使用明确枚举值，如：
  - `task_started`
  - `task_parsed`
  - `step_started`
  - `step_finished`
  - `step_failed`
  - `task_completed`
  - `task_failed`
- `trace_id` 为可选但强烈推荐字段；
- 事件必须可序列化、可持久化。

## 4.3 状态主键规范（强制）

- `session_id` 是状态存储唯一主键；
- 状态存储模块不负责生成新的 `session_id`；
- 若上游未传入合法 `session_id`，应由上游（接口层 / Agent 主流程）补齐后再写状态；
- 不允许一个任务在状态链中出现多个不同 `session_id`。

# 5. 核心接口设计（抽象基类）

## 5.1 BaseStateStore

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

class BaseStateStore(ABC):
    @abstractmethod
    def save_state(self, session_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
        """
        保存或覆盖会话状态
        """
        pass

    @abstractmethod
    def get_state(self, session_id: str) -> Dict[str, Any]:
        """
        读取会话状态
        """
        pass

    @abstractmethod
    def append_event(self, session_id: str, event: Dict[str, Any]) -> Dict[str, Any]:
        """
        追加状态事件
        """
        pass

    @abstractmethod
    def clear_state(self, session_id: str) -> Dict[str, Any]:
        """
        清空或删除状态
        """
        pass
```

可选扩展接口：

```python
def cleanup_expired(self) -> Dict[str, Any]: ...
def exists(self, session_id: str) -> bool: ...
```

约束：

- `save_state()` 与 `append_event()` 必须以 `session_id` 为唯一主键；
- `append_event()` 不得在内部修改会话主键；
- `clear_state()` 应删除状态与事件，保持一致性。

# 6. 核心实现设计（标准状态存储实现）

## 6.1 类职责说明

标准状态存储实现负责：

- 保存和读取会话状态；
- 以事件日志方式记录执行轨迹；
- 提供 TTL 过期控制与清理；
- 保证 `session_id` 维度的数据一致性；
- 在状态写入失败时提供明确日志与错误，但不默认阻断主任务流程。

本实现必须保持：

- 无 HTTP 依赖；
- `session_id` 作为唯一主键；
- 状态与事件结构清晰；
- 支持示例实现与生产实现的能力边界；
- 返回结构稳定，便于 Agent 模块消费。

## 6.2 save_state() 规则（强制）

### 6.2.1 输入要求

- `session_id` 非空；
- `state` 必须可序列化；
- 若状态不存在，应创建新状态记录；
- 若状态已存在，应按覆盖或 merge 策略更新（需文档明确）。

### 6.2.2 推荐行为

当前推荐：

- `save_state()` 作为“当前聚合状态覆盖写”接口；
- `append_event()` 作为“执行过程事件追加”接口；
- 不要把两者混为同一种写法。

### 6.2.3 返回要求

成功响应建议结构：

```json
{
  "code": "SUCCESS",
  "message": "state saved",
  "data": {
    "session_id": "session_001"
  }
}
```

## 6.3 get_state() 规则

### 6.3.1 返回结构

建议返回：

```json
{
  "session_id": "session_001",
  "state": {
    "status": "running"
  },
  "events": [
    {
      "event_type": "task_started",
      "trace_id": "trace_demo_001"
    }
  ],
  "created_at": "2026-03-19T12:00:00Z",
  "updated_at": "2026-03-19T12:10:00Z",
  "expires_at": "2026-03-20T12:00:00Z"
}
```

### 6.3.2 异常语义

- 若状态不存在，应返回 `STATE_NOT_FOUND`（推荐新增）或兼容 `DOCUMENT_NOT_FOUND` 风格错误码时需在本模块文档明确；
- 当前推荐单独使用 `STATE_NOT_FOUND`，以区分文档类资源不存在。

## 6.4 append_event() 规则（修订重点）

### 6.4.1 输入要求

- `session_id` 非空；
- `event` 必须符合 `StateEvent` 或兼容结构；
- `event["session_id"]` 若存在，必须与入参 `session_id` 一致。

### 6.4.2 事件顺序

- 事件应按时间顺序追加；
- 追加时应自动补齐 `created_at`（若未提供）；
- 建议在 payload 中保留：
  - `step_id`
  - `tool_name`
  - `status`
  - `error`
  - `trace_id`

### 6.4.3 失败处理

- 状态写入失败不应默认阻断主任务；
- 但必须记录日志；
- 返回结构中应明确失败原因，供上游决定是否忽略或告警。

## 6.5 clear_state() / delete_state() 规则

### 6.5.1 本模块职责

- 删除或清空指定 `session_id` 下的状态与事件；
- 清理对应的本地文件 / Redis key / 数据库记录。

### 6.5.2 推荐设计

- `clear_state()`：清除当前状态内容，但保留空壳（可选）
- `delete_state()`：完全删除状态记录（可选扩展）
- 当前若只保留一个接口，推荐语义为“完全清除该会话状态”

## 6.6 TTL 与过期清理（修订重点）

### 6.6.1 过期策略

本模块应支持 TTL（time-to-live）：

- 每个会话状态可配置过期时间；
- 到达过期时间后，可在读取时判定无效，或由后台清理；
- 默认 TTL 由配置项控制。

### 6.6.2 cleanup_expired() 行为

推荐提供：

```python
def cleanup_expired(self) -> Dict[str, Any]:
    ...
```

行为要求：

- 扫描过期状态；
- 删除过期状态记录；
- 返回清理数量与会话列表摘要（可选）。

### 6.6.3 与业务层边界

- TTL 控制由状态存储模块实现；
- 何时触发清理可由上层调度或定时任务决定；
- 本模块不负责业务恢复策略。

## 6.7 示例实现与生产实现分层说明（强制）

### 6.7.1 示例实现

适用于：

- 本地开发
- 联调验证
- 小规模 PoC

允许简化：

- 使用本地 JSON 文件按 `session_id` 存储；
- 使用目录扫描做过期清理；
- 容量控制较简单。

### 6.7.2 生产实现

必须满足：

- 更可靠的 TTL 与清理能力；
- 更适合并发写入；
- 更稳定的状态读取性能；
- 更好的恢复与容量治理。

推荐方案：

- Redis
- 数据库
- KV 存储
- Redis + 持久化日志混合方案

## 6.8 状态写入与主任务解耦（强制）

### 6.8.1 原则

- 状态存储是“辅助可观测能力”，不是主任务唯一成败依据；
- 除非系统明确设定强一致依赖，否则状态写入失败不应直接让 Agent 主任务失败。

### 6.8.2 推荐行为

- Agent 在调用 `append_event()` / `save_state()` 失败时：
  - 记录日志；
  - 继续主任务；
  - 在最终结果中可按需提示状态记录异常（可选）。

### 6.8.3 例外

- 若某些业务强依赖状态恢复能力，可由上层显式开启“状态强一致模式”；
- 本模块默认不启用该模式。

## 6.9 trace_id 规则（强制）

- `trace_id` 由应用层生成，经接口层、调度层、Agent 模块透传到状态事件；
- 本模块不得重新生成新的业务 `trace_id`；
- `append_event()` 建议将 `trace_id` 持久化到事件中；
- 最终状态数据应支持按 `session_id` 追踪，也支持间接通过 `trace_id` 辅助排障。

## 6.10 错误处理与统一返回

### 6.10.1 错误码约定

| 错误码 | 说明 |
| :--- | :--- |
| `SUCCESS` | 执行成功 |
| `STATE_NOT_FOUND` | 状态不存在 |
| `STATE_SAVE_FAILED` | 状态保存失败 |
| `STATE_APPEND_FAILED` | 事件追加失败 |
| `STATE_CLEAR_FAILED` | 状态清理失败 |
| `PARAM_INVALID` | 输入参数不合法 |
| `UNKNOWN_ERROR` | 未知异常兜底 |

### 6.10.2 返回约束

- 成功与失败响应都建议兼容系统统一响应结构；
- `details` 尽量结构化，如：
  - `session_id`
  - `event_type`
  - `expires_at`
  - `operation`

# 7. 模块调用示例

## 7.1 保存状态示例

```python
result = state_store.save_state(
    session_id="session_001",
    state={"status": "running"}
)
```

## 7.2 追加事件示例

```python
result = state_store.append_event(
    session_id="session_001",
    event={
        "session_id": "session_001",
        "event_type": "task_started",
        "trace_id": "trace_demo_001",
        "payload": {
            "task": "请基于知识库回答这个问题"
        }
    }
)
```

## 7.3 读取状态示例

```python
result = state_store.get_state("session_001")
```

## 7.4 清理过期状态示例

```python
result = state_store.cleanup_expired()
```

# 8. 测试规范

## 8.1 测试范围（强制）

| 测试类型 | 测试内容 |
| :--- | :--- |
| save_state 测试 | 是否能正确保存会话状态 |
| get_state 测试 | 是否能按 `session_id` 正确读取状态 |
| append_event 测试 | 事件是否正确追加且顺序稳定 |
| session 一致性测试 | 事件中的 `session_id` 是否与主键一致 |
| TTL 测试 | 状态过期后是否正确失效或被清理 |
| cleanup_expired 测试 | 过期清理是否正常工作 |
| 状态写入失败测试 | 写入失败是否被正确记录且不默认阻断主任务 |
| 统一返回测试 | 返回结构是否兼容系统统一响应 |
| 容量控制测试（可选） | 存储目录或 key 数量限制是否生效 |

## 8.2 Mock 示例

```python
state = {
    "session_id": "session_001",
    "state": {"status": "running"},
    "events": [
        {
            "session_id": "session_001",
            "event_type": "task_started",
            "trace_id": "trace_demo_001",
            "payload": {"task": "demo"}
        }
    ]
}
```

# 9. 模块配置管理

建议配置示例如下：

```yaml
state_store:
  provider_type: "local_json"
  storage_dir: "./state_store"
  ttl_seconds: 86400
  enable_cleanup: true
  max_storage_mb: 512
```

说明：

- `provider_type` 用于区分本地文件、Redis 等实现；
- `storage_dir` 为本地状态目录；
- `ttl_seconds` 为默认过期时间；
- `enable_cleanup` 控制是否启用清理逻辑；
- `max_storage_mb` 为本地存储容量保护（可选）。

# 10. 交付物清单（强制）

模块开发完成后，需提交以下交付物：

| 交付物 | 说明 |
| :--- | :--- |
| `core/base.py` | 抽象基类，定义状态存储核心接口 |
| `core/impl.py` | 默认状态存储实现 |
| `utils/tool_functions.py` | session 校验、时间计算、持久化辅助函数 |
| `config/config.py` | 模块配置读取逻辑 |
| `tests/test_impl.py` | 核心测试用例 |
| `README.md` | 模块说明文档 |
| `requirements.txt` | 依赖包清单 |

可选扩展交付物（按复杂度选择）：

- `providers/*`
- `examples/*`
- `docs/*`

若使用可选扩展目录，必须在 `README.md` 中说明职责与边界，并纳入测试覆盖。

# 11. 可替换性约束

| 约束项 | 说明 |
| :--- | :--- |
| 上游调用 | Agent 模块或管理服务只能依赖 `BaseStateStore` 抽象接口 |
| 主键约束 | `session_id` 是唯一主键，本模块不得生成第二主键 |
| 事件约束 | 事件结构必须稳定，且可序列化 |
| TTL 约束 | 实现应支持过期控制，至少在生产实现中必须具备 |
| 失败语义 | 状态写入失败默认不阻断主任务，除非上游显式要求强一致模式 |
| 统一结构 | 返回结构必须稳定，并与系统统一响应兼容 |

# 12. 常见问题（FAQ）

| 问题 | 说明 |
| :--- | :--- |
| 为什么状态存储模块不负责生成 `session_id`？ | 因为 `session_id` 是会话主键，应由接口层或主任务入口统一生成并透传。 |
| 状态写入失败是否必须让 Agent 任务失败？ | 默认不需要。状态存储是辅助可观测能力，除非上游显式要求强一致模式。 |
| 为什么事件要单独追加，而不是只存最终状态？ | 因为事件链能支撑调试、排障、回放和多步任务分析。 |
| 为什么推荐生产环境用 Redis / DB？ | 因为本地文件更适合开发与联调，生产环境更需要并发、TTL、清理和恢复能力。 |

# 13. 附录：系统错误码关联

本模块直接使用或透传的核心错误码如下：

| 错误码 | 来源 | 适用场景 |
| :--- | :--- | :--- |
| `SUCCESS` | 本模块/下游 | 请求成功 |
| `STATE_NOT_FOUND` | 本模块 | 状态不存在 |
| `STATE_SAVE_FAILED` | 本模块 | 状态保存失败 |
| `STATE_APPEND_FAILED` | 本模块 | 事件追加失败 |
| `STATE_CLEAR_FAILED` | 本模块 | 状态清理失败 |
| `PARAM_INVALID` | 本模块 | 输入参数不合法 |
| `UNKNOWN_ERROR` | 异常兜底 | 未知运行时异常 |

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
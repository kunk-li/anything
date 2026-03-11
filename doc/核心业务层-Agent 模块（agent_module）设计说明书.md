# 核心业务层-Agent 模块（agent_module）设计说明书

---

## 1. 文档概述

### 1.1 文档目的
本文档为 RAG 与 Agent 系统核心业务层-Agent 模块的独立、完整设计说明书。文档严格遵循系统整体架构规范（基于已提供的 12 份设计文档），明确模块功能、项目结构、接口定义、依赖关系、数据格式及开发要求。旨在指导开发人员（含初学者）进行该模块的独立开发、测试与集成，确保模块与系统无缝兼容、可扩展、可替换。

### 1.2 适用人群
- **开发人员**：作为 Agent 模块开发、测试、维护的唯一标准依据。
- **测试人员**：作为编写测试用例、验收模块功能的标准依据。
- **项目管理人员**：参考本说明书进行模块开发进度管控与交付物验收。

### 1.3 核心需求回顾
| 需求类型 | 具体要求 |
| :--- | :--- |
| **模块功能** | 实现 Agent 智能代理核心能力，包括任务解析、决策规划、工具调用、结果汇总、失败重试与超时控制，支持会话状态持久化。 |
| **开发语言** | Python 3.10+，与系统整体保持一致。 |
| **开发模式** | 独立开发、互不依赖，基于本说明书即可完成开发，开发完成后通过统一接口集成至核心业务层。 |
| **文档要求** | 详细、易懂，适配初学者，明确模块所有可提前定义的内容（接口、数据格式、项目结构等）。 |
| **模块约束** | 需包含抽象基类（ABC），确保模块一致性；代码可与系统其他模块交换，数据格式符合系统统一标准。 |

### 1.4 术语定义
| 术语 | 定义 |
| :--- | :--- |
| **Agent** | 智能代理，具备任务解析、工具调用、决策规划能力，可自主完成复杂任务，协同 RAG 模块提升响应质量。 |
| **任务解析** | 将用户任务拆解为可执行的子步骤序列，确定每个步骤所需的工具及输入参数。 |
| **工具调用** | Agent 模块调用外部工具（如 RAG 检索、计算器、API 等）完成特定任务的能力。 |
| **会话状态** | Agent 运行过程中的各类状态数据（会话记忆、任务步骤、工具调用记录等），由状态存储模块持久化。 |
| **ABC** | 抽象基类，定义模块的核心接口与方法，强制子类实现，保障模块一致性。 |
| **标准化响应** | 模块输出统一格式结果，包含任务执行结果、状态码、错误信息，遵循系统统一异常码规范。 |

---

## 2. 模块核心设计

### 2.1 模块定位与职责
本模块属于系统**核心业务层**，是 Agent 智能代理能力的核心实现，串联数据层与基础支撑层所有相关模块，完整实现 Agent 任务执行全流程：
- 接收用户任务/查询，完成参数校验、异常处理。
- 调用任务解析逻辑，将复杂任务拆解为可执行的子步骤。
- 根据子步骤决策调用相应工具（RAG 检索、计算器、大模型生成等）。
- 调用**状态存储模块**持久化会话状态，支持任务延续与断点恢复。
- 汇总工具执行结果，生成最终响应。
- 输出标准化 Agent 响应，包含执行结果、状态信息、事件记录。
- 屏蔽底层模块差异，支持配置化切换工具、超时策略、重试机制。

### 2.2 输入输出规范

#### 2.2.1 输入
| 参数名 | 类型 | 必填 | 说明 | 默认值 |
| :--- | :--- | :--- | :--- | :--- |
| `task` | str | 是 | 用户任务描述文本 | - |
| `session_id` | str | 否 | 会话唯一标识，用于状态隔离 | 自动生成 |
| `max_retries` | int | 否 | 工具调用最大重试次数 | 从配置读取（默认 3） |
| `timeout` | int | 否 | 任务执行超时时间（秒） | 从配置读取（默认 30） |

#### 2.2.2 输出
标准化 Agent 响应格式（遵循系统统一异常码规范）：
```json
{
  "code": "SUCCESS",
  "message": "Agent 执行成功",
  "data": {
    "task": "用户任务描述",
    "session_id": "agent_session_001",
    "plan": { "plan": [...] },
    "results": [ { "tool": "...", "output": {...} } ]
  },
  "cost_time": 2.5,
  "trace_id": "b3b1c6d7f2b24f5aa0d8e7c8b9a1c2d3"
}
```

### 2.3 依赖关系
本模块是核心业务层核心模块之一，依赖基础支撑层、数据层、核心业务层其他模块。

#### 基础支撑层依赖
| 依赖模块 | 用途 |
| :--- | :--- |
| **通用工具模块** (`common_utils_module`) | 文本清洗、参数校验、时间处理。 |
| **配置管理模块** (`config_module`) | 读取 Agent 参数、超时配置、重试策略。 |
| **日志模块** (`log_module`) | 记录 Agent 全流程日志、异常信息（使用 `SystemLogger`）。 |
| **异常处理模块** (`exception_module`) | 抛出标准化 Agent 异常（`AgentException`）。 |

#### 数据层依赖
| 依赖模块 | 用途 |
| :--- | :--- |
| **状态存储模块** (`state_store_module`) | 持久化 Agent 会话状态、事件记录（使用 `BaseStateStore` 接口）。 |
| **大模型对接模块** (`llm_adapter_module`) | 调用聊天大模型进行任务解析与结果生成。 |

#### 核心业务层依赖
| 依赖模块 | 用途 |
| :--- | :--- |
| **RAG 模块** (`rag_module`) | 作为工具被 Agent 调用，执行知识库检索。 |

---

## 3. 统一项目结构规范

严格遵循系统整体项目结构规范，模块根目录命名为 `agent_module`（全小写，多单词用下划线连接），目录结构如下，开发者不得随意修改目录名称与层级。

```
agent_module/                  # 模块根目录
├── __init__.py                # 模块初始化文件，暴露核心类/方法
├── core/                      # 核心逻辑目录（抽象基类 + 实现类）
│   ├── __init__.py
│   ├── base.py                # 抽象基类（ABC），定义 Agent 核心接口
│   └── impl.py                # 具体实现类，继承抽象基类
├── model/                     # 数据模型目录（统一请求/响应模型）
│   ├── __init__.py
│   └── data_model.py          # Agent 请求/响应标准化模型
├── tools/                     # 工具注册目录（Agent 专属工具定义）
│   ├── __init__.py
│   └── tool_registry.py       # 工具注册与管理逻辑
├── utils/                     # 模块专属工具函数
│   ├── __init__.py
│   └── tool_functions.py      # 任务解析、结果汇总等工具
├── config/                    # 模块专属配置
│   ├── __init__.py
│   └── config.py              # 读取全局配置，补充 Agent 专属配置
├── tests/                     # 测试用例目录
│   ├── __init__.py
│   └── test_impl.py           # 核心功能测试用例
└── README.md                  # 模块说明文档（适配初学者）
```

### 3.1 目录结构说明
| 目录/文件 | 说明 |
| :--- | :--- |
| `agent_module` | 模块根目录，名称固定，与功能精准对应。 |
| `__init__.py` | 每个目录必须包含，根目录暴露核心类（如 `SimpleAgent`），方便其他模块调用。 |
| `core` | 核心逻辑目录，`base.py` 定义抽象接口，`impl.py` 实现 Agent 全流程。 |
| `model` | 模块专属数据模型，定义 Agent 请求、响应的标准化格式。 |
| `tools` | Agent 专属工具注册与管理，支持动态注册/注销工具。 |
| `utils` | 模块专属工具函数，任务解析、结果汇总、事件格式化等。 |
| `config` | 读取系统 Agent 配置，补充模块专属参数（如超时时间、重试次数）。 |
| `tests` | 覆盖任务解析、工具调用、全流程、异常场景的测试用例。 |
| `README.md` | 详细说明模块功能、接口、使用方法、依赖项、扩展步骤。 |

---

## 4. 核心数据模型设计

本模块定义统一的 Agent 请求/响应模型，所有接口均基于该模型交互，确保模块内部及与外部模块的数据格式统一，遵循系统整体数据规范。

### 4.1 Agent 请求模型（AgentRequest）
```python
from typing import Optional, Dict
from dataclasses import dataclass

@dataclass
class AgentRequest:
    """Agent 任务执行统一请求模型"""
    task: str                       # 用户任务描述（必填）
    session_id: Optional[str] = None # 会话唯一标识（可选，为空则自动生成）
    max_retries: Optional[int] = None # 工具调用最大重试次数（可选）
    timeout: Optional[int] = None     # 任务执行超时时间（秒，可选）
    extra_params: Optional[Dict] = None # 附加参数（可选）
```

### 4.2 任务计划模型（TaskPlan）
```python
from typing import List, Dict, Optional
from dataclasses import dataclass

@dataclass
class TaskStep:
    """任务子步骤模型"""
    tool: str                       # 工具名称（如 rag_search, calculator）
    input: Dict                     # 工具输入参数
    description: Optional[str] = None # 步骤描述（可选）

@dataclass
class TaskPlan:
    """任务执行计划模型"""
    plan: List[TaskStep]            # 子步骤列表
    created_at: Optional[str] = None # 计划生成时间
```

### 4.3 Agent 响应模型（AgentResponse）
```python
from typing import List, Optional, Dict
from dataclasses import dataclass

@dataclass
class ToolResult:
    """工具执行结果模型"""
    tool: str                       # 工具名称
    output: Dict                    # 工具输出（标准化响应格式）

@dataclass
class AgentResponse:
    """Agent 任务执行统一响应模型，遵循系统统一异常码规范"""
    code: str                       # 响应码：SUCCESS 或系统异常码
    message: str                    # 响应信息
    data: Optional[Dict] = None     # 响应数据
    cost_time: Optional[float] = None # 调用耗时（秒，可选）
    trace_id: Optional[str] = None    # 链路追踪 ID（可选）
```

### 4.4 状态事件模型（StateEvent）
```python
from typing import Dict
from dataclasses import dataclass

@dataclass
class StateEvent:
    """Agent 状态事件模型，用于状态存储模块持久化"""
    event_type: str                 # 事件类型（plan/tool/result/error）
    data: Dict                      # 事件数据
    timestamp: str                  # 事件时间戳
```

---

## 5. 核心接口设计（抽象基类）

### 5.1 Agent 抽象基类（BaseAgent）
定义模块核心接口，强制所有实现类必须实现，保障模块一致性、可替换性。位于 `core/base.py`。

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, Callable
from agent_module.model.data_model import AgentRequest, AgentResponse

class BaseAgent(ABC):
    """Agent 模块抽象基类，所有 Agent 实现类必须继承此类"""

    @abstractmethod
    def parse_task(self, task: str) -> Dict[str, Any]:
        """
        任务解析：将用户任务拆解为可执行的子步骤序列
        :param task: 用户任务描述
        :return: 任务计划（含子步骤列表）
        :raises AgentException: 解析失败时抛出标准化异常
        """
        pass

    @abstractmethod
    def execute(self, task: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """
        任务执行：执行完整任务流程，调用工具并汇总结果
        :param task: 用户任务描述
        :param session_id: 会话唯一标识（可选）
        :return: 标准化 Agent 响应结果（Dict 格式）
        :raises AgentException: 执行失败时抛出标准化异常
        """
        pass

    @abstractmethod
    def call_agent(self, request: AgentRequest) -> AgentResponse:
        """
        统一 Agent 调用接口（对外标准化入口）
        :param request: Agent 请求模型
        :return: Agent 响应模型
        """
        pass

    @abstractmethod
    def register_tool(self, tool_name: str, tool_func: Callable, description: str, input_schema: Dict) -> bool:
        """
        注册工具：动态注册新工具到 Agent 工具池
        :param tool_name: 工具名称
        :param tool_func: 工具 callable 函数
        :param description: 工具描述
        :param input_schema: 工具输入参数 schema
        :return: 注册成功返回 True，失败返回 False
        """
        pass

    @abstractmethod
    def unregister_tool(self, tool_name: str) -> bool:
        """
        注销工具：从 Agent 工具池中移除工具
        :param tool_name: 工具名称
        :return: 注销成功返回 True，失败返回 False
        """
        pass
```

---

## 6. 核心实现设计

### 6.1 标准 Agent 实现类（SimpleAgent）
继承抽象基类，实现完整 Agent 全流程，串联所有依赖模块，是系统默认使用的 Agent 实现类。位于 `core/impl.py`。

**类定义基础结构：**
```python
import time
from typing import Dict, Any, Optional, List
from .base import BaseAgent
from agent_module.model.data_model import AgentRequest, AgentResponse, TaskPlan, TaskStep, ToolResult, StateEvent
from agent_module.tools.tool_registry import ToolRegistry
from agent_module.utils.tool_functions import parse_task_by_rules, aggregate_results

# 依赖模块导入（遵循设计文档依赖关系）
from state_store_module.core.impl import LocalStateStore
from common_utils_module.core.impl import CommonUtils
from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from exception_module.core.impl import AgentException

class SimpleAgent(BaseAgent):
    """标准 Agent 实现类：基于规则的任务拆解 + 工具调用，系统默认实现"""

    def __init__(self, tools: Optional[Dict[str, Any]] = None):
        """
        初始化 Agent 模块，加载系统配置，注册默认工具
        :param tools: 初始工具字典（可选），格式：{"tool_name": callable}
        """
        # 基础支撑层初始化
        self.utils = CommonUtils()
        self.logger = SystemLogger()
        self.config = ConfigManager()
        self.config.load_config()

        # 状态存储模块初始化
        self.state_store = LocalStateStore()

        # 工具注册表初始化
        self.tool_registry = ToolRegistry()

        # 注册初始工具（若传入）
        if tools:
            for tool_name, tool_func in tools.items():
                self.tool_registry.register(tool_name, tool_func)

        # 读取系统 Agent 核心配置
        self.max_retries = int(self.config.get_config("agent.max_retries", 3))
        self.timeout = int(self.config.get_config("agent.timeout", 30))

        self.logger.info("Agent 模块初始化完成，加载系统默认配置")

    def parse_task(self, task: str) -> Dict[str, Any]:
        """实现抽象方法：任务解析，基于规则拆解任务为子步骤"""
        # 逻辑定义：
        # 1. 基于关键词规则解析任务（可扩展为 LLM 解析）
        # 2. 记录任务解析事件到状态存储
        # 3. 返回任务计划字典
        pass

    def execute(self, task: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """实现抽象方法：任务执行，调用工具并汇总结果"""
        # 逻辑定义：
        # 1. 参数校验（任务非空）
        # 2. 任务解析
        # 3. 执行子步骤（含超时检查、工具调用重试机制）
        # 4. 结果汇总
        # 5. 结果封装（包含 code, message, data, cost_time）
        pass

    def call_agent(self, request: AgentRequest) -> AgentResponse:
        """实现抽象方法：标准化 Agent 调用入口，请求校验 + 异常封装"""
        # 逻辑定义：
        # 1. 调用全流程 execute 方法
        # 2. 转换为 AgentResponse 模型
        # 3. 异常捕获与标准化返回
        pass

    def register_tool(self, tool_name: str, tool_func: Callable, description: str, input_schema: Dict) -> bool:
        """实现抽象方法：注册工具到工具池"""
        # 逻辑定义：调用 tool_registry.register 并记录日志
        pass

    def unregister_tool(self, tool_name: str) -> bool:
        """实现抽象方法：从工具池移除工具"""
        # 逻辑定义：调用 tool_registry.unregister 并记录日志
        pass

    def _get_or_create_session(self) -> str:
        """私有方法：获取或创建会话 ID"""
        pass

    def _call_tool_with_retry(self, tool_name: str, tool_input: Dict, session_id: str) -> Dict:
        """私有方法：工具调用（含重试机制）"""
        # 逻辑定义：
        # 1. 获取工具函数
        # 2. 循环调用直到成功或达到 max_retries
        # 3. 记录工具调用事件到状态存储
        # 4. 失败抛出 AgentException
        pass
```

### 6.2 工具注册表（ToolRegistry）
管理所有可用工具，支持动态注册与注销。位于 `tools/tool_registry.py`。

**类定义基础结构：**
```python
from typing import Dict, Any, Callable, Optional

class ToolRegistry:
    """Agent 工具注册表，管理所有可用工具"""

    def __init__(self):
        self._tools: Dict[str, Dict[str, Any]] = {}

    def register(self, tool_name: str, tool_func: Callable, description: str = "", input_schema: Optional[Dict] = None):
        """注册工具"""
        pass

    def unregister(self, tool_name: str):
        """注销工具"""
        pass

    def get(self, tool_name: str) -> Optional[Callable]:
        """获取工具函数"""
        pass

    def list_tools(self) -> Dict[str, Dict]:
        """列出所有已注册工具"""
        pass
```

---

## 7. 模块工具函数

### 7.1 任务解析工具（utils/tool_functions.py）
提供任务解析与结果汇总的辅助函数。

**函数定义基础结构：**
```python
from typing import Dict, Any, List

def parse_task_by_rules(task: str) -> Dict[str, Any]:
    """
    基于规则的任务解析（简化版，可扩展为 LLM 解析）
    :param task: 用户任务描述
    :return: 任务计划字典
    """
    # 逻辑定义：
    # 1. 检测检索关键词 -> 生成 rag_search 步骤
    # 2. 检测计算关键词 -> 生成 calculator 步骤
    # 3. 默认 -> 生成 llm_generate 步骤
    pass

def aggregate_results(results: List[Dict]) -> str:
    """
    汇总工具执行结果
    :param results: 工具执行结果列表
    :return: 汇总后的文本
    """
    pass
```

---

## 8. 模块调用示例

### 8.1 基础调用示例
```python
from agent_module.core.impl import SimpleAgent
from agent_module.model.data_model import AgentRequest

# 1. 初始化工具字典（示例）
def rag_search_tool(inp: dict):
    return {"code": "SUCCESS", "message": "ok", "data": {"answer": "检索结果"}}

tools = {"rag_search": rag_search_tool}

# 2. 初始化 Agent 实例
agent = SimpleAgent(tools=tools)

# 3. 标准化接口调用（推荐）
request = AgentRequest(
    task="计算 123 + 456 的结果",
    session_id="session_002",
    max_retries=3,
    timeout=30
)
response = agent.call_agent(request)
```

### 8.2 工具注册示例
```python
from agent_module.core.impl import SimpleAgent

agent = SimpleAgent()

def weather_tool(inp: dict):
    return {"code": "SUCCESS", "message": "ok", "data": {"weather": "晴"}}

agent.register_tool(
    tool_name="weather_query",
    tool_func=weather_tool,
    description="查询城市天气",
    input_schema={"city": "str"}
)
```

---

## 9. 测试规范

### 9.1 测试范围
| 测试类型 | 测试内容 |
| :--- | :--- |
| **任务解析测试** | 规则解析准确性、关键词匹配、默认行为。 |
| **工具调用测试** | 工具注册/注销、工具执行、重试机制。 |
| **全流程测试** | 任务解析→工具调用→结果汇总端到端测试。 |
| **异常场景测试** | 任务为空、工具不存在、超时、重试失败。 |
| **状态存储测试** | 会话状态保存、事件追加、状态读取。 |
| **配置切换测试** | 超时时间、重试次数配置切换。 |

### 9.2 测试用例基础框架
位于 `tests/test_impl.py`。
```python
import unittest
from agent_module.core.impl import SimpleAgent
from agent_module.model.data_model import AgentRequest
from exception_module.core.impl import AgentException

class TestAgentModule(unittest.TestCase):
    """Agent 模块单元测试类"""

    def setUp(self):
        """测试前置：初始化 Agent 实例、测试数据"""
        pass

    def test_task_parse(self):
        """测试任务解析功能"""
        pass

    def test_agent_execute(self):
        """测试 Agent 全流程执行"""
        pass

    def test_empty_task_execute(self):
        """测试空任务执行，验证异常抛出"""
        pass

    def test_tool_retry(self):
        """测试工具重试机制"""
        pass

    def test_call_agent_interface(self):
        """测试标准化接口 call_agent"""
        pass

    def test_tool_register_unregister(self):
        """测试工具注册与注销"""
        pass
```

---

## 10. 模块配置管理

### 10.1 配置项说明
位于 `config/config.py`，读取全局配置中的 `agent` 节点。

**配置类基础结构：**
```python
from config_module.core.impl import ConfigManager

class AgentConfig:
    """Agent 模块专属配置类"""

    def __init__(self):
        self.config_manager = ConfigManager()
        self.config_manager.load_config()

    def get_max_retries(self) -> int:
        """获取工具调用最大重试次数"""
        pass

    def get_timeout(self) -> int:
        """获取任务执行超时时间（秒）"""
        pass

    def get_default_session_prefix(self) -> str:
        """获取默认会话 ID 前缀"""
        pass
```

### 10.2 配置文件示例（系统全局 config.yaml）
```yaml
# Agent 模块配置
agent:
  max_retries: 3                    # 工具调用最大重试次数
  timeout: 30                       # 任务执行超时时间（秒）
  session_prefix: "agent_session"   # 会话 ID 前缀
  enable_state_persist: true        # 是否启用状态持久化
  state_expire_hours: 24            # 会话状态过期时间（小时）
```

---

## 11. 交付物清单（强制）

模块开发完成后，需提交以下交付物，确保符合系统集成要求：

| 交付物 | 说明 |
| :--- | :--- |
| `core/base.py` | 抽象基类，定义 Agent 核心接口。 |
| `core/impl.py` | 具体实现类（标准 Agent 全流程）。 |
| `model/data_model.py` | Agent 请求/响应标准化数据模型。 |
| `tools/tool_registry.py` | 工具注册与管理逻辑。 |
| `utils/tool_functions.py` | 任务解析、结果汇总工具。 |
| `config/config.py` | 模块配置读取逻辑。 |
| `tests/test_impl.py` | 核心功能测试用例。 |
| `README.md` | 模块说明文档（适配初学者）。 |
| `requirements.txt` | 依赖包清单（无额外专属依赖，复用系统依赖）。 |

---

## 12. 可替换性约束（强制）

| 约束项 | 说明 |
| :--- | :--- |
| **接口依赖** | 上层模块（协同调度、应用层）仅依赖 `BaseAgent` 抽象接口，禁止直接引用具体实现类。 |
| **扩展实现** | 新增 Agent 实现（如 LLM 任务解析 Agent、多轮对话 Agent）仅需实现 `BaseAgent` 抽象接口，无需修改上层代码。 |
| **工具注册** | 工具注册/注销接口必须遵循统一规范，支持动态扩展工具池。 |
| **响应格式** | 任务执行结果、标准化响应格式必须严格遵循系统统一标准。 |
| **异常处理** | 异常必须遵循系统统一异常码规范，抛出 `AgentException`。 |
| **状态存储** | 会话状态存储必须通过状态存储模块接口，禁止直接操作存储介质。 |

---

## 13. 常见问题（FAQ）

| 问题 | 解答 |
| :--- | :--- |
| **任务解析不准确怎么办？** | 可扩展 `parse_task_by_rules` 函数，增加关键词规则；或接入 LLM 进行智能任务解析。 |
| **工具调用失败如何处理？** | 模块内置重试机制，可通过配置调整 `max_retries`；失败后抛出 `TOOL_CALL_FAILED` 异常。 |
| **Agent 执行超时怎么办？** | 检查 `timeout` 配置是否合理；优化任务拆解，减少子步骤数量；检查工具执行效率。 |
| **如何新增自定义工具？** | 调用 `register_tool` 方法动态注册，或初始化时传入 `tools` 字典。 |
| **会话状态如何持久化？** | 模块自动调用状态存储模块，配置 `state_store.dir` 指定存储目录。 |
| **如何与 RAG 模块协同？** | 将 RAG 实例封装为工具函数，注册到 Agent 工具池，Agent 执行时自动调用。 |
| **多会话如何隔离？** | 每个会话使用独立 `session_id`，状态存储模块按 `session_id` 隔离存储。 |
| **如何调试 Agent 执行流程？** | 查看状态存储中的事件记录（plan/tool/result），或启用日志模块详细日志。 |

---

## 14. 附录：系统错误码关联

本模块异常与系统错误码表的关联如下：

| 错误码 | 异常类 | 适用场景 |
| :--- | :--- | :--- |
| `AGENT_TASK_PARSE_FAILED` | AgentException | 任务解析失败 |
| `AGENT_EXECUTE_FAILED` | AgentException | Agent 整体执行失败 |
| `AGENT_TIMEOUT` | AgentException | 任务执行超时 |
| `TOOL_NOT_FOUND` | AgentException | 调用的工具未注册 |
| `TOOL_CALL_FAILED` | AgentException | 工具调用失败（含重试后） |
| `PARAM_MISSING` | AgentException | 必填参数缺失（如任务为空） |


返回[系统架构设计](RAG%E4%B8%8EAgent%E7%B3%BB%E7%BB%9F%E6%9E%B6%E6%9E%84%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)
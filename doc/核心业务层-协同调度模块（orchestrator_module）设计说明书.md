# 核心业务层 - 协同调度模块（orchestrator_module）设计说明书

| 文档版本 | v1.0 |
| :--- | :--- |
| **最后更新** | 2026-02-28 |
| **维护责任人** | 协同调度模块开发负责人 |
| **状态** | 正式发布 |

---

## 1. 文档概述

### 1.1 文档目的
本文档为 RAG 与 Agent 系统核心业务层 - 协同调度模块的独立、完整设计说明书。文档严格遵循系统整体架构规范（基于已提供的 12 份设计文档），明确模块功能、项目结构、接口定义、依赖关系、数据格式及开发要求。旨在指导开发人员（含初学者）进行该模块的独立开发、测试与集成，确保模块与系统无缝兼容、可扩展、可替换。

### 1.2 适用人群
- **开发人员**：作为协同调度模块开发、测试、维护的唯一标准依据。
- **测试人员**：作为编写测试用例、验收模块功能的标准依据。
- **项目管理人员**：参考本说明书进行模块开发进度管控与交付物验收。

### 1.3 核心需求回顾
| 需求类型 | 具体要求 |
| :--- | :--- |
| **模块功能** | 实现系统核心业务入口调度，根据请求类型路由至 RAG 或 Agent 模块，支持 Hybrid 协同模式，统一异常处理与响应封装。 |
| **开发语言** | Python 3.10+，与系统整体保持一致。 |
| **开发模式** | 独立开发、互不依赖，基于本说明书即可完成开发，开发完成后通过统一接口集成至核心业务层。 |
| **文档要求** | 详细、易懂，适配初学者，明确模块所有可提前定义的内容（接口、数据格式、项目结构等）。 |
| **模块约束** | 需包含抽象基类（ABC），确保模块一致性；代码可与系统其他模块交换，数据格式符合系统统一标准。 |

### 1.4 术语定义
| 术语 | 定义 |
| :--- | :--- |
| **协同调度** | 根据用户请求特征，决策并路由至 RAG 模块（普通问答）或 Agent 模块（复杂任务），或两者协同工作的核心机制。 |
| **路由策略** | 决定请求流向的逻辑规则，包括基于显式类型（type 字段）或隐式意图识别的路由。 |
| **Hybrid 模式** | Agent 与 RAG 协同工作模式，通常由 Agent 主导，在执行过程中调用 RAG 作为工具。 |
| **ABC** | 抽象基类，定义模块的核心接口与方法，强制子类实现，保障模块一致性。 |
| **标准化响应** | 模块输出统一格式结果，包含执行结果、状态码、错误信息，遵循系统统一异常码规范。 |

---

## 2. 模块核心设计

### 2.1 模块定位与职责
本模块属于系统**核心业务层**，是核心业务逻辑的统一入口与调度中心，串联 RAG 模块与 Agent 模块，完整实现请求分发与协同控制：
- 接收接口层转发的标准化请求，完成参数校验、异常处理。
- 根据请求类型（type）或意图，决策路由至 RAG 模块或 Agent 模块。
- 支持 Hybrid 模式，协调 Agent 调用 RAG 工具的协同流程。
- 统一封装 RAG/Agent 的执行结果，输出标准化响应。
- 记录调度日志与链路追踪信息，便于问题排查。
- 屏蔽底层模块差异，支持配置化切换路由策略、超时控制。

### 2.2 输入输出规范

#### 2.2.1 输入
遵循系统统一请求格式（见架构设计说明书第 8 章）：
| 参数名 | 类型 | 必填 | 说明 | 默认值 |
| :--- | :--- | :--- | :--- | :--- |
| `type` | str | 是 | 请求类型：`rag` / `agent` / `hybrid` | `rag` |
| `query` | str | 条件必填 | 用户问题（type=rag 时必填） | - |
| `task` | str | 条件必填 | 用户任务（type=agent/hybrid 时必填） | - |
| `session_id` | str | 否 | 会话唯一标识 | 自动生成 |
| `top_k` | int | 否 | 检索片段数量（rag 模式） | 5 |
| `extra_params` | Dict | 否 | 额外扩展参数 | {} |

#### 2.2.2 输出
标准化协同调度响应格式（遵循系统统一异常码规范）：
```json
{
  "code": "SUCCESS",
  "message": "调度执行成功",
  "data": {
    "route_type": "rag",
    "result": { ... }  // RAG 或 Agent 的原始响应数据
  },
  "cost_time": 1.5,
  "trace_id": "b3b1c6d7f2b24f5aa0d8e7c8b9a1c2d3"
}
```

### 2.3 依赖关系
本模块是核心业务层入口，依赖基础支撑层及核心业务层其他模块。

#### 基础支撑层依赖
| 依赖模块 | 用途 |
| :--- | :--- |
| **通用工具模块** (`common_utils_module`) | 参数校验、时间处理。 |
| **配置管理模块** (`config_module`) | 读取调度策略、超时配置、默认路由。 |
| **日志模块** (`log_module`) | 记录调度全流程日志、异常信息（使用 `SystemLogger`）。 |
| **异常处理模块** (`exception_module`) | 抛出标准化调度异常（`OrchestratorException`）。 |

#### 核心业务层依赖
| 依赖模块 | 用途 |
| :--- | :--- |
| **RAG 模块** (`rag_module`) | 执行普通问答检索增强生成流程（通过 `BaseRAG` 接口）。 |
| **Agent 模块** (`agent_module`) | 执行复杂任务规划与工具调用流程（通过 `BaseAgent` 接口）。 |

---

## 3. 统一项目结构规范

严格遵循系统整体项目结构规范，模块根目录命名为 `orchestrator_module`（全小写，多单词用下划线连接），目录结构如下，开发者不得随意修改目录名称与层级。

```
orchestrator_module/                  # 模块根目录
├── __init__.py                # 模块初始化文件，暴露核心类/方法
├── core/                      # 核心逻辑目录（抽象基类 + 实现类）
│   ├── __init__.py
│   ├── base.py                # 抽象基类（ABC），定义调度核心接口
│   └── impl.py                # 具体实现类，继承抽象基类
├── model/                     # 数据模型目录（统一请求/响应模型）
│   ├── __init__.py
│   └── data_model.py          # 调度请求/响应标准化模型
├── utils/                     # 模块专属工具函数
│   ├── __init__.py
│   └── tool_functions.py      # 路由决策、结果封装等工具
├── config/                    # 模块专属配置
│   ├── __init__.py
│   └── config.py              # 读取全局配置，补充调度专属配置
├── tests/                     # 测试用例目录
│   ├── __init__.py
│   └── test_impl.py           # 核心功能测试用例
└── README.md                  # 模块说明文档（适配初学者）
```

### 3.1 目录结构说明
| 目录/文件 | 说明 |
| :--- | :--- |
| `orchestrator_module` | 模块根目录，名称固定，与功能精准对应。 |
| `__init__.py` | 每个目录必须包含，根目录暴露核心类（如 `SimpleOrchestrator`），方便其他模块调用。 |
| `core` | 核心逻辑目录，`base.py` 定义抽象接口，`impl.py` 实现调度全流程。 |
| `model` | 模块专属数据模型，定义调度请求、响应的标准化格式。 |
| `utils` | 模块专属工具函数，路由决策、结果封装、异常转换等。 |
| `config` | 读取系统调度配置，补充模块专属参数（如默认路由类型）。 |
| `tests` | 覆盖路由决策、模块调用、全流程、异常场景的测试用例。 |
| `README.md` | 详细说明模块功能、接口、使用方法、依赖项、扩展步骤。 |

---

## 4. 核心数据模型设计

本模块定义统一的调度请求/响应模型，所有接口均基于该模型交互，确保模块内部及与外部模块的数据格式统一，遵循系统整体数据规范。

### 4.1 调度请求模型（OrchestratorRequest）
```python
from typing import Optional, Dict, Any
from dataclasses import dataclass

@dataclass
class OrchestratorRequest:
    """协同调度统一请求模型"""
    # 请求类型：rag / agent / hybrid
    type: str
    # 用户问题（rag 模式必填）
    query: Optional[str] = None
    # 用户任务（agent/hybrid 模式必填）
    task: Optional[str] = None
    # 会话唯一标识（可选，为空则自动生成）
    session_id: Optional[str] = None
    # 检索片段数量（rag 模式可选）
    top_k: int = 5
    # 附加参数（可选，传递给底层模块）
    extra_params: Optional[Dict[str, Any]] = None
```

### 4.2 调度响应模型（OrchestratorResponse）
```python
from typing import Optional, Dict, Any
from dataclasses import dataclass

@dataclass
class OrchestratorResponse:
    """协同调度统一响应模型，遵循系统统一异常码规范"""
    # 响应码：SUCCESS 或系统异常码
    code: str
    # 响应信息
    message: str
    # 响应数据（包含路由类型与底层模块结果）
     Optional[Dict[str, Any]] = None
    # 实际路由类型（rag/agent/hybrid）
    route_type: Optional[str] = None
    # 调用耗时（秒，可选）
    cost_time: Optional[float] = None
    # 链路追踪 ID（可选）
    trace_id: Optional[str] = None
```

---

## 5. 核心接口设计（抽象基类）

### 5.1 协同调度抽象基类（BaseOrchestrator）
定义模块核心接口，强制所有实现类必须实现，保障模块一致性、可替换性。位于 `core/base.py`。

```python
from abc import ABC, abstractmethod
from typing import Dict, Any
from orchestrator_module.model.data_model import OrchestratorRequest, OrchestratorResponse

class BaseOrchestrator(ABC):
    """协同调度模块抽象基类，所有调度实现类必须继承此类"""

    @abstractmethod
    def route(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        路由决策与执行：根据请求内容路由到 RAG/Agent/协同，并执行对应流程
        :param request: 原始请求字典（含 type, query, task 等）
        :return: 标准化响应结果（Dict 格式）
        :raises OrchestratorException: 路由失败或执行失败时抛出标准化异常
        """
        pass

    @abstractmethod
    def call_orchestrator(self, request: OrchestratorRequest) -> OrchestratorResponse:
        """
        统一调度调用接口（对外标准化入口）
        :param request: 调度请求模型
        :return: 调度响应模型
        """
        pass

    @abstractmethod
    def register_module(self, module_type: str, module_instance: Any) -> bool:
        """
        注册业务模块：动态注册 RAG 或 Agent 实例到调度器
        :param module_type: 模块类型（rag/agent）
        :param module_instance: 模块实例（需实现 BaseRAG 或 BaseAgent 接口）
        :return: 注册成功返回 True，失败返回 False
        """
        pass
```

---

## 6. 核心实现设计

### 6.1 标准协同调度实现类（SimpleOrchestrator）
继承抽象基类，实现完整调度全流程，串联 RAG 与 Agent 模块，是系统默认使用的调度实现类。位于 `core/impl.py`。

**类定义基础结构：**
```python
import time
from typing import Dict, Any, Optional
from .base import BaseOrchestrator
from orchestrator_module.model.data_model import OrchestratorRequest, OrchestratorResponse
from orchestrator_module.utils.tool_functions import validate_request_params

# 依赖模块导入（遵循设计文档依赖关系，建议通过注入而非硬编码）
from common_utils_module.core.impl import CommonUtils
from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from exception_module.core.impl import OrchestratorException

class SimpleOrchestrator(BaseOrchestrator):
    """标准协同调度实现类：基于请求类型的路由 + 模块调用，系统默认实现"""

    def __init__(self, rag_runner=None, agent_runner=None):
        """
        初始化调度模块，加载系统配置，注册业务模块实例
        :param rag_runner: RAG 模块实例（需实现 BaseRAG 接口）
        :param agent_runner: Agent 模块实例（需实现 BaseAgent 接口）
        """
        # 基础支撑层初始化
        self.utils = CommonUtils()
        self.logger = SystemLogger()
        self.config = ConfigManager()
        self.config.load_config()

        # 业务模块注册表
        self.modules = {}
        if rag_runner:
            self.register_module("rag", rag_runner)
        if agent_runner:
            self.register_module("agent", agent_runner)

        # 读取系统调度核心配置
        self.default_type = self.config.get_config("orchestrator.default_type", "rag")
        self.timeout = int(self.config.get_config("orchestrator.timeout", 60))

        self.logger.info("协同调度模块初始化完成，加载系统默认配置")

    def route(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """实现抽象方法：路由决策与执行"""
        pass

    def call_orchestrator(self, request: OrchestratorRequest) -> OrchestratorResponse:
        """实现抽象方法：标准化调度调用入口"""
        pass

    def register_module(self, module_type: str, module_instance: Any) -> bool:
        """实现抽象方法：注册业务模块"""
        pass

    def _execute_rag(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """私有方法：执行 RAG 流程"""
        pass
    
    def _execute_agent(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """私有方法：执行 Agent 流程"""
        pass
```

### 6.2 工具函数（utils/tool_functions.py）
提供调度相关的辅助函数。

**函数定义基础结构：**
```python
from typing import Dict, Any

def validate_request_params(request: Dict[str, Any]) -> bool:
    """
    校验请求参数完整性
    :param request: 请求字典
    :return: 校验通过返回 True，否则抛出异常
    """
    # 逻辑定义：
    # 1. 检查 type 是否合法
    # 2. 检查 rag 模式下 query 是否存在
    # 3. 检查 agent 模式下 task 是否存在
    pass

def wrap_response_data(route_type: str, result: Dict[str, Any]) -> Dict[str, Any]:
    """
    封装响应数据，添加路由类型标识
    :param route_type: 路由类型
    :param result: 底层模块返回结果
    :return: 封装后的数据字典
    """
    pass
```

---

## 7. 模块调用示例

### 7.1 基础调用示例
```python
from orchestrator_module.core.impl import SimpleOrchestrator
from orchestrator_module.model.data_model import OrchestratorRequest

# 1. 初始化 RAG 与 Agent 实例（假设已实现）
# rag_instance = SimpleRAG(llm_client=...)
# agent_instance = SimpleAgent(tools=...)

# 2. 初始化调度器（注入依赖）
orchestrator = SimpleOrchestrator(rag_runner=rag_instance, agent_runner=agent_instance)

# 3. 标准化接口调用（RAG 模式）
request = OrchestratorRequest(
    type="rag",
    query="RAG 系统架构是什么？",
    top_k=5
)
response = orchestrator.call_orchestrator(request)
print(f"路由类型：{response.route_type}, 结果：{response.data}")

# 4. 标准化接口调用（Agent 模式）
request = OrchestratorRequest(
    type="agent",
    task="请整理一份 RAG 系统开发计划",
    session_id="session_001"
)
response = orchestrator.call_orchestrator(request)
```

### 7.2 动态注册模块示例
```python
from orchestrator_module.core.impl import SimpleOrchestrator

# 1. 初始化空调度器
orchestrator = SimpleOrchestrator()

# 2. 动态注册 RAG 模块
orchestrator.register_module("rag", rag_instance)

# 3. 动态注册 Agent 模块
orchestrator.register_module("agent", agent_instance)
```

---

## 8. 测试规范

### 8.1 测试范围
| 测试类型 | 测试内容 |
| :--- | :--- |
| **路由决策测试** | type 参数识别、默认类型 fallback、非法 type 处理。 |
| **模块调用测试** | RAG 模块调用、Agent 模块调用、Hybrid 模式调用。 |
| **全流程测试** | 请求→路由→执行→响应端到端测试。 |
| **异常场景测试** | 模块未注册、参数缺失、底层模块异常、超时。 |
| **注册功能测试** | 动态注册、重复注册、注销（若扩展）。 |

### 8.2 测试用例基础框架
位于 `tests/test_impl.py`。
```python
import unittest
from orchestrator_module.core.impl import SimpleOrchestrator
from orchestrator_module.model.data_model import OrchestratorRequest
from exception_module.core.impl import OrchestratorException

class MockRAG:
    def run(self, query, top_k):
        return {"code": "SUCCESS", "data": {"answer": "mock rag"}}

class MockAgent:
    def execute(self, task, session_id):
        return {"code": "SUCCESS", "data": {"result": "mock agent"}}

class TestOrchestratorModule(unittest.TestCase):
    """协同调度模块单元测试类"""

    def setUp(self):
        """测试前置：初始化调度器实例、Mock 模块"""
        self.rag_mock = MockRAG()
        self.agent_mock = MockAgent()
        self.orchestrator = SimpleOrchestrator(
            rag_runner=self.rag_mock, 
            agent_runner=self.agent_mock
        )

    def test_rag_route(self):
        """测试 RAG 模式路由"""
        request = {"type": "rag", "query": "test", "top_k": 5}
        result = self.orchestrator.route(request)
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["route_type"], "rag")

    def test_agent_route(self):
        """测试 Agent 模式路由"""
        request = {"type": "agent", "task": "test"}
        result = self.orchestrator.route(request)
        self.assertEqual(result["code"], "SUCCESS")
        self.assertEqual(result["data"]["route_type"], "agent")

    def test_invalid_type(self):
        """测试非法 type 参数"""
        request = {"type": "invalid", "query": "test"}
        with self.assertRaises(OrchestratorException):
            self.orchestrator.route(request)

    def test_module_not_found(self):
        """测试模块未注册场景"""
        empty_orch = SimpleOrchestrator()
        request = {"type": "rag", "query": "test"}
        with self.assertRaises(OrchestratorException):
            empty_orch.route(request)

    def test_call_orchestrator_interface(self):
        """测试标准化接口 call_orchestrator"""
        request = OrchestratorRequest(type="rag", query="test")
        response = self.orchestrator.call_orchestrator(request)
        self.assertIn(response.code, ["SUCCESS", "ORCHESTRATOR_RUN_FAILED"])

if __name__ == "__main__":
    unittest.main()
```

---

## 9. 模块配置管理

### 9.1 配置项说明
位于 `config/config.py`，读取全局配置中的 `orchestrator` 节点。

**配置类基础结构：**
```python
from config_module.core.impl import ConfigManager

class OrchestratorConfig:
    """协同调度模块专属配置类"""

    def __init__(self):
        self.config_manager = ConfigManager()
        self.config_manager.load_config()

    def get_default_type(self) -> str:
        """获取默认请求类型"""
        return self.config_manager.get_config("orchestrator.default_type", "rag")

    def get_timeout(self) -> int:
        """获取调度超时时间（秒）"""
        return int(self.config_manager.get_config("orchestrator.timeout", 60))
```

### 9.2 配置文件示例（系统全局 config.yaml）
```yaml
# 协同调度模块配置
orchestrator:
  default_type: "rag"             # 默认请求类型（rag/agent/hybrid）
  timeout: 60                     # 调度超时时间（秒）
  enable_intelligent_route: false # 是否启用智能意图识别路由（未来扩展）
```

---

## 10. 交付物清单（强制）

模块开发完成后，需提交以下交付物，确保符合系统集成要求：

| 交付物 | 说明 |
| :--- | :--- |
| `core/base.py` | 抽象基类，定义调度核心接口。 |
| `core/impl.py` | 具体实现类（标准调度全流程）。 |
| `model/data_model.py` | 调度请求/响应标准化数据模型。 |
| `utils/tool_functions.py` | 路由决策、结果封装工具。 |
| `config/config.py` | 模块配置读取逻辑。 |
| `tests/test_impl.py` | 核心功能测试用例。 |
| `README.md` | 模块说明文档（适配初学者）。 |
| `requirements.txt` | 依赖包清单（无额外专属依赖，复用系统依赖）。 |

---

## 11. 可替换性约束（强制）

| 约束项 | 说明 |
| :--- | :--- |
| **接口依赖** | 上层模块（接口层）仅依赖 `BaseOrchestrator` 抽象接口，禁止直接引用具体实现类。 |
| **扩展实现** | 新增调度实现（如智能意图识别调度）仅需实现 `BaseOrchestrator` 抽象接口，无需修改上层代码。 |
| **模块注册** | 业务模块（RAG/Agent）必须通过 `register_module` 注入，禁止在调度器内部硬编码实例化。 |
| **响应格式** | 调度结果、标准化响应格式必须严格遵循系统统一标准。 |
| **异常处理** | 异常必须遵循系统统一异常码规范，抛出 `OrchestratorException`。 |

---

## 12. 常见问题（FAQ）

| 问题 | 解答 |
| :--- | :--- |
| **路由类型不支持怎么办？** | 检查请求 `type` 参数是否为 `rag`/`agent`/`hybrid`；检查配置中是否启用了自定义路由类型。 |
| **提示模块未注册？** | 确保在初始化 `SimpleOrchestrator` 时传入了 `rag_runner` 或 `agent_runner` 实例。 |
| **Hybrid 模式如何工作？** | Hybrid 模式在调度层视为 Agent 模式，由 Agent 内部通过工具调用 RAG 实现协同，无需调度层特殊处理。 |
| **如何扩展智能路由？** | 新增实现类继承 `BaseOrchestrator`，在 `route` 方法中接入意图识别模型，替代简单的 `type` 判断。 |
| **调度超时如何处理？** | 检查 `orchestrator.timeout` 配置；优化底层 RAG/Agent 执行效率；在 `route` 方法中增加超时判断逻辑。 |
| **如何调试路由流程？** | 查看日志模块中 `orchestrator_module` 相关日志，关注 `route_type` 与模块调用记录。 |

---

## 13. 附录：系统错误码关联

本模块异常与系统错误码表的关联如下（补充至架构设计说明书第 10 章）：

| 错误码 | 异常类 | 适用场景 |
| :--- | :--- | :--- |
| `ORCHESTRATOR_RUN_FAILED` | OrchestratorException | 调度器整体执行失败 |
| `MODULE_NOT_FOUND` | OrchestratorException | 调用的业务模块（RAG/Agent）未注册 |
| `BAD_REQUEST` | OrchestratorException | 请求类型 type 不支持或参数缺失 |
| `ORCHESTRATOR_TIMEOUT` | OrchestratorException | 调度执行超时 |

---

**文档版本**: v1.0  
**最后更新**: 2026-02-28  
**维护责任人**: 协同调度模块开发负责人

返回[系统架构设计](RAG%E4%B8%8EAgent%E7%B3%BB%E7%BB%9F%E6%9E%B6%E6%9E%84%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)
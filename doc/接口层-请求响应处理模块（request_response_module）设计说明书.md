# 接口层-请求响应处理模块（request_response_module）设计说明书

| 文档版本 | v1.1 |
| :--- | :--- |
| 最后更新 | 2026-03-19 |
| 维护责任人 | 请求响应处理模块开发负责人 |
| 状态 | 修订版 |

> 本修订版对齐《RAG与Agent系统架构设计说明书》v1.1，重点修正 trace_id 来源、统一请求/响应结构、接口边界、错误码与校验职责。
---

## 1. 文档概述

### 1.1 文档目的
本文档为 RAG 与 Agent 系统接口层 - 请求响应处理模块的独立设计说明书。
本模块位于应用层与核心业务层之间，是系统内部唯一的业务请求标准化入口，负责完成：

- 统一业务参数校验
- 请求标准化与默认值补齐
- trace_id / session_id 透传与补齐
- 调用协同调度模块执行核心流程
- 统一响应封装
- 异常转换为系统标准错误响应

本文档作为本模块开发、测试、联调与后续替换实现的唯一标准依据。
### 1.2 适用人群
- **开发人员**：作为请求响应处理模块开发、测试、维护的唯一标准依据。
- **测试人员**：作为编写测试用例、验收模块功能的标准依据。
- **项目管理人员**：参考本说明书进行模块开发进度管控与交付物验收。

### 1.3 核心需求回顾
| 需求类型 | 具体要求 |
| :--- | :--- |
| 模块功能 | 作为接口层唯一业务请求入口，完成业务语义校验、请求标准化、调度调用、统一响应封装、异常处理。 |
| 开发语言 | Python 3.10+，最低 3.10，推荐 3.12，与系统整体保持一致。 |
| 开发模式 | 独立开发、可替换实现、通过抽象接口集成。 |
| 文档要求 | 与系统总设计 v1.1 保持一致，明确请求/响应、trace_id、错误码、边界与测试要求。 |
| 模块约束 | 应用层仅依赖 `BaseRequestHandler`；本模块不得承载 HTTP 协议逻辑，不得直接决定 HTTP 状态码。 |

### 1.4 术语定义
| 术语 | 定义 |
| :--- | :--- |
| **请求处理** | 接收外部请求（API/控制台），完成参数校验、格式标准化、类型转换，转发至核心业务层。 |
| **响应处理** | 接收核心业务层返回结果，封装为系统统一响应格式，包含状态码、消息、数据、追踪 ID 等。 |
| **异常封装** | 捕获系统运行过程中的异常，转换为标准化错误响应，遵循系统统一错误码规范。 |
| **ABC** | 抽象基类，定义模块的核心接口与方法，强制子类实现，保障模块一致性。 |
| **标准化响应** | 模块输出统一格式结果，包含 code、message、data、trace_id 等字段，遵循系统统一响应结构。 |

---

## 2. 模块核心设计

### 2.1 模块定位与职责

本模块属于系统**接口层**，位于**应用层**与**核心业务层**之间，是系统内部统一的业务请求处理入口，不直接暴露 HTTP 协议接口。

核心职责如下：
- 接收应用层传入的原始业务请求字典或统一请求对象；
- 执行业务语义层参数校验（如 type/query/task/top_k/session_id）；
- 对请求做标准化处理，补齐默认值并透传 trace_id；
- 按统一格式调用协同调度模块；
- 将下游返回结果封装为系统统一响应结构；
- 捕获并转换全流程异常为标准化错误响应；
- 记录请求、响应与异常日志，确保 trace_id 可追踪。

本模块不负责：
- 不负责 HTTP/HTTPS 协议处理；
- 不负责认证鉴权、CORS、路由、中间件；
- 不负责直接决定 HTTP 状态码；
- 不直接执行 RAG / Agent / Hybrid 业务逻辑。

### 2.2 输入输出规范

#### 2.2.1 输入

遵循系统统一请求格式（见系统总设计第 8 章）：

| 参数名 | 类型 | 必填 | 说明 | 默认值 |
| :--- | :--- | :--- | :--- | :--- |
| `type` | str | 是 | 请求类型：`rag` / `agent` / `hybrid` | `rag` |
| `query` | str | 条件必填 | 用户问题（`type=rag` 时必填） | - |
| `task` | str | 条件必填 | 用户任务（`type=agent/hybrid` 时必填） | - |
| `session_id` | str | 否 | 会话唯一标识；`rag` 可为空，`agent/hybrid` 若为空由本模块补齐 | - |
| `top_k` | int | 否 | 检索片段数量（仅 `rag` 相关） | 5 |
| `trace_id` | str | 否 | 链路追踪 ID；由应用层入口生成并透传 | - |
| `extra_params` | Dict | 否 | 扩展参数透传字典，不允许各层自定义改名 | `{}` |

#### 2.2.2 输出

标准化响应格式（遵循系统总设计第 10 章）：

**成功响应**
```json
{
  "code": "SUCCESS",
  "message": "ok",
  "data": {},
  "trace_id": "b3b1c6d7f2b24f5aa0d8e7c8b9a1c2d3",
  "retryable": false,
  "details": null,
  "cost_time": 0.123456
}
```

**失败响应**
```json
{
  "code": "PARAM_INVALID",
  "message": "top_k 参数必须为 1~50 的整数",
  "data": null,
  "trace_id": "b3b1c6d7f2b24f5aa0d8e7c8b9a1c2d3",
  "retryable": false,
  "details": {
    "field": "top_k",
    "expected": "integer (1~50)",
    "actual": "string",
    "example": 10
  },
  "cost_time": 0.004231
}
```


---

### 2.3 依赖关系（小修）
这里主要补一句边界：

```md
#### 应用层协作约束
| 协作对象 | 说明 |
| :--- | :--- |
| API服务模块 / 控制台模块 | 仅负责生成或透传 `trace_id`，并调用 `BaseRequestHandler.handle()`；不得重复实现业务语义校验。 |
---

## 3. 统一项目结构规范

本模块遵循系统总设计 v1.1 的统一目录规范。

### 3.1 必选目录与文件
```text
request_response_module/
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

### 3.1 目录结构说明
| 目录/文件 | 说明 |
| :--- | :--- |
| `request_response_module` | 模块根目录，名称固定，与功能精准对应。 |
| `__init__.py` | 每个目录必须包含，根目录暴露核心类（如 `RequestHandler`），方便其他模块调用。 |
| `core` | 核心逻辑目录，`base.py` 定义抽象接口，`impl.py` 实现请求响应处理全流程。 |
| `model` | 模块专属数据模型，定义请求、响应的标准化格式。 |
| `utils` | 模块专属工具函数，请求校验、响应封装、异常转换等。 |
| `config` | 读取系统处理配置，补充模块专属参数（如校验规则、响应模板）。 |
| `tests` | 覆盖请求校验、响应封装、异常处理、全流程的测试用例。 |
| `README.md` | 详细说明模块功能、接口、使用方法、依赖项、扩展步骤。 |

本模块如有需要，可增加：
examples/：示例请求与响应
schemas/：可选的 JSON Schema / Pydantic Schema
docs/：补充说明材料
新增扩展目录时，必须在 README.md 中说明职责与边界。

---


---

## 4. 核心数据模型设计（整段修订）

原文的 `UnifiedRequest` 仍保留了 `source`、`timestamp` 这类字段，但总设计 v1.1 统一请求格式中没有这两个字段，且当前系统实际主链路更依赖 `trace_id + extra_params`。:contentReference[oaicite:13]{index=13} :contentReference[oaicite:14]{index=14}

建议改成下面这版。

### 4.1 UnifiedRequest
```python
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

@dataclass
class UnifiedRequest:
    """系统统一请求模型（接口层标准）"""
    type: str
    query: Optional[str] = None
    task: Optional[str] = None
    session_id: Optional[str] = None
    top_k: int = 5
    trace_id: Optional[str] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)
```


### 4.2 统一响应模型（UnifiedResponse）
```python
from dataclasses import dataclass
from typing import Any, Dict, Optional

@dataclass
class UnifiedResponse:
    """系统统一响应模型"""
    code: str
    message: str
    data: Optional[Dict[str, Any]] = None
    trace_id: str = ""
    retryable: bool = False
    details: Optional[Dict[str, Any]] = None
    cost_time: Optional[float] = None
```

### 4.3 错误详情模型（ErrorDetails）
```python
from typing import Optional, Any
from dataclasses import dataclass

@dataclass
class ErrorDetails:
    """错误详情模型，用于失败响应中的 details 字段"""
    # 错误字段名
    field: Optional[str] = None
    # 期望值/类型
    expected: Optional[Any] = None
    # 实际值/类型
    actual: Optional[Any] = None
    # 示例值
    example: Optional[Any] = None
    # 修复建议
    hint: Optional[str] = None
    
    allowed: Optional[list[Any]] = None
```

---

## 5. 核心接口设计（抽象基类）

### 5.1 请求响应处理抽象基类（BaseRequestHandler）
定义模块核心接口，强制所有实现类必须实现，保障模块一致性、可替换性。位于 `core/base.py`。

```python
from abc import ABC, abstractmethod
from typing import Dict, Any

from ..model.data_model import UnifiedRequest, UnifiedResponse


class BaseRequestHandler(ABC):
    @abstractmethod
    def validate_request(self, request: Dict[str, Any]) -> tuple[bool, str, str]:
        """
        请求参数校验
        :return: (是否通过, 错误信息, 错误码)
        """
        pass

    @abstractmethod
    def handle(self, request: Dict[str, Any], trace_id: Optional[str] = None) -> Dict[str, Any]:
        """
        处理请求全流程
        :param trace_id: 由应用层透传的 trace_id；为空时本模块兜底生成
        """
        pass

    @abstractmethod
    def format_response(self, code: str, message: str, data: Any, 
                        trace_id: str, cost_time: float = None) -> Dict[str, Any]:
        """
        格式化响应：将处理结果封装为系统统一响应格式
        :param code: 响应码
        :param message: 响应信息
        :param data: 响应数据
        :param trace_id: 链路追踪 ID
        :param cost_time: 调用耗时
        :return: 标准化响应字典
        """
        pass

    @abstractmethod
    def handle_exception(self, exception: Exception, trace_id: str) -> Dict[str, Any]:
        """
        异常处理：捕获异常并转换为标准化错误响应
        :param exception: 捕获的异常对象
        :param trace_id: 链路追踪 ID
        :return: 标准化错误响应字典
        """
        pass
```

---

## 6. 核心实现设计

### 6.1 标准请求响应处理实现类（RequestHandler）
继承抽象基类，实现完整请求响应处理全流程，串联协同调度模块与基础支撑层，是系统默认使用的处理实现类。位于 `core/impl.py`。
`RequestHandler` 是系统默认请求处理实现，负责：
- 调用统一工具函数完成业务语义校验
- 标准化请求结构
- 透传 trace_id / session_id
- 调用协同调度模块
- 统一封装响应
- 将异常转换为系统标准错误响应

本实现必须保持无状态，可并发使用。
**类定义基础结构：**
```python
from orchestrator_module.core.base import BaseOrchestrator


class RequestHandler(BaseRequestHandler):
    def __init__(self, orchestrator: BaseOrchestrator):
        """
        初始化处理模块，注入协同调度实例，加载系统配置
        :param orchestrator: 协同调度模块实例（需实现 BaseOrchestrator 接口）
        """
        # 基础支撑层初始化
        self.utils = CommonUtils()
        self.logger = SystemLogger()
        self.config = ConfigManager()
        self.config.load_config()
        self.exception_handler = ExceptionHandler()

        # 核心依赖模块注入
        self.orchestrator = orchestrator

        # 读取系统处理核心配置
        self.default_type = self.config.get_config(
            "request_response.default_type",
            "rag"
        )
        self.enable_trace = self.config.get_config(
            "request_response.enable_trace",
            True
        )

        self.logger.info("请求响应处理模块初始化完成，加载系统默认配置")

    def validate_request(self, request: Dict[str, Any]) -> Tuple[bool, str, str]:
        """
        校验业务请求参数
        PARAM_MISSING：缺字段或空值
        PARAM_INVALID：类型/范围错误
        BAD_REQUEST：type 不支持或整体结构非法
        REQUEST_TOO_LARGE：请求体估算大小超过限制
        返回：
        - is_valid: 是否通过
        - error_message: 错误描述
        - error_code: 系统统一错误码
        规则：
        1. 检查 request 是否为 dict
        2. 检查请求大小是否超过 max_request_size
        3. 检查 type 是否属于 rag / agent / hybrid
        4. 检查 rag 模式下 query 是否存在且为非空字符串
        5. 检查 agent / hybrid 模式下 task 是否存在且为非空字符串
        6. 检查 top_k 是否为 1~50 的整数
        """
        pass
    
    def handle(self, request: Dict[str, Any], trace_id: Optional[str] = None) -> Dict[str, Any]:
        """
        处理请求全流程
    
        处理顺序：
        1. 解析 trace_id：优先使用入参，其次 request['trace_id']，最后兜底生成
        2. 调用 validate_request() 进行业务语义校验
        3. 校验失败时，按错误码返回统一失败响应
        4. 请求标准化：补默认 type / top_k / extra_params；仅对 agent/hybrid 缺失时补 session_id
        5. 调用 orchestrator.route()
        6. 对下游结果统一补全 trace_id / retryable / cost_time
        7. 返回统一响应
        8. 异常时调用 handle_exception()
        """
        pass

    def format_response(
        self,
        code: str,
        message: str,
        data: Any,
        trace_id: str,
        request_context: Optional[Dict[str, Any]] = None,
        cost_time: Optional[float] = None,
    ) -> Dict[str, Any]:
        """
        统一响应封装
        - SUCCESS -> retryable = False, details = None
        - 失败 -> retryable 根据错误码表计算
        - details 通过 build_error_details(code, request_context, message) 生成
        """
        pass

    def handle_exception(self, exception: Exception, trace_id: str) -> Dict[str, Any]:
        """实现抽象方法：异常处理
        异常码解析优先取标准异常对象的 code/message
        未知异常统一映射为 UNKNOWN_ERROR
        返回必须带 trace_id
        details 由标准模板生成"""
        # 逻辑定义：
        # 1. 调用异常处理模块获取标准化错误信息
        # 2. 记录异常日志（包含 trace_id）
        # 3. 构建错误响应（包含 details）
        # 4. 返回标准化错误响应字典
        pass

    def _generate_trace_id(self) -> str:
        """私有方法：生成链路追踪 ID"""
        # 逻辑定义：生成 UUID 或时间戳 + 随机数
        pass

    def _standardize_request(self, request: Dict[str, Any], trace_id: str) -> Dict[str, Any]:
        """
        标准化请求格式
        规则：
        - 默认 type = config.default_type
        - 默认 top_k = 5
        - 默认 extra_params = {}
        - trace_id 强制写入
        - 仅当 type in {'agent', 'hybrid'} 且 session_id 为空时，自动生成 session_id
        """
        pass    
```

### 6.2 工具函数（utils/tool_functions.py）
提供请求响应处理相关的辅助函数。

**函数定义基础结构：**
```python
from typing import Dict, Any, Tuple, Optional


def validate_request_params(request: Dict[str, Any]) -> Tuple[bool, str, str]:
    """
    校验业务请求参数
    :return: (是否通过, 错误信息, 错误码)
    """
    # 逻辑定义：
    # 1. 检查 type 是否合法
    # 2. 检查 rag 模式下 query 是否存在
    # 3. 检查 agent 模式下 task 是否存在
    # 4. 检查参数类型是否正确
    pass


def build_error_details(
    code: str,
    request: Optional[Dict[str, Any]] = None,
    message: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """
    构建错误详情信息，用于响应中的 details 字段
    仅作为兜底函数使用
    不是主路径
    主路径 trace_id 由应用层传入
    :param code: 错误码
    :param request: 原始请求
    :return: 错误详情字典（失败时）或 None（成功时）
    
    """
    # 逻辑定义：
    # 1. 根据错误码生成对应的 details 结构
    # 2. 包含 field、expected、actual、example、hint 等字段
    pass


def generate_trace_id() -> str:
    """
    生成链路追踪 ID
    :return: 追踪 ID 字符串
    """
    # 逻辑定义：生成 UUID 或时间戳 + 随机数
    pass
```

---

## 7. 模块调用示例

### 7.1 基础调用示例
```python
from request_response_module.core.impl import RequestHandler
from orchestrator_module.core.impl import SimpleOrchestrator

# 1. 初始化协同调度模块（假设已实现）
# orchestrator = SimpleOrchestrator(rag_runner=rag, agent_runner=agent)

# 2. 初始化请求处理器（注入调度模块）
handler = RequestHandler(orchestrator=orchestrator)

# 3. 处理 RAG 请求
rag_request = {
    "type": "rag",
    "query": "RAG 系统架构是什么？",
    "top_k": 5
}
response = handler.handle(rag_request)
print(f"响应码：{response['code']}, 追踪 ID: {response['trace_id']}")

# 4. 处理 Agent 请求
agent_request = {
    "type": "agent",
    "task": "请整理一份 RAG 系统开发计划",
    "session_id": "session_001"
}
response = handler.handle(agent_request)
```

### 7.2 异常处理示例
```python
handler = RequestHandler(orchestrator=orchestrator)

rag_request = {
    "type": "rag",
    "query": "RAG 系统架构是什么？",
    "top_k": 5,
    "trace_id": "trace_demo_001"
}
response = handler.handle(rag_request, trace_id=rag_request["trace_id"])
# 响应示例：
# {
#   "code": "PARAM_MISSING",
#   "message": "缺少必填参数：query",
#   "data": null,
#   "trace_id": "xxx",
#   "retryable": false,
#   "details": {"field": "query", "expected": "string"}
# }
```

### 7.3 与 API 服务模块集成示例
```python
# API 服务模块中调用请求响应处理模块
from fastapi import FastAPI, Request
from request_response_module.core.base import BaseRequestHandler

app = FastAPI()
handler: BaseRequestHandler = None  # 启动时注入

@app.post("/invoke")
async def invoke(req: Request):
    body = await req.json()
    trace_id = getattr(req.state, "trace_id", None)
    return handler.handle(body, trace_id=trace_id)

@app.get("/health")
def health():
    """健康检查"""
    return {"status": "ok"}
```

---

## 8. 测试规范

### 8.1 测试范围（修订）
| 测试类型 | 测试内容 |
| :--- | :--- |
| 请求校验测试 | type 合法性、query/task 条件必填、top_k 范围、请求大小限制 |
| trace 透传测试 | 应用层传入 trace_id 时是否原样透传；未传入时是否兜底生成 |
| session 规则测试 | rag 不补 session_id；agent/hybrid 自动补 session_id |
| 响应封装测试 | retryable 计算、details 模板生成、cost_time 写入 |
| 异常处理测试 | 标准异常、未知异常、下游调度异常 |
| 边界职责测试 | 本模块不决定 HTTP 状态码，不执行鉴权逻辑 |
| **全流程测试** | 请求→校验→调度→响应端到端测试。 |
| **配置切换测试** | 默认 type 切换、trace 开关切换、校验规则切换。 |

### 8.2 测试用例基础框架
位于 `tests/test_impl.py`。
```python
import unittest
from request_response_module.core.impl import RequestHandler
from orchestrator_module.core.impl import SimpleOrchestrator


class MockOrchestrator:
    """模拟协同调度模块，用于测试"""
    def route(self, request):
        if request.get("type") == "rag":
            return {"code": "SUCCESS", "message": "ok", "data": {"answer": "mock"}}
        raise Exception("调度异常")


class TestRequestHandler(unittest.TestCase):
    """请求响应处理模块单元测试类"""

    def setUp(self):
        """测试前置：初始化处理器实例、Mock 调度模块"""
        self.orchestrator = MockOrchestrator()
        self.handler = RequestHandler(orchestrator=self.orchestrator)

    def test_validate_rag_request(self):
        """测试 RAG 请求校验"""
        request = {"type": "rag", "query": "test", "top_k": 5}
        is_valid, error_msg = self.handler.validate_request(request)
        self.assertTrue(is_valid)

    def test_validate_missing_query(self):
        """测试缺少 query 参数校验"""
        request = {"type": "rag"}
        is_valid, error_msg = self.handler.validate_request(request)
        self.assertFalse(is_valid)
        self.assertIn("query", error_msg)

    def test_handle_success(self):
        """测试成功请求处理"""
        request = {"type": "rag", "query": "test"}
        response = self.handler.handle(request)
        self.assertEqual(response["code"], "SUCCESS")
        self.assertIn("trace_id", response)

    def test_handle_exception(self):
        """测试异常处理"""
        request = {"type": "agent", "task": "test"}  # Mock 会抛出异常
        response = self.handler.handle(request)
        self.assertNotEqual(response["code"], "SUCCESS")
        self.assertIn("trace_id", response)

    def test_format_response(self):
        """测试响应格式化"""
        response = self.handler.format_response(
            code="SUCCESS",
            message="ok",
            data={"test": "data"},
            trace_id="test_trace"
        )
        self.assertEqual(response["code"], "SUCCESS")
        self.assertEqual(response["retryable"], False)


if __name__ == "__main__":
    unittest.main()
```

---

## 9. 模块配置管理

### 9.1 配置项说明
位于 `config/config.py`，读取全局配置中的 `request_response` 节点。

**配置类基础结构：**
```python
from config_module.core.impl import ConfigManager


class RequestResponseConfig:
    """请求响应处理模块专属配置类"""

    def __init__(self):
        self.config_manager = ConfigManager()
        self.config_manager.load_config()

    def get_default_type(self) -> str:
        """获取默认请求类型"""
        return self.config_manager.get_config(
            "request_response.default_type", 
            "rag"
        )

    def is_trace_enabled(self) -> bool:
        """是否启用链路追踪"""
        return self.config_manager.get_config(
            "request_response.enable_trace", 
            True
        )

    def get_max_request_size(self) -> int:
        """获取最大请求大小（字节）"""
        return int(self.config_manager.get_config(
            "request_response.max_request_size", 
            1048576  # 1MB
        ))
```

### 9.2 配置文件示例（系统全局 config.yaml）
```yaml
# 请求响应处理模块配置
request_response:
  default_type: "rag"              # 默认请求类型（rag/agent/hybrid）
  default_top_k: 5
  enable_trace: true               # 是否启用链路追踪
  max_request_size: 1048576        # 最大请求大小（字节，默认 1MB）
  session_prefix: "session"
  timeout: 60                      # 请求处理超时时间（秒）
  validate_strict: true            # 是否启用严格参数校验
```

---

## 10. 交付物清单（强制）

模块开发完成后，需提交以下交付物，确保符合系统集成要求：

| 交付物 | 说明 |
| :--- | :--- |
| `core/base.py` | 抽象基类，定义处理核心接口。 |
| `core/impl.py` | 具体实现类（标准请求响应处理全流程）。 |
| `model/data_model.py` | 请求/响应标准化数据模型。 |
| `utils/tool_functions.py` | 请求校验、响应封装工具。 |
| `config/config.py` | 模块配置读取逻辑。 |
| `tests/test_impl.py` | 核心功能测试用例。 |
| `README.md` | 模块说明文档（适配初学者）。 |
| `requirements.txt` | 依赖包清单（无额外专属依赖，复用系统依赖）。 |

若模块使用额外扩展目录（如 examples/、schemas/），也必须在 README 中说明职责，并纳入测试覆盖范围。
---

## 11. 可替换性约束（强制）

| 约束项 | 说明 |
| :--- | :--- |
| 接口依赖 | 应用层仅依赖 `BaseRequestHandler`，不得依赖 `RequestHandler` 私有实现细节。 |
| HTTP 边界 | 本模块不得处理 HTTP 状态码映射、鉴权、中间件、路由。 |
| 调度注入 | 协同调度依赖必须通过构造函数注入，禁止内部硬编码实例化。 |
| trace 约束 | `trace_id` 由应用层入口生成并透传，本模块仅兜底生成。 |
| 统一格式 | 请求与响应结构必须严格遵循系统总设计 v1.1。 |
---

## 12. 常见问题（FAQ）

| 问题 | 解答 |
| :--- | :--- |
| **请求参数校验失败怎么办？** | 检查请求中是否包含必填参数（rag 模式需 query，agent 模式需 task）；检查 type 是否为 rag/agent/hybrid。 |
| 响应中 trace_id 为空？ | 先检查应用层是否已透传 trace_id；若未透传，检查本模块兜底生成逻辑。 |
| **如何处理大请求？** | 检查请求大小是否超过 `max_request_size` 配置；建议在 API 层增加请求大小限制。 |
| **异常响应格式不统一？** | 确保所有异常都通过 `handle_exception` 方法处理；检查异常处理模块是否正确集成。 |
| 如何扩展新的请求类型？ | 先更新系统总设计与统一请求格式，再修改本模块校验规则与协同调度模块路由，不得仅修改本模块代码。 |
| **如何调试请求处理流程？** | 查看日志模块中 `request_response_module` 相关日志，关注 trace_id 与请求参数。 |
| **如何禁用参数校验？** | 修改配置中 `validate_strict` 为 false（不推荐生产环境使用）。 |
| **如何处理并发请求？** | 模块本身无状态，支持并发；确保协同调度模块与底层模块支持并发调用。 |

---

## 13. 附录：系统错误码关联

本模块直接使用或透传的核心错误码如下：

| 错误码 | 来源 | 适用场景 |
| :--- | :--- | :--- |
| `SUCCESS` | 本模块/下游 | 处理成功 |
| `PARAM_MISSING` | 本模块 | 缺少 query / task 等必填字段 |
| `PARAM_INVALID` | 本模块 | top_k 类型或范围不合法 |
| `BAD_REQUEST` | 本模块 | type 非 rag/agent/hybrid 或请求结构不合法 |
| `ORCHESTRATOR_RUN_FAILED` | 下游透传 | 协同调度执行失败 |
| `AGENT_TIMEOUT` | 下游透传 | Agent 执行超时 |
| `RAG_RUN_FAILED` | 下游透传 | RAG 执行失败 |
| `UNKNOWN_ERROR` | 异常兜底 | 未知运行时异常 |

---

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
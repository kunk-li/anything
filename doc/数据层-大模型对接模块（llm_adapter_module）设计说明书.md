# 数据层-大模型对接模块（llm_adapter_module）设计说明书

| 文档版本 | v1.1 |
| :--- | :--- |
| 最后更新 | 2026-03-19 |
| 维护责任人 | 大模型对接模块开发负责人 |
| 状态 | 修订版 |

> 本修订版对齐《RAG与Agent系统架构设计说明书》v1.1、RAG 模块修订版、Embedding 模块修订版、接口层与应用层修订版，重点修正统一请求/响应模型、Chat / Embedding / Multimodal 能力分层、provider 抽象边界、trace_id 透传、配置治理与统一错误码。

# 1. 文档概述

## 1.1 文档目的

本文档为 RAG 与 Agent 系统数据层-大模型对接模块（`llm_adapter_module`）的独立设计说明书。

本模块负责系统中的大模型统一接入能力，是“请求封装 -> provider 选择 -> 模型调用 -> 结果标准化返回”的核心数据层模块。模块在系统中的职责包括：

- 提供统一的 Chat、Embedding、Multimodal 调用入口；
- 屏蔽不同大模型提供方（本地模型、OpenAI、兼容 API 等）的差异；
- 将业务层请求转换为统一的模型调用请求结构；
- 将模型返回结果转换为系统标准结构；
- 对错误、超时、配置缺失等场景做标准化封装；
- 为核心业务层提供稳定、可替换的大模型服务抽象。

本文档作为本模块开发、测试、联调与后续替换实现的唯一标准依据。

## 1.2 适用人群

适用于大模型对接模块开发人员、RAG 模块开发人员、Embedding 模块开发人员、Agent 模块开发人员、测试人员、架构设计人员及后续维护人员。

## 1.3 核心需求回顾

| 需求类型 | 具体要求 |
| :--- | :--- |
| 模块功能 | 提供统一的 Chat / Embedding / Multimodal 模型接入能力。 |
| 开发语言 | Python 3.10+，最低 3.10，推荐 3.12，与系统整体保持一致。 |
| 开发模式 | 独立开发、可替换实现、通过抽象接口集成。 |
| 文档要求 | 与系统总设计 v1.1、RAG / Embedding / Agent / 接口层设计保持一致。 |
| 模块约束 | 本模块不负责 HTTP 协议处理；不重新生成新的业务 trace_id；必须通过统一请求/响应结构封装不同 provider；不得把 provider 细节泄露给上层模块。 |

# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于系统**数据层**，是系统中负责大模型统一接入的数据能力模块。

本模块职责如下：

- 接收来自业务层的标准模型请求；
- 根据能力类型选择对应 adapter/provider；
- 调用底层大模型服务；
- 将返回结果封装为统一结构；
- 对 provider 错误、配置缺失、超时等进行标准化处理；
- 为上层模块提供稳定、可替换的大模型调用接口。

本模块不负责：

- 不负责 HTTP/HTTPS 协议处理；
- 不负责业务语义校验；
- 不负责 Prompt 业务编排；
- 不负责索引构建；
- 不负责向量数据库操作；
- 不直接决定 HTTP 状态码；
- 不重新生成新的业务 `trace_id`。

## 2.2 模块边界

### 2.2.1 本模块负责

- 统一模型请求结构定义；
- provider / adapter 选择；
- chat completion 调用；
- embedding 调用；
- multimodal 调用；
- 标准结果封装；
- 配置读取与模型默认值管理。

### 2.2.2 本模块不负责

- 不负责上层任务规划；
- 不负责 RAG 检索与 citations；
- 不负责 Agent 工具编排；
- 不负责文档上传、索引、路由、中间件、鉴权；
- 不直接暴露应用层 HTTP 接口。

## 2.3 依赖关系

### 2.3.1 上游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `rag_module` | 用于回答生成 |
| `embedding_module` | 用于向量生成 |
| `agent_module` | 用于总结、推理、工具补充能力 |
| 其他业务模块 | 需要调用大模型能力时复用 |

### 2.3.2 下游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| OpenAI / 兼容 API / 本地模型服务 | 实际模型调用提供方 |

### 2.3.3 基础依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `config_module` | 模块配置读取 |
| `log_module` | 调用日志记录 |
| `exception_module` | 异常封装 |
| `common_utils_module` | 通用辅助函数 |

说明：

- 本模块必须通过抽象 adapter/provider 屏蔽底层模型调用差异；
- 上层模块不得依赖 provider 原生 SDK；
- 默认实现由 bootstrap 或配置注入，不得在业务层硬编码厂商调用。

# 3. 统一项目结构规范

本模块遵循系统总设计 v1.1 的统一目录规范。

## 3.1 必选目录与文件

```text
llm_adapter_module/
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

- `providers/`：不同 provider 适配实现
- `examples/`：调用示例
- `docs/`：补充说明材料

说明：

- 当前阶段可采用 `core/impl.py` 单文件实现；
- 当 provider 数量增多时，建议拆分 `providers/`；
- 新增扩展目录必须在 `README.md` 中说明职责与边界。

# 4. 核心数据模型设计

## 4.1 LLMRequest（统一请求模型）

```python
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class LLMRequest:
    mode: str  # chat / embedding / multimodal
    model_name: Optional[str] = None
    input_text: Optional[str] = None
    input_texts: Optional[List[str]] = None
    messages: Optional[List[Dict[str, Any]]] = None
    file_content: Optional[Dict[str, Any]] = None
    media_content: Optional[List[Dict[str, Any]]] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    trace_id: Optional[str] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)
```

说明：

- `mode` 是统一能力入口标识；
- `chat` 主路径优先使用 `messages`，兼容 `input_text`；
- `embedding` 主路径优先使用 `input_texts`，兼容单条文本；
- `multimodal` 可结合 `input_text`、`file_content`、`media_content`；
- `trace_id` 由上游透传；
- 扩展参数统一通过 `extra_params` 承载。

## 4.2 LLMResponse（统一响应模型）

```python
from dataclasses import dataclass
from typing import Any, Dict, Optional

@dataclass
class LLMResponse:
    code: str
    message: str
    data: Optional[Dict[str, Any]] = None
    trace_id: str = ""
    retryable: bool = False
    details: Optional[Dict[str, Any]] = None
```

说明：

- 所有成功与失败响应都必须返回 `trace_id`；
- `data` 中的具体结构随 mode 不同而不同，但外层统一。

## 4.3 ChatResponseData

```python
from dataclasses import dataclass
from typing import Optional, Dict, Any

@dataclass
class ChatResponseData:
    content: str
    model_name: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
```

## 4.4 EmbeddingResponseData

```python
from dataclasses import dataclass, field
from typing import List, Dict, Any, Optional

@dataclass
class EmbeddingResponseData:
    items: List[Dict[str, Any]] = field(default_factory=list)
    model_name: Optional[str] = None
    count: int = 0
```

## 4.5 MultimodalResponseData

```python
from dataclasses import dataclass
from typing import Optional, Dict, Any

@dataclass
class MultimodalResponseData:
    content: str
    model_name: Optional[str] = None
    usage: Optional[Dict[str, Any]] = None
```

# 5. 核心接口设计（抽象基类）

## 5.1 BaseLLMAdapter

```python
from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseLLMAdapter(ABC):
    @abstractmethod
    def call(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行统一模型调用
        """
        pass
```

## 5.2 BaseLLMService

```python
from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseLLMService(ABC):
    @abstractmethod
    def call(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        统一入口：根据 mode 分发到对应 adapter
        """
        pass

    @abstractmethod
    def register_adapter(self, mode: str, adapter: Any) -> None:
        """
        注册 adapter
        """
        pass
```

约束：

- adapter 负责 provider 级调用；
- service 负责统一入口与 adapter 分发；
- 上层业务模块优先依赖 `BaseLLMService` 抽象接口。

# 6. 核心实现设计（标准 LLMService 与 Adapter）

## 6.1 类职责说明

标准 `LLMService` 实现负责：

- 接收统一模型请求；
- 根据 `mode` 选择 adapter；
- 处理默认模型配置；
- 透传 `trace_id`；
- 返回标准化结果。

标准 adapter 实现负责：

- 将统一请求映射到具体 provider；
- 调用底层 SDK 或服务；
- 将 provider 原始结果转换为标准结构；
- 不把 provider 特有字段泄露为上层强依赖。

## 6.2 mode 规则（强制）

当前支持以下 mode：

- `chat`
- `embedding`
- `multimodal`

约束：

- `chat`、`embedding`、`multimodal` 是统一标准枚举；
- 不允许业务层直接依赖 provider 自定义 mode 名；
- 若新增 mode，必须先更新总设计与本模块设计文档。

## 6.3 LLMService.call() 处理顺序（强制）

```text
1. 读取 request.mode / model_name / trace_id / extra_params
2. 校验 mode 是否存在并已注册 adapter
3. 解析有效 model_name（请求级优先于默认配置）
4. 调用对应 adapter.call()
5. 对结果补齐 trace_id / retryable / 统一响应外壳
6. 返回统一响应
7. 异常时返回标准错误响应
```

## 6.4 ChatAdapter 规则

### 6.4.1 输入要求

优先支持：

- `messages`：标准多轮消息结构

兼容支持：

- `input_text`：由 adapter 内部转换为单轮消息列表

约束：

- 不允许 `messages` 与 `input_text` 逻辑互相冲突；
- 若两者都存在，优先以 `messages` 为准，`input_text` 仅作兼容。

### 6.4.2 输出要求

成功响应 `data` 至少包含：

- `content`
- `model_name`

推荐补充：

- `usage`
- `finish_reason`

### 6.4.3 温度与 token 规则

- `temperature` 与 `max_tokens` 由请求级优先于默认配置；
- 若 provider 不支持某参数，应在 adapter 内吸收差异，而不是抛给上层模块处理。

## 6.5 EmbeddingAdapter 规则

### 6.5.1 输入要求

主路径优先支持：

- `input_texts`

兼容支持：

- `input_text` -> 自动包装为单元素列表

### 6.5.2 输出要求

成功响应 `data` 至少包含：

- `items`
- `model_name`
- `count`

每个 item 推荐包含：

- `text`
- `embedding`
- `dimension`

### 6.5.3 与 Embedding 模块边界

- `llm_adapter_module` 提供“统一 provider 调用能力”；
- `embedding_module` 提供“更贴近业务侧的向量化封装、归一化、维度校验、请求级参数覆盖”等能力；
- 若业务层直接需要向量化，优先调用 `embedding_module` 而不是直接绕过到 provider adapter。

## 6.6 MultimodalAdapter 规则

### 6.6.1 输入要求

允许组合：

- `input_text`
- `file_content`
- `media_content`

说明：

- `file_content` 适用于文件摘要、文档问答等场景；
- `media_content` 适用于图片、音频等多模态输入；
- 具体支持能力取决于 provider，但接口层统一结构不变。

### 6.6.2 输出要求

成功响应 `data` 至少包含：

- `content`
- `model_name`

推荐补充：

- `usage`

## 6.7 provider 抽象边界（修订重点）

### 6.7.1 必须隔离的差异

adapter 必须屏蔽：

- provider SDK 名称差异
- 参数命名差异
- 响应字段差异
- 异常类型差异
- 模型命名细节

### 6.7.2 上层不可见内容

业务层不应依赖：

- OpenAI SDK 原始对象
- provider 原生异常对象
- provider 原生 messages / responses 结构细节

### 6.7.3 adapter 注册建议

建议采用：

```python
service.register_adapter("chat", chat_adapter)
service.register_adapter("embedding", embedding_adapter)
service.register_adapter("multimodal", multimodal_adapter)
```

## 6.8 默认模型与请求级覆盖规则（强制）

### 6.8.1 默认模型

配置中应分别定义：

- 默认 chat 模型
- 默认 embedding 模型
- 默认 multimodal 模型

### 6.8.2 请求级覆盖

优先级如下：

- `request.model_name` > `mode` 默认模型 > provider 内部默认值

约束：

- 不允许忽略请求级模型覆盖；
- 日志中建议记录最终生效模型名。

## 6.9 trace_id 规则（强制）

- `trace_id` 由应用层生成，经接口层与业务层透传至本模块；
- 本模块不得重新生成新的业务 `trace_id`；
- adapter 调用日志、异常日志、provider 请求日志（若支持）都应附带同一 `trace_id`；
- 最终响应必须保留该 `trace_id`。

## 6.10 错误处理与统一返回

### 6.10.1 错误码约定

| 错误码 | 说明 |
| :--- | :--- |
| `SUCCESS` | 执行成功 |
| `CONFIG_NOT_FOUND` | 模型配置或 provider 配置缺失 |
| `CONFIG_KEY_MISSING` | 关键配置项缺失 |
| `MODEL_NOT_SUPPORTED` | 请求模型或 mode 不支持 |
| `LLM_CALL_FAILED` | 模型调用失败 |
| `LLM_TIMEOUT` | 模型调用超时 |
| `PARAM_INVALID` | 输入参数不合法 |
| `UNKNOWN_ERROR` | 未知异常兜底 |

### 6.10.2 返回约束

- 成功与失败响应都必须返回 `trace_id`
- 若失败且属于可重试错误，必须正确设置 `retryable`
- `details` 需尽量结构化，如：
  - `mode`
  - `model_name`
  - `provider`
  - `timeout`
  - `reason`

## 6.11 示例实现与生产实现分层说明（强制）

### 6.11.1 示例实现

适用于：

- 本地开发
- 联调验证
- Mock / Stub provider
- 小规模 PoC

允许简化：

- 使用单一 provider
- 使用同步调用
- 使用轻量级异常封装

### 6.11.2 生产实现

必须满足：

- 支持多 provider 切换
- 配置可管理
- 超时与重试治理清晰
- 调用日志与 trace 透明
- 更稳定的异常分类与恢复策略

# 7. 模块调用示例

## 7.1 注册 adapter 示例

```python
service.register_adapter("chat", chat_adapter)
service.register_adapter("embedding", embedding_adapter)
service.register_adapter("multimodal", multimodal_adapter)
```

## 7.2 Chat 调用示例

```python
request = {
    "mode": "chat",
    "model_name": "gpt-4.1-mini",
    "messages": [
        {"role": "user", "content": "请总结这段内容"}
    ],
    "trace_id": "trace_demo_001"
}

result = llm_service.call(request)
```

## 7.3 Embedding 调用示例

```python
request = {
    "mode": "embedding",
    "model_name": "text-embedding-3-large",
    "input_texts": ["问题一", "问题二"],
    "trace_id": "trace_demo_002"
}

result = llm_service.call(request)
```

## 7.4 Multimodal 调用示例

```python
request = {
    "mode": "multimodal",
    "model_name": "gpt-4.1",
    "input_text": "请概述这张图片的主要内容",
    "media_content": [
        {"type": "image_url", "url": "https://example.com/demo.png"}
    ],
    "trace_id": "trace_demo_003"
}

result = llm_service.call(request)
```

# 8. 测试规范

## 8.1 测试范围（强制）

| 测试类型 | 测试内容 |
| :--- | :--- |
| mode 分发测试 | `chat / embedding / multimodal` 是否正确分发到对应 adapter |
| 请求级覆盖测试 | `model_name / temperature / max_tokens` 是否覆盖默认配置 |
| 结构一致性测试 | 不同 mode 的外层响应是否保持统一结构 |
| chat 兼容测试 | `messages` 与 `input_text` 的兼容规则是否正确 |
| embedding 兼容测试 | `input_text` 是否能正确包装为单元素列表 |
| trace 透传测试 | `trace_id` 是否贯穿 service、adapter 与响应 |
| 错误处理测试 | 配置缺失、provider 调用失败、超时、非法 mode 等场景 |
| provider 隔离测试 | 上层是否无需感知 provider 原生结构 |
| 统一响应测试 | 成功与失败响应是否符合统一结构 |

## 8.2 Mock 示例

```python
class MockChatAdapter:
    def call(self, request):
        return {
            "code": "SUCCESS",
            "message": "ok",
            "data": {
                "content": "示例回答",
                "model_name": request.get("model_name")
            },
            "trace_id": request.get("trace_id"),
            "retryable": False,
            "details": None
        }
```

# 9. 模块配置管理

建议配置示例如下：

```yaml
llm_adapter:
  provider: "openai"
  default_models:
    chat: "gpt-4.1-mini"
    embedding: "text-embedding-3-large"
    multimodal: "gpt-4.1"
  timeout: 60
  api_key: "${OPENAI_API_KEY}"
  base_url: "${OPENAI_BASE_URL:https://api.openai.com/v1}"
```

说明：

- `provider` 为默认提供方；
- `default_models` 按 mode 配置；
- `timeout` 为统一调用超时；
- 敏感配置通过环境变量注入；
- `base_url` 支持兼容 OpenAI-like 服务。

# 10. 交付物清单（强制）

模块开发完成后，需提交以下交付物：

| 交付物 | 说明 |
| :--- | :--- |
| `core/base.py` | 抽象基类，定义统一模型调用核心接口 |
| `core/impl.py` | 默认 LLMService 实现 |
| `model/data_model.py` | 统一请求/响应数据模型 |
| `utils/tool_functions.py` | 请求转换、参数兼容、异常封装辅助函数 |
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
| 上游调用 | 业务层优先依赖 `BaseLLMService` 抽象接口 |
| provider 依赖 | 上层不得依赖 provider 原生 SDK 结构 |
| mode 约束 | `chat / embedding / multimodal` 为统一标准 mode |
| 参数覆盖 | 请求级模型和参数必须优先于默认配置 |
| trace 约束 | `trace_id` 由应用层生成并透传，本模块不得重新生成 |
| 统一结构 | 所有 mode 的外层响应必须保持统一结构 |

# 12. 常见问题（FAQ）

| 问题 | 说明 |
| :--- | :--- |
| 为什么 Embedding 还需要单独一个 `embedding_module`？ | 因为 `llm_adapter_module` 负责统一 provider 调用，而 `embedding_module` 负责更贴近业务的向量化封装、归一化与维度校验。 |
| 为什么不能让业务层直接调用 provider SDK？ | 因为这会让 provider 差异泄露到业务层，破坏可替换性与统一结构。 |
| `messages` 和 `input_text` 同时传入怎么办？ | 统一约定优先使用 `messages`，`input_text` 仅作兼容输入。 |
| 为什么所有模式都要统一外层响应结构？ | 因为这能减少上层模块分支处理复杂度，并与系统统一错误码和 trace_id 规则保持一致。 |

# 13. 附录：系统错误码关联

本模块直接使用或透传的核心错误码如下：

| 错误码 | 来源 | 适用场景 |
| :--- | :--- | :--- |
| `SUCCESS` | 本模块/下游 | 请求成功 |
| `CONFIG_NOT_FOUND` | 本模块 | 配置文件不存在或 provider 配置未加载 |
| `CONFIG_KEY_MISSING` | 本模块 | 关键配置项缺失 |
| `MODEL_NOT_SUPPORTED` | 本模块 | mode 或模型不支持 |
| `LLM_CALL_FAILED` | 本模块/下游 | 模型调用失败 |
| `LLM_TIMEOUT` | 本模块/下游 | 模型调用超时 |
| `PARAM_INVALID` | 本模块 | 输入参数不合法 |
| `UNKNOWN_ERROR` | 异常兜底 | 未知运行时异常 |

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
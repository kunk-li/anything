# 核心业务层-Embedding模块（embedding_module）设计说明书

| 文档版本 | v1.1 |
| :--- | :--- |
| 最后更新 | 2026-03-19 |
| 维护责任人 | Embedding模块开发负责人 |
| 状态 | 修订版 |

> 本修订版对齐《RAG与Agent系统架构设计说明书》v1.1、接口层-请求响应处理模块修订版、应用层-API服务模块修订版、协同调度模块修订版与 RAG 模块修订版，重点修正抽象依赖边界、请求级参数覆盖、trace_id 透传、向量维度校验、归一化规则与统一响应结构。

# 1. 文档概述

## 1.1 文档目的

本文档为 RAG 与 Agent 系统核心业务层-Embedding模块（`embedding_module`）的独立设计说明书。

本模块负责系统中的文本嵌入能力，是“文本清洗 -> 向量生成 -> 向量校验 -> 归一化 -> 统一返回”的核心执行模块。模块在系统中的职责包括：

- 接收标准化 Embedding 请求；
- 对输入文本执行必要的清洗和规范化；
- 调用底层嵌入模型或 LLM 向量接口生成向量；
- 校验向量维度与格式；
- 按配置执行向量归一化；
- 返回系统统一响应结构；
- 作为独立能力供 RAG 模块、索引构建链路及其他业务模块调用。

本文档作为本模块开发、测试、联调与后续替换实现的唯一标准依据。

## 1.2 适用人群

适用于 Embedding 模块开发人员、RAG 模块开发人员、索引链路开发人员、测试人员、架构设计人员及后续维护人员。

## 1.3 核心需求回顾

| 需求类型 | 具体要求 |
| :--- | :--- |
| 模块功能 | 提供单文本与批量文本嵌入能力，输出可直接用于向量检索的向量结果。 |
| 开发语言 | Python 3.10+，最低 3.10，推荐 3.12，与系统整体保持一致。 |
| 开发模式 | 独立开发、可替换实现、通过抽象接口集成。 |
| 文档要求 | 与系统总设计 v1.1 及 RAG / 数据层 / 接口层子设计保持一致。 |
| 模块约束 | 本模块不负责 HTTP 协议处理；不重新生成新的业务 trace_id；必须支持维度校验、可选归一化、请求级参数覆盖与统一响应结构。 |

# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于系统**核心业务层**，是系统中负责文本向量化能力的核心模块。

本模块职责如下：

- 接收标准化文本嵌入请求；
- 执行输入文本的清洗与预处理；
- 调用底层嵌入模型生成向量；
- 对向量进行维度校验、数值合法性检查与可选归一化；
- 返回统一响应结构；
- 为 RAG 检索、索引构建、文档批量入库等能力提供统一 Embedding 接口。

本模块不负责：

- 不负责 HTTP/HTTPS 协议处理；
- 不负责业务语义级参数校验（由接口层负责）；
- 不负责向量数据库写入；
- 不负责文档解析与 chunking；
- 不重新生成新的业务 `trace_id`；
- 不直接管理模型服务生命周期之外的应用层行为。

## 2.2 模块边界

### 2.2.1 本模块负责

- 文本清洗与预处理；
- 单条或批量文本向量生成；
- 向量维度校验；
- 可选归一化；
- 错误封装与统一响应。

### 2.2.2 本模块不负责

- 不负责向量持久化写入；
- 不负责 query 改写、检索、重排、回答生成；
- 不负责索引编排；
- 不负责 HTTP 状态码映射；
- 不直接依赖应用层对象。

## 2.3 依赖关系

### 2.3.1 上游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `rag_module` | 用于 query 向量化 |
| `index_service / 索引链路` | 用于 chunk 批量向量化 |
| 其他核心模块 | 需要语义向量能力时复用 |

### 2.3.2 下游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `llm_adapter_module`（可选） | 通过统一 LLM 接口生成向量 |
| 本地模型依赖（如 sentence-transformers） | 本地嵌入模型执行 |
| `common_utils_module` | 文本清洗等辅助能力 |

### 2.3.3 基础依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `config_module` | 模块配置读取 |
| `log_module` | 运行日志记录 |
| `exception_module` | 异常封装 |
| `common_utils_module` | 通用辅助函数 |

说明：

- 本模块优先依赖抽象接口，如 `BaseLLMService` 或等价嵌入服务抽象；
- 具体实现（本地模型 / OpenAI / 其他向量服务）由 bootstrap 注入或配置选择；
- 不在模块内部硬编码固定厂商或固定模型实现作为唯一主路径。

# 3. 统一项目结构规范

本模块遵循系统总设计 v1.1 的统一目录规范。

## 3.1 必选目录与文件

```text
embedding_module/
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

- `providers/`：不同嵌入提供方实现
- `examples/`：调用示例
- `docs/`：补充说明材料

说明：

- 当前阶段可采用 `core/impl.py` 单文件实现；
- 当 provider 类型增多时，建议拆分 `providers/`；
- 新增扩展目录必须在 `README.md` 中说明职责与边界。

# 4. 核心数据模型设计

## 4.1 EmbeddingRequest

```python
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

@dataclass
class EmbeddingRequest:
    texts: List[str]
    model_name: Optional[str] = None
    normalize: bool = True
    batch_size: int = 32
    trace_id: Optional[str] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)
```

说明：

- 统一使用 `texts` 作为输入字段，单文本场景也可传长度为 1 的列表；
- `model_name` 为请求级模型覆盖参数；
- `normalize` 表示是否对返回向量做归一化；
- `batch_size` 可由请求级覆盖；
- `trace_id` 由应用层生成并向下透传。

## 4.2 EmbeddingItem

```python
from dataclasses import dataclass
from typing import List, Optional

@dataclass
class EmbeddingItem:
    text: str
    embedding: List[float]
    dimension: int
    model_name: Optional[str] = None
```

## 4.3 EmbeddingResponse

```python
from dataclasses import dataclass
from typing import Any, Dict, Optional

@dataclass
class EmbeddingResponse:
    code: str
    message: str
    data: Optional[Dict[str, Any]] = None
    trace_id: str = ""
    retryable: bool = False
    details: Optional[Dict[str, Any]] = None
```

建议 `data` 中至少包含：

```json
{
  "items": [
    {
      "text": "示例文本",
      "embedding": [0.12, 0.34],
      "dimension": 2,
      "model_name": "demo-model"
    }
  ],
  "model_name": "demo-model",
  "count": 1,
  "normalized": true
}
```

# 5. 核心接口设计（抽象基类）

## 5.1 BaseEmbedding

```python
from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseEmbedding(ABC):
    @abstractmethod
    def embed_text(self, text: str, **kwargs) -> Dict[str, Any]:
        """
        对单条文本生成向量
        """
        pass

    @abstractmethod
    def embed_texts(self, texts: list[str], **kwargs) -> Dict[str, Any]:
        """
        对多条文本批量生成向量
        """
        pass
```

约束：

- `embed_text()` 与 `embed_texts()` 都必须返回统一响应结构或其兼容结构；
- 批量接口应为主路径，单条接口可作为兼容封装；
- 不负责做 HTTP、向量入库或检索逻辑。

# 6. 核心实现设计（标准 Embedding 实现）

## 6.1 类职责说明

标准 Embedding 实现负责：

- 接收文本或文本列表；
- 清洗并预处理输入；
- 调用实际嵌入提供方；
- 校验返回向量维度与数值合法性；
- 执行可选归一化；
- 返回统一响应结构。

本实现必须保持：

- 无 HTTP 依赖；
- 可并发复用；
- `trace_id` 全流程透传；
- 支持请求级参数覆盖模块默认配置；
- 支持不同 provider 的可替换实现。

## 6.2 构造函数建议

```python
class StandardEmbedding(BaseEmbedding):
    def __init__(
        self,
        provider = None,
        default_model_name: str | None = None,
        default_batch_size: int = 32,
        default_normalize: bool = True,
        expected_dimension: int | None = None,
    ):
        self.provider = provider
        self.default_model_name = default_model_name
        self.default_batch_size = default_batch_size
        self.default_normalize = default_normalize
        self.expected_dimension = expected_dimension
```

说明：

- `provider` 为实际嵌入执行器，本地模型或远程服务都可；
- 不在构造函数中硬编码唯一提供方；
- `expected_dimension` 用于运行时校验；
- 请求级参数可覆盖默认配置。

## 6.3 embed_texts() 处理顺序（强制）

```text
1. 读取 texts / model_name / normalize / batch_size / trace_id
2. 计算有效参数（请求级优先于模块默认值）
3. 清洗输入文本列表
4. 调用 provider 批量生成向量
5. 校验每条向量的维度与数值合法性
6. 按需要做归一化
7. 返回统一响应
8. 异常时返回标准错误响应
```

## 6.4 请求级参数覆盖规则（修订重点）

请求级参数优先级必须高于模块默认值：

- `model_name`: `request.model_name` > `self.default_model_name`
- `batch_size`: `request.batch_size` > `self.default_batch_size`
- `normalize`: `request.normalize` > `self.default_normalize`

说明：

- 若请求未显式提供，则使用模块默认配置；
- 不允许忽略请求级覆盖配置；
- 运行日志中建议记录本次执行的有效参数。

## 6.5 文本清洗规则

### 6.5.1 基础清洗

至少执行：

- 去除首尾空白；
- 统一换行；
- 将空字符串过滤或返回参数错误；
- 将 `None`、非字符串输入视为非法输入。

### 6.5.2 长文本处理

- 本模块不负责 chunking；
- 若单条文本过长，可记录告警并交由 provider 侧裁剪或由上游预先切分；
- 不建议在本模块内静默截断为业务主路径。

## 6.6 provider 调用规则

### 6.6.1 本地模型 provider

适用于：

- sentence-transformers
- 其他本地嵌入模型

要求：

- 批量调用优先；
- 模型初始化失败时返回 `EMBEDDING_INIT_FAILED`；
- 返回向量必须为数值列表。

### 6.6.2 LLM / 远程服务 provider

适用于：

- OpenAI embeddings
- 其他兼容嵌入 API

要求：

- 配置缺失时返回 `EMBEDDING_CONFIG_MISSING`
- 远程调用失败时返回 `EMBEDDING_CALL_FAILED`
- 支持 `trace_id` 透传到日志与请求上下文（若接口支持）

## 6.7 向量维度校验（强制）

### 6.7.1 校验规则

每条向量都必须校验：

- 是否为列表或等价数值序列；
- 长度是否大于 0；
- 若配置了 `expected_dimension`，则长度必须严格一致；
- 元素必须为数值类型；
- 不允许出现 NaN / Inf。

### 6.7.2 不合法处理

若任一向量不合法，应返回结构化错误，例如：

```json
{
  "code": "EMBEDDING_DIMENSION_INVALID",
  "message": "向量维度不匹配",
  "details": {
    "expected_dimension": 1024,
    "actual_dimension": 768,
    "index": 0
  }
}
```

## 6.8 归一化规则（强制）

### 6.8.1 默认行为

- 默认建议开启归一化；
- 是否归一化以请求级配置优先；
- 归一化用于将向量适配余弦相似度检索等场景。

### 6.8.2 归一化要求

- 对每条向量独立归一化；
- 零向量必须特殊处理，不能直接除零；
- 若向量为零向量，应返回明确错误或在文档中定义兼容策略。

### 6.8.3 命名一致性约束

实现中必须统一使用同一个归一化工具函数命名，例如：

- `normalize_vector`：单条向量
- `normalize_vectors`：批量向量

不得出现接口文档、实现导入与调用命名不一致的情况。

## 6.9 trace_id 规则（强制）

- `trace_id` 由应用层生成，经接口层和业务层透传到本模块；
- 本模块不得重新生成新的业务 `trace_id`；
- provider 调用日志、异常日志、维度校验日志都应附带同一 `trace_id`；
- 最终响应必须保留该 `trace_id`。

## 6.10 返回结构要求

成功响应建议结构：

```json
{
  "code": "SUCCESS",
  "message": "ok",
  "data": {
    "items": [
      {
        "text": "示例文本",
        "embedding": [0.1, 0.2, 0.3],
        "dimension": 3,
        "model_name": "demo-model"
      }
    ],
    "model_name": "demo-model",
    "count": 1,
    "normalized": true
  },
  "trace_id": "trace_demo_001",
  "retryable": false,
  "details": null
}
```

失败响应需保持系统统一结构，并尽量提供结构化 details。

## 6.11 错误处理与统一返回

### 6.11.1 错误码约定

| 错误码 | 说明 |
| :--- | :--- |
| `SUCCESS` | 执行成功 |
| `EMBEDDING_CONFIG_MISSING` | embedding 配置缺失 |
| `EMBEDDING_INIT_FAILED` | embedding 初始化失败 |
| `EMBEDDING_CALL_FAILED` | embedding 调用失败 |
| `EMBEDDING_DIMENSION_INVALID` | 向量维度不合法 |
| `PARAM_INVALID` | 输入文本或参数不合法 |
| `UNKNOWN_ERROR` | 未知异常兜底 |

### 6.11.2 返回约束

- 所有成功与失败响应都必须返回 `trace_id`
- 若失败且属于可重试错误，必须正确设置 `retryable`
- `details` 需尽量结构化，如 `model_name`、`expected_dimension`、`index`、`batch_size` 等

# 7. 模块调用示例

## 7.1 基础组装示例

```python
from bootstrap import build_embedding_provider
from embedding_module.core.impl import StandardEmbedding

provider = build_embedding_provider()

embedding = StandardEmbedding(
    provider=provider,
    default_model_name="text-embedding-3-large",
    default_batch_size=32,
    default_normalize=True,
    expected_dimension=3072
)
```

## 7.2 单文本调用示例

```python
result = embedding.embed_text(
    "RAG 系统架构是什么？",
    trace_id="trace_demo_001"
)
```

## 7.3 批量调用示例

```python
result = embedding.embed_texts(
    texts=["问题一", "问题二"],
    model_name="text-embedding-3-small",
    normalize=True,
    batch_size=16,
    trace_id="trace_demo_002"
)
```

# 8. 测试规范

## 8.1 测试范围（强制）

| 测试类型 | 测试内容 |
| :--- | :--- |
| 单条嵌入测试 | `embed_text()` 是否返回正确结构 |
| 批量嵌入测试 | `embed_texts()` 是否支持批量调用与批量结果 |
| 参数覆盖测试 | 请求级 `model_name / batch_size / normalize` 是否覆盖默认配置 |
| 维度校验测试 | 向量维度不匹配时是否返回结构化错误 |
| 归一化测试 | 是否正确归一化；零向量是否正确处理 |
| trace 透传测试 | `trace_id` 是否贯穿 provider 调用与最终响应 |
| 错误处理测试 | 配置缺失、初始化失败、调用失败、非法输入等场景 |
| 命名一致性测试 | 归一化工具函数命名与调用是否一致 |
| 统一响应测试 | 返回值是否符合统一响应结构 |

## 8.2 Mock 示例

```python
class MockProvider:
    def embed_texts(self, texts, model_name=None, batch_size=32):
        return [[0.1, 0.2, 0.3] for _ in texts]
```

# 9. 模块配置管理

建议配置示例如下：

```yaml
embedding:
  model_name: "text-embedding-3-large"
  batch_size: 32
  normalize: true
  expected_dimension: 3072
  provider_type: "remote"
```

说明：

- `model_name` 为默认模型；
- `batch_size` 为默认批量大小；
- `normalize` 为默认归一化开关；
- `expected_dimension` 用于维度校验；
- `provider_type` 用于区分本地 / 远程实现。

# 10. 交付物清单（强制）

模块开发完成后，需提交以下交付物：

| 交付物 | 说明 |
| :--- | :--- |
| `core/base.py` | 抽象基类，定义 Embedding 核心接口 |
| `core/impl.py` | 默认 Embedding 实现 |
| `model/data_model.py` | Embedding 数据模型 |
| `utils/tool_functions.py` | 文本清洗、维度校验、归一化辅助函数 |
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
| 上游调用 | RAG 模块或索引链路只能依赖 `BaseEmbedding` 抽象接口 |
| provider 依赖 | 本模块不得硬编码唯一 provider 实现 |
| 参数覆盖 | 请求级参数必须优先于默认配置 |
| 维度约束 | 向量返回必须经过维度与数值合法性校验 |
| trace 约束 | `trace_id` 由应用层生成并向下透传，本模块不得重新生成 |
| 统一结构 | 请求与响应结构必须严格遵循系统总设计 v1.1 |

# 12. 常见问题（FAQ）

| 问题 | 说明 |
| :--- | :--- |
| Embedding 模块是否负责向量入库？ | 不负责。本模块只负责生成与校验向量，入库由索引链路或数据层负责。 |
| 为什么必须做维度校验？ | 因为向量维度不一致会直接导致检索链路失败或结果异常。 |
| 归一化是否必须开启？ | 默认建议开启，但是否启用以请求级配置优先。 |
| 为什么不能在本模块里静默截断长文本？ | 因为 chunking 应由上游索引链路负责，静默截断会影响检索质量与可解释性。 |

# 13. 附录：系统错误码关联

本模块直接使用或透传的核心错误码如下：

| 错误码 | 来源 | 适用场景 |
| :--- | :--- | :--- |
| `SUCCESS` | 本模块/下游 | 请求成功 |
| `EMBEDDING_CONFIG_MISSING` | 本模块/下游 | embedding 配置缺失 |
| `EMBEDDING_INIT_FAILED` | 本模块/下游 | embedding 初始化失败 |
| `EMBEDDING_CALL_FAILED` | 本模块/下游 | embedding 调用失败 |
| `EMBEDDING_DIMENSION_INVALID` | 本模块 | 向量维度不合法 |
| `PARAM_INVALID` | 本模块 | 输入文本或参数不合法 |
| `UNKNOWN_ERROR` | 异常兜底 | 未知运行时异常 |

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
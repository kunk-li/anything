# 数据层-向量数据库模块（vector_db_module）设计说明书

| 文档版本 | v1.1 |
| :--- | :--- |
| 最后更新 | 2026-03-19 |
| 维护责任人 | 向量数据库模块开发负责人 |
| 状态 | 修订版 |

> 本修订版对齐《RAG与Agent系统架构设计说明书》v1.1、RAG 模块修订版、Embedding 模块修订版与索引链路规范，重点修正 vector_id 策略、chunk 级 metadata 约束、查询返回结构、delete 能力边界、示例实现与生产实现分层及统一错误码。

# 1. 文档概述

## 1.1 文档目的

本文档为 RAG 与 Agent 系统数据层-向量数据库模块（`vector_db_module`）的独立设计说明书。

本模块负责系统中的向量存储与相似度检索能力，是“向量写入 -> 向量查询 -> 元数据过滤 -> 持久化管理 -> 可选删除”的核心数据层模块。模块在系统中的职责包括：

- 接收来自索引链路或业务模块的向量写入请求；
- 存储 chunk 级向量及 metadata；
- 提供相似度检索与条件过滤能力；
- 返回可供 RAG 模块直接消费的 chunk 级检索结果；
- 提供索引加载、持久化、可选删除等能力；
- 作为示例实现或生产实现的统一抽象层。

本文档作为本模块开发、测试、联调与后续替换实现的唯一标准依据。

## 1.2 适用人群

适用于向量数据库模块开发人员、RAG 模块开发人员、Embedding 模块开发人员、索引链路开发人员、测试人员、架构设计人员及后续维护人员。

## 1.3 核心需求回顾

| 需求类型 | 具体要求 |
| :--- | :--- |
| 模块功能 | 提供 chunk 级向量写入、查询、过滤、持久化与可选删除能力。 |
| 开发语言 | Python 3.10+，最低 3.10，推荐 3.12，与系统整体保持一致。 |
| 开发模式 | 独立开发、可替换实现、通过抽象接口集成。 |
| 文档要求 | 与系统总设计 v1.1、RAG / Embedding / 索引链路相关设计保持一致。 |
| 模块约束 | 本模块不负责 HTTP 协议处理；必须以 chunk 为最小索引单元；metadata 字段必须满足系统统一规范；示例实现与生产实现能力边界必须明确。 |

# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于系统**数据层**，是系统中负责向量索引与相似度检索的数据能力模块。

本模块职责如下：

- 接收来自索引链路的向量写入请求；
- 维护向量索引及对应 metadata；
- 提供相似度查询与 metadata 过滤；
- 将检索结果以 chunk 级结构返回；
- 支持索引加载、持久化与可选删除；
- 作为底层能力供 RAG 模块、Agent 工具和其他检索型模块调用。

本模块不负责：

- 不负责 HTTP/HTTPS 协议处理；
- 不负责统一业务参数校验；
- 不负责文本 embedding 生成；
- 不负责文档解析与 chunking；
- 不负责生成回答或拼接 Prompt；
- 不直接决定 HTTP 状态码。

## 2.2 模块边界

### 2.2.1 本模块负责

- 向量写入（upsert）
- 向量查询（query）
- metadata 过滤
- 索引持久化与加载
- 可选删除（生产实现）
- 索引基本一致性校验

### 2.2.2 本模块不负责

- 不负责 embedding 生成；
- 不负责 chunk 生成；
- 不负责整篇文档回填为上下文主路径；
- 不负责模型调用；
- 不负责应用层鉴权、中间件、路由、Trace 生成。

## 2.3 依赖关系

### 2.3.1 上游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `embedding_module` | 生成待写入的向量 |
| `index_service / 索引链路` | 触发向量写入与重建 |
| `rag_module` | 查询向量检索结果 |

### 2.3.2 下游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| FAISS / Chroma / Milvus / PGVector 等 | 实际向量索引实现载体 |

### 2.3.3 基础依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `config_module` | 模块配置读取 |
| `log_module` | 索引与检索日志记录 |
| `exception_module` | 异常封装 |
| `common_utils_module` | 通用辅助函数 |

说明：

- 本模块必须提供统一抽象接口，屏蔽具体向量数据库差异；
- 示例实现可使用本地 FAISS；
- 生产实现可替换为 Milvus / PGVector / Chroma 等支持删除与扩展的方案。

# 3. 统一项目结构规范

本模块遵循系统总设计 v1.1 的统一目录规范。

## 3.1 必选目录与文件

```text
vector_db_module/
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

- `providers/`：不同向量数据库后端实现
- `examples/`：调用示例
- `docs/`：补充说明材料

说明：

- 当前阶段可采用 `core/impl.py` 单文件实现；
- 当 provider 类型增多时，建议拆分 `providers/`；
- 新增扩展目录必须在 `README.md` 中说明职责与边界。

# 4. 核心数据模型设计

## 4.1 VectorRecord

```python
from dataclasses import dataclass, field
from typing import Dict, List, Any

@dataclass
class VectorRecord:
    vector_id: str
    embedding: List[float]
    metadata: Dict[str, Any] = field(default_factory=dict)
```

说明：

- `vector_id` 推荐等于 `chunk_id`；
- `embedding` 为数值向量；
- `metadata` 至少包含系统规定的 chunk 级字段。

## 4.2 QueryResultItem

```python
from dataclasses import dataclass
from typing import Optional, Dict, Any

@dataclass
class QueryResultItem:
    vector_id: str
    score: float
    metadata: Dict[str, Any]
    content: Optional[str] = None
```

说明：

- `vector_id` 推荐与 `chunk_id` 一致；
- `metadata` 用于承载 `doc_id / chunk_id / file_name / chunk_index` 等字段；
- 若索引中直接保存 chunk 内容，可通过 `content` 返回；
- 若未存内容，至少应保证上游可通过 metadata 定位 chunk。

## 4.3 Metadata 最低字段规范（强制）

每条写入向量的 `metadata` 至少必须包含：

```json
{
  "doc_id": "doc123",
  "chunk_id": "doc123#c000010",
  "file_name": "系统设计说明书.md",
  "chunk_index": 10
}
```

推荐补充：

```json
{
  "start_char": 1200,
  "end_char": 1680,
  "source": "local",
  "content": "本 chunk 文本"
}
```

约束：

- 缺少 `doc_id / chunk_id / file_name / chunk_index` 之一，不允许入库；
- 若 `vector_id != chunk_id`，必须在文档中明确说明映射关系；
- 推荐直接令 `vector_id = chunk_id`。

# 5. 核心接口设计（抽象基类）

## 5.1 BaseVectorDB

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class BaseVectorDB(ABC):
    @abstractmethod
    def upsert_vectors(self, items: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        写入或更新向量
        """
        pass

    @abstractmethod
    def query(
        self,
        embedding: List[float],
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        执行向量检索
        """
        pass

    @abstractmethod
    def delete(self, vector_ids: Optional[List[str]] = None, doc_id: Optional[str] = None) -> Dict[str, Any]:
        """
        删除向量（示例实现可不支持）
        """
        pass
```

约束：

- `upsert_vectors()` 的输入必须满足 chunk 级 metadata 约束；
- `query()` 必须返回 chunk 级可消费结果；
- `delete()` 可在示例实现中不支持，但接口必须保留，便于生产实现替换。

# 6. 核心实现设计（标准向量库实现）

## 6.1 类职责说明

标准向量库实现负责：

- 写入 chunk 级向量；
- 执行向量相似度检索；
- 根据 filters 过滤 metadata；
- 提供索引加载与持久化；
- 在支持的实现中提供删除能力；
- 返回统一、可消费的查询结果结构。

本实现必须保持：

- 无 HTTP 依赖；
- 可并发复用（至少保证线程/进程使用边界清晰）；
- 入库前执行 metadata 与向量校验；
- 输出结构稳定且可直接被 RAG 模块消费。

## 6.2 vector_id 策略（修订重点）

### 6.2.1 强制推荐策略

- 推荐：`vector_id = chunk_id`

### 6.2.2 不推荐策略

- 不推荐：`vector_id = doc_id`

原因：

- 一个文档通常会产生多个 chunk；
- 若 `vector_id = doc_id`，会出现覆盖或冲突；
- 会破坏 chunk 级引用与精确召回定位。

### 6.2.3 兼容规则

- 若历史实现已使用其他 id 方案，必须在 metadata 中保留 `chunk_id`；
- 新增实现一律推荐使用 `chunk_id` 作为 `vector_id`。

## 6.3 upsert_vectors() 规则（强制）

### 6.3.1 输入要求

每个写入项至少包含：

- `vector_id`
- `embedding`
- `metadata`

且 `metadata` 必须包含：

- `doc_id`
- `chunk_id`
- `file_name`
- `chunk_index`

### 6.3.2 校验要求

写入前必须校验：

- `vector_id` 非空；
- `embedding` 为非空数值列表；
- 所有向量维度一致；
- metadata 必填字段齐全；
- 若 `vector_id != metadata["chunk_id"]`，记录兼容映射说明或警告。

### 6.3.3 返回要求

成功响应建议结构：

```json
{
  "code": "SUCCESS",
  "message": "upsert ok",
  "data": {
    "count": 100
  }
}
```

失败响应应包含结构化 details，例如：

```json
{
  "code": "VECTOR_UPSERT_FAILED",
  "message": "向量写入失败",
  "details": {
    "index": "faiss_default",
    "count": 100,
    "dimension": 1024
  }
}
```

## 6.4 query() 规则（强制）

### 6.4.1 输入要求

- `embedding` 必须为数值向量；
- `top_k` 必须大于 0；
- `filters` 为可选 metadata 过滤条件，如 `doc_id`、`source`。

### 6.4.2 输出要求

返回的每条结果至少应包含：

- `vector_id`
- `score`
- `metadata`

且 `metadata` 至少应可提供：

- `doc_id`
- `chunk_id`
- `file_name`
- `chunk_index`

推荐直接补充：

- `content`
- `start_char`
- `end_char`

### 6.4.3 结果可消费性要求

本模块返回结果应尽量让 RAG 模块可直接消费，不应强依赖“再按 doc_id 去整篇 document_store 取原文”作为默认主路径。

## 6.5 filters 规则

允许按 metadata 做简单过滤，例如：

```python
filters = {
    "doc_id": "doc123",
    "source": "local"
}
```

约束：

- 过滤逻辑必须是 metadata 级别，而非业务逻辑级别；
- 不支持的过滤条件应有清晰说明；
- 示例实现可支持简单精确匹配；
- 生产实现可扩展为更复杂过滤表达式。

## 6.6 delete() 能力边界（修订重点）

### 6.6.1 接口必须保留

无论示例实现是否支持，`delete()` 接口都必须保留，以满足生产实现可替换性。

### 6.6.2 示例实现（可不支持）

对于本地 FAISS Flat 等示例实现，可定义：

- 返回 `VECTOR_DELETE_NOT_SUPPORTED`
- 文档中明确说明不支持原因

### 6.6.3 生产实现（必须支持）

生产实现应至少支持以下删除方式之一：

- 按 `vector_ids` 删除
- 按 `doc_id` 删除
- 按 metadata 条件批量删除

### 6.6.4 设计原因

保留 delete 接口的原因：

- 系统总设计要求支持数据生命周期管理；
- 文档删除、索引重建、被遗忘权等场景需要删除能力；
- 示例实现与生产实现能力边界必须文档化说明。

## 6.7 示例实现与生产实现分层说明（强制）

### 6.7.1 示例实现

适用于：

- 本地开发
- 联调验证
- 冒烟测试
- 小规模 PoC

允许简化：

- 使用本地 FAISS `IndexFlatIP`
- 不支持 delete
- 采用内存重建式 upsert

### 6.7.2 生产实现

必须满足：

- 支持删除；
- 支持较大规模索引；
- 支持更稳定持久化；
- 支持更可靠过滤能力；
- 支持可观测性、恢复与扩展。

推荐方案：

- FAISS + IDMap / remove_ids
- Milvus
- Chroma
- PGVector
- 其他支持删除与过滤的向量库

## 6.8 相似度与归一化说明

### 6.8.1 相似度策略

- 若使用 `IndexFlatIP` 并希望模拟余弦相似度，应要求输入向量已归一化；
- 若未归一化，应在文档中明确当前相似度语义。

### 6.8.2 与 Embedding 模块的边界

- Embedding 模块负责向量生成与归一化；
- 向量数据库模块不应承担复杂归一化主路径；
- 若实现内为了安全性补归一化，必须在文档中说明。

## 6.9 持久化与加载规则

### 6.9.1 持久化要求

- 示例实现可使用本地文件持久化；
- 应将索引与 metadata 映射分开保存；
- 保存失败时需返回明确错误码或日志。

### 6.9.2 加载要求

- 启动时若存在历史索引，可加载；
- 加载失败时需明确区分“文件不存在”和“文件损坏”；
- 生产实现应支持更可靠的恢复机制。

## 6.10 错误处理与统一返回

### 6.10.1 错误码约定

| 错误码 | 说明 |
| :--- | :--- |
| `SUCCESS` | 执行成功 |
| `FAISS_NOT_INSTALLED` | 缺少 FAISS 依赖 |
| `VECTOR_UPSERT_FAILED` | 向量写入失败 |
| `VECTOR_QUERY_FAILED` | 向量查询失败 |
| `VECTOR_DELETE_NOT_SUPPORTED` | 当前实现不支持删除 |
| `PARAM_INVALID` | 输入向量或参数不合法 |
| `UNKNOWN_ERROR` | 未知异常兜底 |

### 6.10.2 返回约束

- 成功与失败响应都建议兼容系统统一响应结构；
- `details` 尽量结构化，如：
  - `index`
  - `dimension`
  - `count`
  - `operation`
  - `doc_id`

# 7. 模块调用示例

## 7.1 写入示例

```python
items = [
    {
        "vector_id": "doc123#c000010",
        "embedding": [0.1, 0.2, 0.3],
        "metadata": {
            "doc_id": "doc123",
            "chunk_id": "doc123#c000010",
            "file_name": "系统设计说明书.md",
            "chunk_index": 10,
            "content": "系统采用分层架构设计。"
        }
    }
]

result = vector_db.upsert_vectors(items)
```

## 7.2 查询示例

```python
result = vector_db.query(
    embedding=[0.1, 0.2, 0.3],
    top_k=5,
    filters={"doc_id": "doc123"}
)
```

## 7.3 删除示例（生产实现）

```python
result = vector_db.delete(doc_id="doc123")
```

# 8. 测试规范

## 8.1 测试范围（强制）

| 测试类型 | 测试内容 |
| :--- | :--- |
| upsert 测试 | 是否正确写入向量与 metadata |
| metadata 校验测试 | 缺少 `doc_id / chunk_id / file_name / chunk_index` 时是否拒绝写入 |
| vector_id 策略测试 | `vector_id = chunk_id` 是否正确；错误策略是否能识别 |
| query 结果结构测试 | 返回是否满足 chunk 级可消费结构 |
| filters 测试 | `doc_id / source` 等过滤是否生效 |
| delete 边界测试 | 示例实现是否正确返回 `VECTOR_DELETE_NOT_SUPPORTED` |
| 维度一致性测试 | 向量维度不一致时是否返回结构化错误 |
| 持久化与加载测试 | 索引持久化与恢复是否正常 |
| 统一返回测试 | 返回结构是否兼容系统统一响应 |

## 8.2 Mock 示例

```python
items = [
    {
        "vector_id": "doc1#c000001",
        "embedding": [0.1, 0.2, 0.3],
        "metadata": {
            "doc_id": "doc1",
            "chunk_id": "doc1#c000001",
            "file_name": "demo.md",
            "chunk_index": 1,
            "content": "系统采用分层架构设计。"
        }
    }
]
```

# 9. 模块配置管理

建议配置示例如下：

```yaml
vector_db:
  provider_type: "faiss"
  index_name: "faiss_default"
  persist_path: "./vector_store"
  metric: "inner_product"
  require_normalized_vector: true
  enable_delete: false
```

说明：

- `provider_type` 用于区分 FAISS / Milvus / PGVector 等实现；
- `index_name` 为索引名；
- `persist_path` 为本地持久化目录；
- `metric` 用于标明相似度策略；
- `require_normalized_vector` 指示是否要求上游传入归一化向量；
- `enable_delete` 用于标识当前实现是否支持删除。

# 10. 交付物清单（强制）

模块开发完成后，需提交以下交付物：

| 交付物 | 说明 |
| :--- | :--- |
| `core/base.py` | 抽象基类，定义向量数据库核心接口 |
| `core/impl.py` | 默认向量库实现 |
| `utils/tool_functions.py` | metadata 校验、过滤、持久化辅助函数 |
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
| 上游调用 | RAG 模块或索引链路只能依赖 `BaseVectorDB` 抽象接口 |
| metadata 约束 | 所有写入都必须满足 chunk 级 metadata 最低字段规范 |
| vector_id 约束 | 新实现推荐使用 `chunk_id` 作为 `vector_id` |
| delete 约束 | 示例实现可不支持，但生产实现必须支持删除 |
| 相似度约束 | 必须在文档中明确相似度语义与归一化前提 |
| 统一结构 | 查询结果必须可直接被 RAG 模块消费，且保持稳定结构 |

# 12. 常见问题（FAQ）

| 问题 | 说明 |
| :--- | :--- |
| 为什么不能用 `doc_id` 作为 `vector_id`？ | 因为一个文档通常有多个 chunk，会导致覆盖或冲突，破坏 chunk 级检索与引用。 |
| metadata 为什么必须包含 `chunk_id / chunk_index`？ | 因为 RAG 引用、精确召回与评测都依赖 chunk 级定位。 |
| 示例实现为什么可以不支持 delete？ | 因为本地 FAISS Flat 主要用于开发与联调，但生产实现必须具备删除能力。 |
| 向量库模块是否负责向量归一化？ | 主路径不负责，归一化应由 Embedding 模块完成；如内部补归一化，必须文档说明。 |

# 13. 附录：系统错误码关联

本模块直接使用或透传的核心错误码如下：

| 错误码 | 来源 | 适用场景 |
| :--- | :--- | :--- |
| `SUCCESS` | 本模块/下游 | 请求成功 |
| `FAISS_NOT_INSTALLED` | 本模块 | 缺少 FAISS 依赖 |
| `VECTOR_UPSERT_FAILED` | 本模块 | 向量写入失败 |
| `VECTOR_QUERY_FAILED` | 本模块 | 向量查询失败 |
| `VECTOR_DELETE_NOT_SUPPORTED` | 本模块 | 当前实现不支持删除 |
| `PARAM_INVALID` | 本模块 | 输入向量或参数不合法 |
| `UNKNOWN_ERROR` | 异常兜底 | 未知运行时异常 |

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
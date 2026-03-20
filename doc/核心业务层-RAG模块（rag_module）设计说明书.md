# 核心业务层-RAG模块（rag_module）设计说明书

| 文档版本 | v1.1 |
| :--- | :--- |
| 最后更新 | 2026-03-19 |
| 维护责任人 | RAG模块开发负责人 |
| 状态 | 修订版 |

> 本修订版对齐《RAG与Agent系统架构设计说明书》v1.1、接口层-请求响应处理模块修订版、应用层-API服务模块修订版、协同调度模块修订版与 Agent 模块修订版，重点修正 chunk 级检索与引用规范、依赖注入边界、统一请求透传、上下文拼装规则、索引链路职责与统一响应结构。

# 1. 文档概述

## 1.1 文档目的

本文档为 RAG 与 Agent 系统核心业务层-RAG模块（`rag_module`）的独立设计说明书。

本模块负责系统中的检索增强生成能力，是“问题向量化 -> 检索召回 -> 上下文拼装 -> 生成回答 -> 引用输出”的核心执行模块。模块在系统中的职责包括：

- 接收标准化 RAG 请求；
- 执行 query normalize / retrieve / 可选 rerank / context assemble / generate；
- 基于 chunk 级检索结果构造模型上下文；
- 生成包含 citations 的最终回答；
- 返回系统统一响应结构；
- 作为独立 RAG 执行器供协同调度模块和 Agent 工具调用。

本文档作为本模块开发、测试、联调与后续替换实现的唯一标准依据。

## 1.2 适用人群

适用于 RAG 模块开发人员、协同调度模块开发人员、Agent 模块开发人员、数据层开发人员、测试人员、架构设计人员及后续维护人员。

## 1.3 核心需求回顾

| 需求类型 | 具体要求 |
| :--- | :--- |
| 模块功能 | 提供 query 处理、向量检索、上下文拼装、答案生成与 citations 输出能力。 |
| 开发语言 | Python 3.10+，最低 3.10，推荐 3.12，与系统整体保持一致。 |
| 开发模式 | 独立开发、可替换实现、通过抽象接口集成。 |
| 文档要求 | 与系统总设计 v1.1 及接口层 / 应用层 / 调度层 / Agent 模块子设计保持一致。 |
| 模块约束 | 本模块不负责 HTTP 协议处理；不重新生成新的业务 trace_id；必须按 chunk 级执行检索与引用；不得把整篇文档直接当作检索最小单元。 |

# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于系统**核心业务层**，是系统中负责检索增强生成能力的核心模块。

本模块职责如下：

- 接收标准化查询请求；
- 对 query 做最小清洗与规范化；
- 调用 Embedding 能力生成查询向量；
- 调用向量数据库执行 chunk 级检索；
- 基于检索结果组装上下文；
- 调用大模型生成回答；
- 生成 chunk 级 citations；
- 返回统一响应结构。

本模块不负责：

- 不负责 HTTP/HTTPS 协议处理；
- 不负责业务语义级参数校验（由接口层负责）；
- 不负责索引构建全流程编排；
- 不直接编排上传、鉴权、中间件、路由；
- 不重新生成新的业务 `trace_id`；
- 不将整篇文档直接作为最终检索上下文主路径。

## 2.2 模块边界

### 2.2.1 本模块负责

- Query 规范化；
- Query 向量化；
- 向量召回；
- 可选重排；
- 上下文拼装；
- 生成回答；
- 输出 citations。

### 2.2.2 本模块不负责

- 不做统一请求标准化；
- 不直接做 chunking 索引构建（由索引链路或 index_service 负责）；
- 不负责管理上传文件；
- 不在本模块实现 HTTP 状态码映射；
- 不直接控制外部服务生命周期。

## 2.3 依赖关系

### 2.3.1 上游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `orchestrator_module` | 通过协同调度模块接收 `type=rag` 请求 |
| `agent_module`（工具调用场景） | 通过工具能力间接调用 RAG 检索与生成 |

### 2.3.2 下游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `embedding_module` | 生成查询向量 |
| `vector_db_module` | 执行 chunk 级相似度检索 |
| `llm_adapter_module` 或等价 LLM 服务 | 生成最终回答 |
| `document_store_module`（可选） | 在需要补全文档片段时读取原始内容或片段映射 |

### 2.3.3 基础依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `config_module` | 模块配置读取 |
| `log_module` | 检索与生成过程日志记录 |
| `exception_module` | 异常封装 |
| `common_utils_module` | 通用辅助函数 |

说明：

- 本模块优先依赖抽象接口，如 `BaseEmbedding`、`BaseVectorDB`、LLM 抽象客户端；
- 具体默认实现由 bootstrap 注入；
- `document_store_module` 不应用于“把整篇文档取回再截断”作为默认主路径，而应优先依赖 chunk 级检索结果本身。

# 3. 统一项目结构规范

本模块遵循系统总设计 v1.1 的统一目录规范。

## 3.1 必选目录与文件

```text
rag_module/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── base.py
│   └── impl.py
├── model/
│   ├── __init__.py
│   └── data_model.py
├── prompt/
│   ├── __init__.py
│   └── prompt_template.py
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

- `rerank/`：重排器相关逻辑
- `chunking/`：若短期将 chunking 作为模块内组件实现
- `examples/`：标准请求与返回示例
- `docs/`：补充说明材料

说明：

- 当前阶段可采用 `core/impl.py` 单文件实现；
- 若重排与引用逻辑复杂，建议拆分 `rerank/` 或 `chunking/`；
- 新增扩展目录必须在 `README.md` 中说明职责与边界。

# 4. 核心数据模型设计

## 4.1 RAGRequest

```python
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

@dataclass
class RAGRequest:
    query: str
    top_k: int = 5
    session_id: Optional[str] = None
    trace_id: Optional[str] = None
    extra_params: Dict[str, Any] = field(default_factory=dict)
```

说明：

- `query` 为核心输入；
- `top_k` 表示最终进入答案生成的检索片段数量或候选控制参数；
- `session_id` 在 RAG 场景下可为空；
- `trace_id` 由应用层生成并经接口层、调度层透传；
- rewrite、filter、source 等扩展信息统一通过 `extra_params` 传入。

## 4.2 RetrievedChunk

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class RetrievedChunk:
    chunk_id: str
    doc_id: str
    file_name: str
    chunk_index: int
    content: str
    score: float
    start_char: Optional[int] = None
    end_char: Optional[int] = None
```

说明：

- 检索结果必须以 chunk 级对象表达；
- `content` 为该 chunk 的文本内容；
- `start_char / end_char` 用于稳定引用；
- 不允许仅返回 `doc_id` 而缺少 `chunk_id / chunk_index / content`。

## 4.3 Citation

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class Citation:
    chunk_id: str
    doc_id: str
    file_name: str
    start_char: Optional[int] = None
    end_char: Optional[int] = None
    score: Optional[float] = None
```

## 4.4 RAGResponse

```python
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any

@dataclass
class RAGResponse:
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
  "answer": "最终回答文本 [CIT:doc123#c000010]",
  "citations": [
    {
      "chunk_id": "doc123#c000010",
      "doc_id": "doc123",
      "file_name": "xxx.md",
      "start_char": 1200,
      "end_char": 1680,
      "score": 0.87
    }
  ],
  "retrieved_chunks": [
    {
      "chunk_id": "doc123#c000010",
      "doc_id": "doc123",
      "file_name": "xxx.md",
      "chunk_index": 10,
      "score": 0.87
    }
  ]
}
```

# 5. 核心接口设计（抽象基类）

## 5.1 BaseRAG

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List

class BaseRAG(ABC):
    @abstractmethod
    def retrieve(self, request: Dict[str, Any]) -> List[Dict[str, Any]]:
        """
        执行检索并返回 chunk 级结果
        """
        pass

    @abstractmethod
    def generate(self, request: Dict[str, Any], retrieved_chunks: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        基于检索结果生成回答
        """
        pass

    @abstractmethod
    def run(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        执行完整 RAG 流程
        """
        pass
```

约束：

- `retrieve()` 必须返回 chunk 级结果；
- `generate()` 必须可生成 citations；
- `run()` 不负责做业务语义校验；
- 输入输出必须兼容系统统一结构。

# 6. 核心实现设计（SimpleRAG）

## 6.1 类职责说明

`SimpleRAG` 是系统默认 RAG 实现，负责：

- 接收标准化 RAG 请求；
- 进行 query 规范化与向量化；
- 执行 chunk 级向量检索；
- 拼装上下文并调用 LLM 生成回答；
- 输出 citations 与统一响应结构。

本实现必须保持：

- 无 HTTP 依赖；
- 可并发复用；
- `trace_id` 全流程透传；
- 以 chunk 级结果为检索、引用与回答的核心单元。

## 6.2 构造函数建议

```python
from embedding_module.core.base import BaseEmbedding
from vector_db_module.core.base import BaseVectorDB

class SimpleRAG(BaseRAG):
    def __init__(
        self,
        llm_client,
        embedding: BaseEmbedding | None = None,
        vector_db: BaseVectorDB | None = None,
        doc_store = None,
        reranker = None,
    ):
        self.llm_client = llm_client
        self.embedding = embedding
        self.vector_db = vector_db
        self.doc_store = doc_store
        self.reranker = reranker
```

说明：

- 不在构造函数内硬编码创建默认实现；
- 默认实现由 bootstrap 注入；
- `doc_store` 仅用于补充必要片段或元数据，不应成为“取整篇文档再截断”的主路径；
- `reranker` 可为空，未启用时直接按检索顺序使用。

## 6.3 run() 处理顺序（强制）

```text
1. 读取 query / top_k / trace_id / extra_params
2. 调用 retrieve() 获取 chunk 级检索结果
3. 可选：执行 rerank
4. 调用 generate() 基于 chunk 结果生成回答
5. 生成 citations
6. 返回统一响应
7. 异常时返回标准错误响应
```

## 6.4 Query Normalize / Rewrite 规则

### 6.4.1 Query Normalize

当前版本至少执行：

- 去首尾空白；
- 统一换行；
- 长度截断（防止异常长输入）；
- 必要时简单去噪。

### 6.4.2 Query Rewrite（可选）

当前版本可不强制实现完整 rewrite，但若实现，必须输出结构：

```json
{
  "rewrite_query": "用于检索的改写问题",
  "keywords": ["关键字1"],
  "filters": {
    "doc_id": "...",
    "source": "..."
  }
}
```

说明：

- rewrite 结果必须能直接送入 embedding；
- 若未实现 rewrite，默认使用 normalize 后的 query 直接检索。

## 6.5 retrieve() 设计（必须改）

### 6.5.1 输入要求

- 输入 `query` 已由接口层保证合法；
- 本模块不重新校验 query 是否为空；
- `trace_id` 必须透传到 embedding 与 vector_db 调用链路（如支持）。

### 6.5.2 执行要求

```text
1. normalize query
2. embedding.embed_text(query)
3. vector_db.query(...)
4. 返回 chunk 级检索结果列表
```

### 6.5.3 返回约束

`retrieve()` 返回的每个元素至少包含：

- `chunk_id`
- `doc_id`
- `file_name`
- `chunk_index`
- `content`
- `score`

若缺少以上字段，不视为合格的 chunk 级结果。

### 6.5.4 禁止事项

- 不允许仅返回 `doc_id`
- 不允许把整篇文档内容作为默认检索返回单元
- 不允许在 retrieve() 完成后再按 `doc_id` 去 document_store 取整篇内容并整体截断作为主上下文

## 6.6 rerank 规则（可选）

若启用 rerank：

- 输入为 retrieve() 的候选 chunks；
- 输出仍必须保持 chunk 级对象；
- 不得丢失 `chunk_id / doc_id / file_name / chunk_index / content`；
- 默认 `top_k_rerank` 建议 8；
- 未启用时，按 retrieve 结果顺序进入 context assemble。

## 6.7 Context Assemble 规则（强制）

### 6.7.1 拼装原则

- 以 chunk 为最小上下文单元；
- 按 rank 依次加入；
- 达到 `max_context_tokens` 即停止；
- 每个 chunk 可按 `max_chunk_in_prompt_tokens` 截断；
- 不再以“整篇文档”为上下文主路径。

### 6.7.2 相邻 chunk 合并（可选）

- 若来自同一文档且 chunk_index 相邻，可在保留引用映射的前提下合并显示；
- 合并后仍必须保留 chunk 级 citation 映射。

### 6.7.3 结果去重

- `chunk_id` 去重；
- 避免同一内容重复进入 prompt。

## 6.8 generate() 设计（必须改）

### 6.8.1 输入

- 标准化请求
- chunk 级检索结果列表

### 6.8.2 生成要求

- 必须基于 chunk 内容组装 prompt；
- 输出回答时应生成 citations；
- 若模型本身不直接支持引用插入，可由后处理逻辑补 `[CIT:chunk_id]`。

### 6.8.3 返回要求

输出结果应至少包含：

- `answer`
- `citations`

示例：

```json
{
  "answer": "本系统采用分层架构+模块化设计……[CIT:doc123#c000010]",
  "citations": [
    {
      "chunk_id": "doc123#c000010",
      "doc_id": "doc123",
      "file_name": "系统设计说明书.md",
      "start_char": 1200,
      "end_char": 1680,
      "score": 0.87
    }
  ]
}
```

## 6.9 citations 规则（强制）

### 6.9.1 Answer 中的引用标记

- 文内引用格式：`[CIT:chunk_id]`
- 多引用格式：`[CIT:chunkA,chunkB]`

### 6.9.2 citations 字段要求

每条 citation 至少包含：

- `chunk_id`
- `doc_id`
- `file_name`

推荐补充：

- `start_char`
- `end_char`
- `score`

### 6.9.3 兼容规则

- 若暂时无法在 answer 中插入引用标记，也必须返回 `citations` 字段；
- 最终目标是 answer 与 citations 同时可用。

## 6.10 trace_id 规则（强制）

- `trace_id` 由应用层生成，经接口层与调度层透传到本模块；
- 本模块不得重新生成新的业务 `trace_id`；
- embedding、检索、生成、异常日志都应尽量附带同一 `trace_id`；
- 最终响应必须保留该 `trace_id`。

## 6.11 索引链路边界（修订重点）

本模块与索引构建链路关系如下：

- 本模块负责“消费已构建好的 chunk 索引”；
- 本模块不直接负责 parser -> chunker -> embedding -> vector_db 的离线构建主流程；
- 若短期将 chunking 作为模块内组件实现，也仅限于为开发与联调提供兼容能力；
- 正式索引构建应通过独立 index_service 或统一索引流程完成。

## 6.12 错误处理与统一返回

### 6.12.1 错误码约定

| 错误码 | 说明 |
| :--- | :--- |
| `SUCCESS` | 执行成功 |
| `EMBEDDING_CONFIG_MISSING` | embedding 配置缺失 |
| `EMBEDDING_INIT_FAILED` | embedding 初始化失败 |
| `VECTOR_QUERY_FAILED` | 向量检索失败 |
| `RAG_RUN_FAILED` | RAG 执行失败 |
| `UNKNOWN_ERROR` | 未知异常兜底 |

### 6.12.2 返回约束

- 所有成功与失败响应都必须返回 `trace_id`
- 若失败且属于可重试错误，必须正确设置 `retryable`
- `details` 需尽量结构化，如包含 `top_k`、`embedding_dim`、`stage` 等信息

# 7. 模块调用示例

## 7.1 基础组装示例

```python
from bootstrap import build_embedding, build_vector_db, build_llm_client
from rag_module.core.impl import SimpleRAG

embedding = build_embedding()
vector_db = build_vector_db()
llm_client = build_llm_client()

rag = SimpleRAG(
    llm_client=llm_client,
    embedding=embedding,
    vector_db=vector_db
)
```

## 7.2 标准 RAG 调用示例

```python
request = {
    "query": "RAG 系统架构是什么？",
    "top_k": 5,
    "trace_id": "trace_demo_001",
    "extra_params": {}
}

result = rag.run(request)
```

## 7.3 Agent 工具调用示例（概念性）

```python
def rag_search_tool(payload):
    return rag.run({
        "query": payload["query"],
        "top_k": payload.get("top_k", 5),
        "trace_id": payload.get("trace_id"),
        "extra_params": payload.get("extra_params", {})
    })
```

# 8. 测试规范

## 8.1 测试范围（强制）

| 测试类型 | 测试内容 |
| :--- | :--- |
| 检索结果结构测试 | retrieve() 是否返回 chunk 级结果 |
| chunk 引用测试 | answer 与 citations 是否正确输出 |
| 上下文拼装测试 | context assemble 是否按 chunk 构造且长度受控 |
| trace 透传测试 | `trace_id` 是否贯穿 embedding / 检索 / 生成 / 响应 |
| rerank 测试 | 启用 rerank 时是否仍保持 chunk 级结构 |
| 错误处理测试 | embedding 初始化失败、vector query 失败、生成失败等场景 |
| 禁止整篇回退测试 | 不应默认将整篇文档取回后截断作为主路径 |
| 统一响应测试 | 返回值是否符合统一响应结构 |

## 8.2 Mock 示例

```python
class MockEmbedding:
    def embed_text(self, text):
        return [0.1, 0.2, 0.3]

class MockVectorDB:
    def query(self, embedding, top_k=5, filters=None):
        return [
            {
                "chunk_id": "doc1#c000001",
                "doc_id": "doc1",
                "file_name": "demo.md",
                "chunk_index": 1,
                "content": "系统采用分层架构设计。",
                "score": 0.91,
                "start_char": 0,
                "end_char": 18
            }
        ]

class MockLLM:
    def generate(self, prompt):
        return "系统采用分层架构设计。[CIT:doc1#c000001]"
```

# 9. 模块配置管理

建议配置示例如下：

```yaml
rag:
  top_k_retrieve: 50
  top_k_rerank: 8
  max_context_tokens: 3000
  max_chunk_in_prompt_tokens: 600
  enable_rerank: false
  enable_rewrite: false
```

说明：

- `top_k_retrieve` 为召回阶段参数；
- `top_k_rerank` 为重排后进入上下文拼装的候选数量；
- `max_context_tokens` 控制 prompt 上下文长度；
- `max_chunk_in_prompt_tokens` 控制单 chunk 最大进入 prompt 的长度；
- rewrite / rerank 可通过配置控制开关。

# 10. 交付物清单（强制）

模块开发完成后，需提交以下交付物：

| 交付物 | 说明 |
| :--- | :--- |
| `core/base.py` | 抽象基类，定义 RAG 核心接口 |
| `core/impl.py` | 默认 RAG 实现 |
| `model/data_model.py` | RAG 数据模型 |
| `prompt/prompt_template.py` | Prompt 模板 |
| `utils/tool_functions.py` | 上下文拼装、citations、辅助函数 |
| `config/config.py` | 模块配置读取逻辑 |
| `tests/test_impl.py` | 核心测试用例 |
| `README.md` | 模块说明文档 |
| `requirements.txt` | 依赖包清单 |

可选扩展交付物（按复杂度选择）：

- `rerank/*`
- `chunking/*`
- `examples/*`
- `docs/*`

若使用可选扩展目录，必须在 `README.md` 中说明职责与边界，并纳入测试覆盖。

# 11. 可替换性约束

| 约束项 | 说明 |
| :--- | :--- |
| 上游调用 | 调度层只能依赖 `BaseRAG` 抽象接口 |
| 下游依赖 | 本模块优先依赖 `BaseEmbedding`、`BaseVectorDB`、抽象 LLM 客户端 |
| chunk 约束 | 检索、引用、上下文拼装都必须以 chunk 为最小单元 |
| trace 约束 | `trace_id` 由应用层生成并经接口层、调度层透传，本模块不得重新生成 |
| 索引边界 | 本模块消费索引，不直接承担离线索引构建主流程 |
| 统一结构 | 请求与响应结构必须严格遵循系统总设计 v1.1 |

# 12. 常见问题（FAQ）

| 问题 | 说明 |
| :--- | :--- |
| 为什么不能直接按 doc_id 取整篇文档再截断？ | 因为这会破坏 chunk 级检索与引用，导致召回精度、引用稳定性和评测一致性下降。 |
| citations 必须返回吗？ | 必须。即使 answer 中暂时未插入 `[CIT:chunk_id]`，也必须返回结构化 `citations` 字段。 |
| rewrite / rerank 是否强制？ | 当前版本不强制，但如果启用，必须保持 chunk 级结果与统一结构不变。 |
| RAG 模块是否负责索引构建？ | 不负责离线索引构建主流程；本模块主要消费已构建好的 chunk 索引。 |

# 13. 附录：系统错误码关联

本模块直接使用或透传的核心错误码如下：

| 错误码 | 来源 | 适用场景 |
| :--- | :--- | :--- |
| `SUCCESS` | 本模块/下游 | 请求成功 |
| `EMBEDDING_CONFIG_MISSING` | 本模块/下游 | embedding 配置缺失 |
| `EMBEDDING_INIT_FAILED` | 本模块/下游 | embedding 初始化失败 |
| `VECTOR_QUERY_FAILED` | 本模块/下游 | 向量检索失败 |
| `RAG_RUN_FAILED` | 本模块 | RAG 执行过程失败 |
| `UNKNOWN_ERROR` | 异常兜底 | 未知运行时异常 |

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
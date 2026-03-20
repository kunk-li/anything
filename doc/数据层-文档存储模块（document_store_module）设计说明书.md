# 数据层-文档存储模块（document_store_module）设计说明书

| 文档版本 | v1.1 |
| :--- | :--- |
| 最后更新 | 2026-03-19 |
| 维护责任人 | 文档存储模块开发负责人 |
| 状态 | 修订版 |

> 本修订版对齐《RAG与Agent系统架构设计说明书》v1.1、RAG 模块修订版、向量数据库模块修订版、索引链路规范与统一请求/响应约束，重点修正文档主键与去重策略、原文与元数据分层、chunk 回溯关系、删除生命周期、与索引链路/向量库的职责边界及统一错误码。

# 1. 文档概述

## 1.1 文档目的

本文档为 RAG 与 Agent 系统数据层-文档存储模块（`document_store_module`）的独立设计说明书。

本模块负责系统中的原始文档与文档元数据存储能力，是“文档创建 -> 文档落盘 -> 元数据维护 -> 查找读取 -> 更新删除 -> 与 chunk / vector 建立回溯关系”的核心数据层模块。模块在系统中的职责包括：

- 接收解析后的标准化文档内容并创建文档记录；
- 持久化原始文本、来源信息与文档元数据；
- 为索引链路提供稳定的 `doc_id` 与原文回溯能力；
- 支持文档读取、更新、删除、列表与去重；
- 为 chunking、引用、审计与生命周期管理提供上游数据基础；
- 作为可替换的数据存储抽象层支持本地文件存储、对象存储或数据库存储等实现。

本文档作为本模块开发、测试、联调与后续替换实现的唯一标准依据。

## 1.2 适用人群

适用于文档存储模块开发人员、文档解析模块开发人员、索引链路开发人员、RAG 模块开发人员、测试人员、架构设计人员及后续维护人员。

## 1.3 核心需求回顾

| 需求类型 | 具体要求 |
| :--- | :--- |
| 模块功能 | 提供文档原文与元数据存储、读取、更新、删除、去重与回溯能力。 |
| 开发语言 | Python 3.10+，最低 3.10，推荐 3.12，与系统整体保持一致。 |
| 开发模式 | 独立开发、可替换实现、通过抽象接口集成。 |
| 文档要求 | 与系统总设计 v1.1、RAG / 向量数据库 / 索引链路相关设计保持一致。 |
| 模块约束 | 本模块不负责 HTTP 协议处理；不负责向量写入；不负责整套索引构建编排；必须输出稳定 doc_id 与完整元数据。 |

# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于系统**数据层**，是系统中负责文档原文与元数据存储的数据能力模块。

本模块职责如下：

- 接收来自文档解析模块或索引链路的标准化文档；
- 为文档生成稳定 `doc_id`；
- 保存文档原文、文件名、来源、哈希、大小、创建时间等元数据；
- 支持按 `doc_id` 查询、读取与更新；
- 支持文档去重、删除与生命周期管理；
- 为 chunking 与 citations 提供文档级回溯基础；
- 向上层提供统一抽象接口，屏蔽底层存储差异。

本模块不负责：

- 不负责 HTTP/HTTPS 协议处理；
- 不负责 embedding 生成；
- 不负责向量数据库写入；
- 不负责 chunking 主流程；
- 不负责回答生成或引用渲染；
- 不直接决定 HTTP 状态码。

## 2.2 模块边界

### 2.2.1 本模块负责

- 文档创建（create_document）
- 文档保存（save_document）
- 文档读取（get_document）
- 文档更新（update_document）
- 文档删除（delete_document）
- 元数据维护
- 哈希去重与文档列表
- chunk / vector 回溯所需的 doc 级信息提供

### 2.2.2 本模块不负责

- 不负责 chunk 生成；
- 不负责向量索引写入；
- 不负责 query 检索与 rerank；
- 不直接把整篇文档作为 RAG 主上下文传给生成器；
- 不在本模块编排 parser -> chunker -> embedding -> vector_db 全链路。

## 2.3 依赖关系

### 2.3.1 上游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `document_parser_module` | 提供标准化文档内容 |
| `index_service / 索引链路` | 调用文档创建、保存、去重与删除 |
| 管理类服务（可选） | 文档生命周期管理、清理、回收等 |

### 2.3.2 下游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| 本地文件系统 / 对象存储 / 数据库 | 实际文档持久化载体 |

### 2.3.3 基础依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `config_module` | 模块配置读取 |
| `log_module` | 存储与删除日志记录 |
| `exception_module` | 异常封装 |
| `common_utils_module` | 通用辅助函数 |

说明：

- 本模块必须通过抽象接口屏蔽底层存储差异；
- 示例实现可使用本地文件系统；
- 生产实现可替换为对象存储、数据库或混合方案。

# 3. 统一项目结构规范

本模块遵循系统总设计 v1.1 的统一目录规范。

## 3.1 必选目录与文件

```text
document_store_module/
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

- `providers/`：不同存储后端实现
- `examples/`：调用示例
- `docs/`：补充说明材料

说明：

- 当前阶段可采用 `core/impl.py` 单文件实现；
- 当 provider 类型增多时，建议拆分 `providers/`；
- 新增扩展目录必须在 `README.md` 中说明职责与边界。

# 4. 核心数据模型设计

## 4.1 DocumentRecord

```python
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

@dataclass
class DocumentRecord:
    doc_id: str
    file_name: str
    content: str
    meta: Dict[str, Any] = field(default_factory=dict)
```

说明：

- `doc_id` 是文档主键；
- `content` 为原始或标准化后的文档正文；
- `meta` 用于承载文件来源、哈希、大小、创建时间等元数据。

## 4.2 DocumentMeta 最低字段规范（强制）

每条文档记录的 `meta` 至少必须包含：

```json
{
  "file_name": "系统设计说明书.md",
  "source": "local",
  "content_hash": "sha256:xxxx",
  "content_length": 12345,
  "created_at": "2026-03-19T12:00:00Z"
}
```

推荐补充：

```json
{
  "storage_type": "text",
  "updated_at": "2026-03-19T12:10:00Z",
  "mime_type": "text/markdown",
  "tags": ["design", "rag"]
}
```

约束：

- `file_name / source / content_hash / content_length / created_at` 缺一不可；
- `content_length` 应是文档正文长度，不允许同一记录中重复定义或多次覆盖；
- `content_hash` 应稳定可复现，用于去重与审计。

## 4.3 ChunkRef（推荐）

为建立文档与 chunk 的回溯关系，建议定义：

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class ChunkRef:
    doc_id: str
    chunk_id: str
    chunk_index: int
    start_char: Optional[int] = None
    end_char: Optional[int] = None
```

说明：

- `document_store_module` 可以不直接持久化所有 chunk；
- 但必须保证文档原文可支撑 chunk 的回溯与引用定位。

# 5. 核心接口设计（抽象基类）

## 5.1 BaseDocumentStore

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional

class BaseDocumentStore(ABC):
    @abstractmethod
    def create_document(self, content: str, file_name: str, **kwargs) -> Dict[str, Any]:
        """
        创建标准文档对象
        """
        pass

    @abstractmethod
    def save_document(self, document: Dict[str, Any]) -> Dict[str, Any]:
        """
        持久化保存文档
        """
        pass

    @abstractmethod
    def get_document(self, doc_id: str) -> Dict[str, Any]:
        """
        根据 doc_id 获取文档
        """
        pass

    @abstractmethod
    def update_document(self, doc_id: str, content: Optional[str] = None, meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """
        更新文档内容或元数据
        """
        pass

    @abstractmethod
    def delete_document(self, doc_id: str) -> Dict[str, Any]:
        """
        删除文档
        """
        pass
```

可选扩展接口：

```python
def list_documents(self, limit: int = 100) -> List[Dict[str, Any]]: ...
def find_by_hash(self, content_hash: str) -> Optional[Dict[str, Any]]: ...
```

约束：

- `create_document()` 必须生成稳定的 `doc_id`；
- `save_document()` 之前应完成必要校验；
- `delete_document()` 必须保留与向量索引联动的上游接口契约，但不直接负责删向量。

# 6. 核心实现设计（标准文档存储实现）

## 6.1 类职责说明

标准文档存储实现负责：

- 创建并持久化文档；
- 维护文档正文与元数据；
- 基于哈希做去重；
- 提供读取、更新、删除能力；
- 为 chunking 与引用回溯提供 doc 级基础信息。

本实现必须保持：

- 无 HTTP 依赖；
- doc_id 稳定；
- 文档正文与元数据分层清晰；
- 不将向量写入职责混入本模块；
- 返回结构稳定，可供索引链路与 RAG 模块使用。

## 6.2 doc_id 规则（修订重点）

### 6.2.1 强制要求

- 每篇文档必须生成唯一 `doc_id`
- `doc_id` 一经生成，应作为文档主键稳定存在
- chunk_id 应基于 `doc_id` 派生，例如：`{doc_id}#c000010`

### 6.2.2 推荐生成方式

可采用以下任一稳定方式：

- 基于时间戳 + 随机串
- 基于哈希前缀 + 随机串
- 基于内容哈希与命名空间生成 UUID

约束：

- 不建议仅用文件名作为 `doc_id`
- 不建议在更新文档内容后重新生成新的 `doc_id`，除非明确将其视为新文档版本

## 6.3 create_document() 规则（强制）

### 6.3.1 输入要求

输入至少包含：

- `content`
- `file_name`

可选输入：

- `source`
- `mime_type`
- `tags`
- 其他补充 meta

### 6.3.2 输出要求

输出文档对象至少包含：

- `doc_id`
- `file_name`
- `content`
- `meta`

且 `meta` 至少包含：

- `file_name`
- `source`
- `content_hash`
- `content_length`
- `created_at`

### 6.3.3 校验要求

创建前必须校验：

- `content` 非空字符串；
- `file_name` 非空；
- `content_length` 只定义一次且与内容长度一致；
- `content_hash` 稳定可复现。

## 6.4 save_document() 规则（强制）

### 6.4.1 存储分层

推荐保存为两部分：

- 原文内容文件 / 对象
- 元数据文件 / 记录

说明：

- 原文与元数据应逻辑分离；
- 元数据应可独立读取，不必每次读取整篇原文。

### 6.4.2 去重策略

推荐按 `content_hash` 做去重：

- 若 hash 已存在，可根据策略：
  - 直接返回已有文档
  - 记录重复引用关系
  - 或保存为新版本

当前版本至少应支持：

- `find_by_hash(content_hash)`
- 保存时识别重复内容

### 6.4.3 返回要求

成功响应建议结构：

```json
{
  "code": "SUCCESS",
  "message": "document saved",
  "data": {
    "doc_id": "doc123",
    "file_name": "系统设计说明书.md"
  }
}
```

## 6.5 get_document() 规则

### 6.5.1 返回结构

返回文档时，建议结构如下：

```json
{
  "doc_id": "doc123",
  "file_name": "系统设计说明书.md",
  "content": "文档正文",
  "meta": {
    "source": "local",
    "content_hash": "sha256:xxxx",
    "content_length": 12345,
    "created_at": "2026-03-19T12:00:00Z"
  }
}
```

### 6.5.2 读取原则

- 默认按 `doc_id` 读取；
- 若只需元数据，建议支持轻量读取模式；
- 若文档不存在，应返回 `DOCUMENT_NOT_FOUND`。

## 6.6 update_document() 规则

### 6.6.1 更新边界

允许更新：

- 文档正文
- 文档元数据
- 标签等扩展字段

约束：

- 若更新正文，必须同步更新：
  - `content_hash`
  - `content_length`
  - `updated_at`
- `doc_id` 不应因内容更新而改变（除非采用版本化策略）
- 更新后应由上游索引链路决定是否重建 chunk / vector 索引

### 6.6.2 与索引链路边界

- 本模块不直接重建向量索引；
- 更新正文后，可返回“需要重建索引”的状态提示或由上游监听；
- 不在本模块内直接操作 `vector_db.delete/upsert`。

## 6.7 delete_document() 规则（修订重点）

### 6.7.1 本模块职责

- 删除文档原文；
- 删除文档元数据；
- 清理本模块内部的 hash 映射与索引关系（若有）。

### 6.7.2 与向量索引边界

- 本模块不直接删向量；
- 删除文档后，必须为上游提供明确的 `doc_id` 返回，供索引链路或管理服务进一步调用 `vector_db.delete(doc_id=...)`；
- 系统总体上应实现“删文档 -> 触发删向量”的生命周期管理，但该编排不应写死在本模块内部。

### 6.7.3 返回要求

成功响应建议结构：

```json
{
  "code": "SUCCESS",
  "message": "document deleted",
  "data": {
    "doc_id": "doc123"
  }
}
```

## 6.8 原文与元数据边界（修订重点）

### 6.8.1 原文

原文应保存：

- 标准化正文内容
- 必要时保留原始格式副本路径（可选）

### 6.8.2 元数据

元数据应保存：

- 文件名
- 来源
- 哈希
- 长度
- 时间戳
- MIME 类型
- 扩展标签

### 6.8.3 禁止事项

- 不允许把冗余派生字段在多处重复定义，例如同一 `content_length` 多次写入且可能不一致；
- 不建议把向量检索结果、模型生成结果写回文档元数据作为长期主数据；
- 不建议把 chunk 列表完整嵌入为文档元数据主路径（可选单独索引映射）。

## 6.9 与 chunk / citation 的回溯关系（强制）

### 6.9.1 最低要求

本模块必须保证：

- 存在稳定 `doc_id`
- 文档正文可读取
- 文档内容足以支持 `chunk_id -> doc_id -> 原文区间` 的回溯

### 6.9.2 推荐做法

由 chunking 或索引链路产出：

- `chunk_id`
- `chunk_index`
- `start_char`
- `end_char`

本模块无需直接生成这些字段，但应保证原文存在且可用于定位。

## 6.10 示例实现与生产实现分层说明（强制）

### 6.10.1 示例实现

适用于：

- 本地开发
- 联调验证
- 小规模 PoC

允许简化：

- 使用本地文件系统保存正文
- 使用 JSON / sidecar 文件保存元数据
- 使用本地 hash 映射文件做去重

### 6.10.2 生产实现

必须满足：

- 更可靠的持久化与恢复
- 更稳定的元数据查询能力
- 更好的删除与生命周期管理
- 更适合并发与扩展的存储后端

推荐方案：

- 对象存储 + 元数据库
- 数据库 + 文件系统混合
- 文档数据库 / KV + 对象存储

## 6.11 错误处理与统一返回

### 6.11.1 错误码约定

| 错误码 | 说明 |
| :--- | :--- |
| `SUCCESS` | 执行成功 |
| `DOCUMENT_NOT_FOUND` | 文档不存在 |
| `DOCUMENT_SAVE_FAILED` | 文档保存失败 |
| `DOCUMENT_UPDATE_FAILED` | 文档更新失败 |
| `DOCUMENT_DELETE_FAILED` | 文档删除失败 |
| `PARAM_INVALID` | 输入内容或参数不合法 |
| `UNKNOWN_ERROR` | 未知异常兜底 |

### 6.11.2 返回约束

- 成功与失败响应都建议兼容系统统一响应结构；
- `details` 尽量结构化，如：
  - `doc_id`
  - `file_name`
  - `path`
  - `content_hash`
  - `operation`

# 7. 模块调用示例

## 7.1 创建并保存文档示例

```python
document = document_store.create_document(
    content="系统采用分层架构设计。",
    file_name="系统设计说明书.md",
    source="local"
)

result = document_store.save_document(document)
```

## 7.2 读取文档示例

```python
result = document_store.get_document("doc123")
```

## 7.3 删除文档示例

```python
result = document_store.delete_document("doc123")
```

# 8. 测试规范

## 8.1 测试范围（强制）

| 测试类型 | 测试内容 |
| :--- | :--- |
| doc_id 测试 | `doc_id` 是否稳定生成 |
| create/save 测试 | 文档是否正确创建并落盘 |
| metadata 校验测试 | `file_name / source / content_hash / content_length / created_at` 是否齐全 |
| 去重测试 | 相同 content_hash 是否可识别 |
| get 测试 | 能否按 `doc_id` 正确读取 |
| update 测试 | 更新正文后 hash / length / updated_at 是否同步变化 |
| delete 测试 | 文档删除后原文、元数据、hash 映射是否清理 |
| chunk 回溯测试 | 文档原文是否能支撑 chunk 定位 |
| 统一返回测试 | 返回结构是否兼容系统统一响应 |

## 8.2 Mock 示例

```python
document = {
    "doc_id": "doc123",
    "file_name": "demo.md",
    "content": "系统采用分层架构设计。",
    "meta": {
        "file_name": "demo.md",
        "source": "local",
        "content_hash": "sha256:demo",
        "content_length": 12,
        "created_at": "2026-03-19T12:00:00Z"
    }
}
```

# 9. 模块配置管理

建议配置示例如下：

```yaml
document_store:
  storage_type: "local_fs"
  storage_dir: "./documents"
  metadata_suffix: ".info.json"
  content_suffix: ".txt"
  hash_index_file: ".hash_doc_map.json"
  enable_dedup: true
```

说明：

- `storage_type` 用于区分本地文件、对象存储等实现；
- `storage_dir` 为存储目录；
- `metadata_suffix` 与 `content_suffix` 控制 sidecar 文件格式；
- `hash_index_file` 用于本地 hash 映射；
- `enable_dedup` 控制是否开启哈希去重。

# 10. 交付物清单（强制）

模块开发完成后，需提交以下交付物：

| 交付物 | 说明 |
| :--- | :--- |
| `core/base.py` | 抽象基类，定义文档存储核心接口 |
| `core/impl.py` | 默认文档存储实现 |
| `utils/tool_functions.py` | 哈希、元数据校验、路径与持久化辅助函数 |
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
| 上游调用 | 索引链路或业务模块只能依赖 `BaseDocumentStore` 抽象接口 |
| doc_id 约束 | `doc_id` 必须稳定，不应把文件名当作唯一主键 |
| 元数据约束 | 最低元数据字段必须齐全，且不得重复定义冲突字段 |
| 去重约束 | 推荐使用 `content_hash` 做去重，不依赖文件名去重 |
| 生命周期边界 | 本模块删文档，不直接删向量；删向量由上游生命周期流程负责 |
| 统一结构 | 读写返回结构必须稳定，并与系统统一响应兼容 |

# 12. 常见问题（FAQ）

| 问题 | 说明 |
| :--- | :--- |
| 为什么不能只用文件名作为 `doc_id`？ | 因为文件名可能重复或变化，无法作为稳定主键。 |
| 文档更新后是否必须重建向量索引？ | 通常需要，但由上游索引链路决定并编排，本模块不直接执行。 |
| 为什么 content_hash 很重要？ | 因为它支撑去重、审计、重复导入检测与生命周期管理。 |
| 删除文档为什么不直接删向量？ | 因为本模块职责是文档存储；删向量属于向量库生命周期管理，应由上游统一编排。 |

# 13. 附录：系统错误码关联

本模块直接使用或透传的核心错误码如下：

| 错误码 | 来源 | 适用场景 |
| :--- | :--- | :--- |
| `SUCCESS` | 本模块/下游 | 请求成功 |
| `DOCUMENT_NOT_FOUND` | 本模块 | 文档不存在 |
| `DOCUMENT_SAVE_FAILED` | 本模块 | 文档保存失败 |
| `DOCUMENT_UPDATE_FAILED` | 本模块 | 文档更新失败 |
| `DOCUMENT_DELETE_FAILED` | 本模块 | 文档删除失败 |
| `PARAM_INVALID` | 本模块 | 输入内容或参数不合法 |
| `UNKNOWN_ERROR` | 异常兜底 | 未知运行时异常 |

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
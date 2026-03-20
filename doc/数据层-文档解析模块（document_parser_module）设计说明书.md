# 数据层-文档解析模块（document_parser_module）设计说明书

| 文档版本 | v1.1 |
| :--- | :--- |
| 最后更新 | 2026-03-19 |
| 维护责任人 | 文档解析模块开发负责人 |
| 状态 | 修订版 |

> 本修订版对齐《RAG与Agent系统架构设计说明书》v1.1、文档存储模块修订版、RAG 模块修订版与索引链路规范，重点修正解析输出标准结构、文件类型边界、解析与存储职责分层、降级策略、批量解析规则与统一错误码。

# 1. 文档概述

## 1.1 文档目的

本文档为 RAG 与 Agent 系统数据层-文档解析模块（`document_parser_module`）的独立设计说明书。

本模块负责系统中的文档内容解析能力，是“文件发现 -> 类型识别 -> 文本提取 -> 标准结构输出”的核心数据层模块。模块在系统中的职责包括：

- 接收本地文件或目录输入；
- 识别文件类型并选择合适解析器；
- 从文档中提取可用文本内容；
- 输出统一的标准化文档结构；
- 为文档存储、索引构建和后续 chunking 提供上游输入；
- 通过可替换实现屏蔽不同文件格式解析细节。

本文档作为本模块开发、测试、联调与后续替换实现的唯一标准依据。

## 1.2 适用人群

适用于文档解析模块开发人员、文档存储模块开发人员、索引链路开发人员、测试人员、架构设计人员及后续维护人员。

## 1.3 核心需求回顾

| 需求类型 | 具体要求 |
| :--- | :--- |
| 模块功能 | 提供单文件与目录批量解析能力，输出统一标准结构。 |
| 开发语言 | Python 3.10+，最低 3.10，推荐 3.12，与系统整体保持一致。 |
| 开发模式 | 独立开发、可替换实现、通过抽象接口集成。 |
| 文档要求 | 与系统总设计 v1.1、文档存储 / RAG / 索引链路设计保持一致。 |
| 模块约束 | 本模块不负责 HTTP 协议处理；不负责文档存储落盘；不负责 chunking 与向量化；必须输出统一标准结构并清晰标注来源与文件信息。 |

# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于系统**数据层**，是系统中负责文件内容提取与标准化输出的数据能力模块。

本模块职责如下：

- 接收单个文件路径或目录路径；
- 识别文件扩展名、MIME 类型或解析模式；
- 选择对应解析器提取文本内容；
- 以统一结构输出解析结果；
- 在批量解析场景下返回多条标准结果；
- 记录解析过程中的成功、失败与降级信息。

本模块不负责：

- 不负责 HTTP/HTTPS 协议处理；
- 不负责文档持久化；
- 不负责 chunking；
- 不负责向量化；
- 不负责向量入库；
- 不直接决定 HTTP 状态码。

## 2.2 模块边界

### 2.2.1 本模块负责

- 文件存在性检查；
- 文件类型识别；
- 单文件解析；
- 目录批量解析；
- 统一标准输出；
- 解析失败与降级记录。

### 2.2.2 本模块不负责

- 不负责文档 `doc_id` 生成；
- 不负责文档去重；
- 不负责内容哈希计算作为长期主键；
- 不负责索引构建编排；
- 不负责文档删除或生命周期管理。

## 2.3 依赖关系

### 2.3.1 上游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `index_service / 索引链路` | 触发单文件或目录解析 |
| 管理类服务（可选） | 文件导入、批处理任务等 |

### 2.3.2 下游依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| 第三方解析库（PyPDF2、python-docx、pandas、pptx 等） | 解析不同格式文件 |
| 本地文件系统 | 文件读取 |

### 2.3.3 基础依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `config_module` | 模块配置读取 |
| `log_module` | 解析日志记录 |
| `exception_module` | 异常封装 |
| `common_utils_module` | 通用辅助函数 |

说明：

- 本模块必须通过统一解析接口屏蔽不同文件类型的实现差异；
- 可按文件类型拆分解析器，但输出结构必须统一；
- 若依赖库不可用，应采用清晰的失败或降级策略，而不是静默成功。

# 3. 统一项目结构规范

本模块遵循系统总设计 v1.1 的统一目录规范。

## 3.1 必选目录与文件

```text
document_parser_module/
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

- `parsers/`：按文件类型拆分解析器实现
- `examples/`：解析示例
- `docs/`：补充说明材料
- `test_data/`：测试样例文件

说明：

- 当前阶段可采用 `core/impl.py` 单文件实现；
- 当文件类型支持增多时，建议拆分 `parsers/`；
- 新增扩展目录必须在 `README.md` 中说明职责与边界。

# 4. 核心数据模型设计

## 4.1 ParsedDocument（强制标准结构）

```python
from dataclasses import dataclass, field
from typing import Any, Dict

@dataclass
class ParsedDocument:
    content: str
    file_name: str
    meta: Dict[str, Any] = field(default_factory=dict)
```

说明：

- `content` 为解析出的标准化文本内容；
- `file_name` 为原始文件名；
- `meta` 至少包含来源、路径、文件类型、解析状态等信息；
- 本模块输出必须兼容该结构，即使内部实现不直接使用 dataclass。

## 4.2 ParsedDocument.meta 最低字段规范（强制）

每条解析结果的 `meta` 至少必须包含：

```json
{
  "source_path": "/data/docs/a.md",
  "file_ext": ".md",
  "source": "local",
  "parser": "markdown",
  "parse_status": "success"
}
```

推荐补充：

```json
{
  "mime_type": "text/markdown",
  "encoding": "utf-8",
  "warning": null,
  "raw_size": 10240
}
```

约束：

- `source_path / file_ext / source / parser / parse_status` 缺一不可；
- 若采用降级解析，必须在 `warning` 或等价字段中说明；
- 不应在本模块输出 `doc_id` 作为必填字段，`doc_id` 由文档存储模块负责。

## 4.3 BatchParseResult（可选）

```python
from dataclasses import dataclass, field
from typing import List, Dict, Any

@dataclass
class BatchParseResult:
    items: List[Dict[str, Any]] = field(default_factory=list)
    success_count: int = 0
    failed_count: int = 0
```

说明：

- 批量目录解析可返回聚合结果；
- 也可直接返回 ParsedDocument 列表，但需在文档中说明一致行为。

# 5. 核心接口设计（抽象基类）

## 5.1 BaseDocumentParser

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List

class BaseDocumentParser(ABC):
    @abstractmethod
    def parse_file(self, file_path: str) -> Dict[str, Any]:
        """
        解析单个文件并返回标准结构
        """
        pass

    @abstractmethod
    def parse_folder(self, folder_path: str) -> List[Dict[str, Any]]:
        """
        批量解析目录中的文件
        """
        pass
```

约束：

- `parse_file()` 必须返回统一标准结构；
- `parse_folder()` 返回的每一项都必须与 `parse_file()` 输出结构一致；
- 接口只负责解析，不负责持久化和索引编排。

# 6. 核心实现设计（标准文档解析实现）

## 6.1 类职责说明

标准文档解析实现负责：

- 接收文件或目录路径；
- 校验路径合法性与存在性；
- 根据文件类型分派解析逻辑；
- 返回统一标准结构；
- 记录解析日志、失败原因与降级信息。

本实现必须保持：

- 无 HTTP 依赖；
- 输出结构稳定；
- 文件类型边界清晰；
- 失败时不静默吞掉异常；
- 不在本模块生成 `doc_id` 或直接落盘到文档存储。

## 6.2 parse_file() 规则（强制）

### 6.2.1 输入要求

- `file_path` 必须存在；
- 必须为文件而非目录；
- 必须能识别扩展名或 MIME 类型；
- 对不支持的类型应返回明确错误。

### 6.2.2 输出要求

返回结构至少包含：

```json
{
  "content": "解析出的文本内容",
  "file_name": "系统设计说明书.md",
  "meta": {
    "source_path": "/data/docs/系统设计说明书.md",
    "file_ext": ".md",
    "source": "local",
    "parser": "markdown",
    "parse_status": "success"
  }
}
```

### 6.2.3 禁止事项

- 不允许返回裸字符串作为解析结果；
- 不允许仅返回 `content` 而缺少 `file_name / meta`；
- 不允许解析失败后仍标记为 success。

## 6.3 parse_folder() 规则（强制）

### 6.3.1 输入要求

- `folder_path` 必须存在且为目录；
- 只遍历白名单支持文件类型，或明确可配置；
- 可按是否递归扫描作为配置项控制。

### 6.3.2 行为要求

- 逐个文件调用单文件解析逻辑；
- 每个文件的成功与失败都应记录；
- 不因单个文件失败而中断整个目录解析（除非配置强制 fail-fast）。

### 6.3.3 返回要求

可选两种方式，但必须在实现中保持一致：

1. 直接返回成功解析的文档列表；
2. 返回包含成功/失败统计的聚合结构。

当前推荐：

- 主返回值为成功解析的标准文档列表；
- 失败信息通过日志或附加 summary 提供。

## 6.4 文件类型支持矩阵（修订重点）

### 6.4.1 建议支持类型

建议首批支持：

- `.txt`
- `.md`
- `.json`
- `.csv`
- `.xml`
- `.pdf`
- `.docx`
- `.pptx`
- `.xlsx` / `.xls`
- `.py`

### 6.4.2 支持策略

- 纯文本类：直接按编码读取并清洗；
- 结构化文本类（json/xml/csv）：尽量提取可读文本内容；
- Office/PDF 类：调用对应第三方库解析；
- 不支持类型：返回 `UNSUPPORTED_FILE_TYPE`。

### 6.4.3 说明

- “支持”不等于完美解析，复杂格式允许降级；
- 降级时必须记录 parser 名、warning 与 parse_status；
- 若依赖库缺失，不应伪装为成功。

## 6.5 降级策略（修订重点）

### 6.5.1 可接受的降级

例如：

- PDF 无法提取复杂表格时，仅提取正文文本；
- Office 文件中图片内容未做 OCR 时，仅提取文本部分；
- XML/JSON 以结构化字符串或扁平文本形式输出。

### 6.5.2 必须记录的字段

发生降级时，`meta` 中应包含：

```json
{
  "parse_status": "partial_success",
  "warning": "table extraction skipped"
}
```

### 6.5.3 禁止事项

- 不允许 silently fallback 却不记录 warning；
- 不允许解析失败仍然返回空 content 且标记 success。

## 6.6 文本清洗边界

### 6.6.1 本模块可做的清洗

- 去除 BOM；
- 统一换行；
- 去除明显空白噪声；
- 对读取失败编码做合理 fallback（如 utf-8 -> gbk）。

### 6.6.2 本模块不应做的事

- 不应在本模块做 chunking；
- 不应做语义级摘要；
- 不应静默截断长文档；
- 不应把内容“美化重写”为摘要文本。

## 6.7 与文档存储模块的边界（强制）

本模块与 `document_store_module` 的关系如下：

- 本模块负责“解析得到标准化文档内容”；
- 文档存储模块负责“生成 doc_id、保存原文与元数据、去重与生命周期管理”；
- 本模块输出中不强制要求 `doc_id`；
- 上游索引链路应先 `parse_file()`，再调用 `document_store.create_document()/save_document()`。

## 6.8 与索引链路的边界（强制）

本模块与索引构建链路关系如下：

- 本模块只负责 parser 阶段；
- 不直接负责 chunking；
- 不直接调用 embedding；
- 不直接写 vector_db；
- 正式链路应为：`parse -> store -> chunk -> embed -> upsert`。

## 6.9 错误处理与统一返回

### 6.9.1 错误码约定

| 错误码 | 说明 |
| :--- | :--- |
| `SUCCESS` | 执行成功 |
| `DOCUMENT_NOT_FOUND` | 文件不存在 |
| `FOLDER_NOT_FOUND` | 目录不存在 |
| `UNSUPPORTED_FILE_TYPE` | 不支持的文件类型 |
| `DOCUMENT_PARSE_FAILED` | 文档解析失败 |
| `PARAM_INVALID` | 输入参数不合法 |
| `UNKNOWN_ERROR` | 未知异常兜底 |

### 6.9.2 返回约束

- 成功解析结果建议直接返回标准文档结构；
- 失败时应返回兼容系统统一响应结构或抛出标准异常；
- `details` 尽量结构化，如：
  - `path`
  - `file_ext`
  - `parser`
  - `reason`

# 7. 模块调用示例

## 7.1 单文件解析示例

```python
result = parser.parse_file("/data/docs/系统设计说明书.md")
```

期望返回：

```json
{
  "content": "系统采用分层架构设计。",
  "file_name": "系统设计说明书.md",
  "meta": {
    "source_path": "/data/docs/系统设计说明书.md",
    "file_ext": ".md",
    "source": "local",
    "parser": "markdown",
    "parse_status": "success"
  }
}
```

## 7.2 目录解析示例

```python
results = parser.parse_folder("/data/docs")
```

# 8. 测试规范

## 8.1 测试范围（强制）

| 测试类型 | 测试内容 |
| :--- | :--- |
| parse_file 测试 | 单文件解析是否返回标准结构 |
| parse_folder 测试 | 批量解析是否正常工作 |
| 文件不存在测试 | 文件不存在时是否返回 `DOCUMENT_NOT_FOUND` |
| 目录不存在测试 | 目录不存在时是否返回 `FOLDER_NOT_FOUND` |
| 文件类型测试 | 支持类型是否可解析；不支持类型是否返回 `UNSUPPORTED_FILE_TYPE` |
| 降级测试 | partial_success 场景是否正确记录 warning |
| 结构一致性测试 | parse_file / parse_folder 输出结构是否一致 |
| 编码测试 | 不同编码文本文件是否能合理解析或明确失败 |
| 统一返回测试 | 错误结构是否清晰、可定位 |

## 8.2 Mock 示例

```python
result = {
    "content": "系统采用分层架构设计。",
    "file_name": "demo.md",
    "meta": {
        "source_path": "/tmp/demo.md",
        "file_ext": ".md",
        "source": "local",
        "parser": "markdown",
        "parse_status": "success"
    }
}
```

# 9. 模块配置管理

建议配置示例如下：

```yaml
document_parser:
  recursive: false
  supported_extensions:
    - ".txt"
    - ".md"
    - ".json"
    - ".csv"
    - ".xml"
    - ".pdf"
    - ".docx"
    - ".pptx"
    - ".xlsx"
    - ".xls"
    - ".py"
  fail_fast: false
  encoding_fallbacks:
    - "utf-8"
    - "gbk"
```

说明：

- `recursive` 控制目录解析是否递归；
- `supported_extensions` 为白名单；
- `fail_fast` 控制是否单文件失败即中断批处理；
- `encoding_fallbacks` 控制文本读取编码降级顺序。

# 10. 交付物清单（强制）

模块开发完成后，需提交以下交付物：

| 交付物 | 说明 |
| :--- | :--- |
| `core/base.py` | 抽象基类，定义文档解析核心接口 |
| `core/impl.py` | 默认文档解析实现 |
| `utils/tool_functions.py` | 文件类型识别、路径校验、文本清洗辅助函数 |
| `config/config.py` | 模块配置读取逻辑 |
| `tests/test_impl.py` | 核心测试用例 |
| `README.md` | 模块说明文档 |
| `requirements.txt` | 依赖包清单 |

可选扩展交付物（按复杂度选择）：

- `parsers/*`
- `examples/*`
- `docs/*`
- `tests/test_data/*`

若使用可选扩展目录，必须在 `README.md` 中说明职责与边界，并纳入测试覆盖。

# 11. 可替换性约束

| 约束项 | 说明 |
| :--- | :--- |
| 上游调用 | 索引链路或导入服务只能依赖 `BaseDocumentParser` 抽象接口 |
| 输出结构 | 所有解析器实现都必须输出统一标准结构 |
| 失败语义 | 不支持的类型、解析失败、部分成功必须区分明确 |
| 存储边界 | 本模块不负责 doc_id 生成与文档落盘 |
| 索引边界 | 本模块只负责 parser，不负责 chunk / embed / upsert |
| 统一结构 | 输出与错误信息必须保持稳定，可供后续模块直接消费 |

# 12. 常见问题（FAQ）

| 问题 | 说明 |
| :--- | :--- |
| 为什么解析模块不直接生成 `doc_id`？ | 因为 `doc_id` 属于文档存储主键，应由文档存储模块统一生成与维护。 |
| 为什么解析模块不直接做 chunking？ | 因为 chunking 属于索引构建与检索规范的一部分，应与解析职责分离。 |
| 降级解析是否允许？ | 允许，但必须显式记录 `parse_status=partial_success` 与 `warning`。 |
| 为什么不能把失败文件静默跳过？ | 因为这会影响批处理可观测性与排障，必须明确记录失败原因。 |

# 13. 附录：系统错误码关联

本模块直接使用或透传的核心错误码如下：

| 错误码 | 来源 | 适用场景 |
| :--- | :--- | :--- |
| `SUCCESS` | 本模块/下游 | 请求成功 |
| `DOCUMENT_NOT_FOUND` | 本模块 | 文件不存在 |
| `FOLDER_NOT_FOUND` | 本模块 | 目录不存在 |
| `UNSUPPORTED_FILE_TYPE` | 本模块 | 不支持的文件类型 |
| `DOCUMENT_PARSE_FAILED` | 本模块 | 文档解析失败 |
| `PARAM_INVALID` | 本模块 | 输入路径或参数不合法 |
| `UNKNOWN_ERROR` | 异常兜底 | 未知运行时异常 |

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
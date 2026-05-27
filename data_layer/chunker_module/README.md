# chunker_module

索引构建阶段的文本切分工具(实现文档第 11 章 Chunking 规范)。

## 职责

把"已解析的文档内容"切成符合 RAG 检索粒度要求的 chunk 列表,供下游
embedding/向量库使用。本模块**不解析原始文件**(那是 document_parser_module
的职责),只对纯文本做切分。

## 入口

| 函数 | 用途 |
|---|---|
| `chunk_document(doc_id, content, file_name, ...) -> List[Chunk]` | 主入口,把整篇文档切成 chunk 列表 |
| `build_upsert_items(chunks, vectors) -> List[Item]` | 把 chunks + vectors 组合成符合 `BaseVectorDB.upsert_vectors` 契约的 items |
| `split_by_natural_boundaries(text)` | 内部使用,按 Markdown 标题 → 段落 → 句号优先级切 |
| `normalize_text(text)` / `estimate_tokens(text)` | 工具函数 |

## 切分策略

按文档 11.3 节顺序执行:
1. **Markdown 标题**: 找到 `# / ## / ### ...` 行,按节切
2. **段落空行**: 没有标题时按 `\n\n` 切
3. **句号/分号**: 都没有时按句子切
4. **滑动窗口降级**: 单元仍超 `max_chunk_size_tokens` 时按字符滑窗,保留 overlap

## 默认参数

| 参数 | 默认 | 推荐范围 |
|---|---|---|
| `chunk_size_tokens` | 400 | 300-600 |
| `chunk_overlap_tokens` | 80 | 60-120 |
| `max_chunk_size_tokens` | 800 | 硬上限 |
| `min_chunk_size_tokens` | 80 | 太短的块会自动合并到上一块 |

## 强制字段

每个 chunk 必须包含(见文档 11.1):
```
{
  "doc_id": "...",
  "chunk_id": "{doc_id}#c000001",  # 6 位零填充,稳定可复现
  "content": "...",
  "meta": {
    "file_name", "source", "chunk_index",
    "start_char", "end_char", "token_count_est"
  }
}
```

`vector_id == chunk_id`(见文档 11.5),确保引用回溯稳定。

## 历史位置

本模块从 `run/chunking_utils.py` 抽取而来。原位置违反"统一模块结构"规范,
现按数据层模块标准重组(`__init__.py + chunker.py + tests/ + README.md`),
对外 import 路径从 `from chunking_utils import ...` 改为 `from chunker_module import ...`。

## 设计偏离

本模块同 [schema_module](../../basic_support/schema_module/README.md) /
[deps_module](../../basic_support/deps_module/README.md),**有意偏离**架构规范中
"core/base.py + impl.py" 二分结构 —— chunker 是无状态算法函数集,
没有抽象/实现二分需求。若未来要支持多种切分策略(代码/表格特化),可在本模块内
加 `strategies/` 子目录或引入 `BaseChunker` ABC,届时再演进。

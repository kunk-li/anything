# vector_db_module（数据层 - 向量数据库模块）

本模块为 RAG/Agent 系统的数据层核心模块之一，提供 **向量 upsert / query / delete** 的统一接口，并内置 **FAISS 本地示例实现**，便于开发/测试环境快速落地。

## 功能

- **upsert_vectors**：批量插入/更新向量
- **query**：相似度检索，返回统一格式结果
- **delete**：删除向量（FAISS Flat 示例实现不支持删除，调用会抛出异常）

## 目录结构

```
vector_db_module/
├── __init__.py
├── core/
│   ├── base.py
│   └── impl.py
├── utils/
│   └── tool_functions.py
├── config/
│   └── config.py
├── tests/
│   └── test_impl.py
├── requirements.txt
└── README.md
```

## 依赖

- `config_module`：读取全局配置（向量维度、存储目录、向量库类型等）
- `log_module`：记录运行日志（可选，若环境缺失则自动降级）
- `exception_module`：抛出标准化异常（VectorDBException）
- `faiss-cpu`：FAISS 示例实现依赖

## 配置（示例）

全局配置（由 `config_module` 加载的 config.yaml）中建议包含：

```yaml
vector_db:
  type: "faiss"
  vector_dimension: 768
  local_dir: "vector_store"
```

## 使用示例

```python
from vector_db_module import get_vector_db

db = get_vector_db()

db.upsert_vectors([
    {"vector_id": "v1", "embedding": [0.1]*768, "metadata": {"doc_id":"d1","chunk_id":"d1#c1"}},
    {"vector_id": "v2", "embedding": [0.2]*768, "metadata": {"doc_id":"d2","chunk_id":"d2#c1"}},
])

hits = db.query([0.1]*768, top_k=5, filter={"doc_id": "d1"})
print(hits)
```

## 注意事项

- **向量维度必须一致**：embedding 维度需与 `vector_db.vector_dimension` 一致，否则会抛出 `VectorDBException(code="VECTOR_INSERT_FAILED")`。
- **FAISS Flat 不支持删除**：示例使用 `IndexFlatIP`，无法 delete。生产可替换为：
  - 支持删除的 FAISS 结构（如 `IndexIDMap` + `remove_ids`，或其他可删除索引）
  - 外部向量库（Milvus / Pinecone / Chroma 等）
- **相似度得分范围**：本实现将余弦相似度 `[-1,1]` 映射为 `[0,1]` 返回，便于上层模块统一使用。

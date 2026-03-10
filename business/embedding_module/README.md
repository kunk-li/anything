# embedding_module

Embedding 模块用于将单条或批量文本转换为标准化向量，供 RAG 模块、向量数据库模块直接使用。

## 1. 模块能力

- 单条文本向量化
- 批量文本向量化
- 向量归一化
- 本地模型（STEmbedding）
- 远程模型（LLMEmbedding）
- 统一请求/响应模型
- 统一异常处理

## 2. 目录结构

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
└── README.md
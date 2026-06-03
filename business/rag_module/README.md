# RAG 模块 (rag_module)

## 1. 模块功能
本模块实现检索增强生成（RAG）全流程，包括：
- 用户问题向量化
- 向量数据库检索
- 原文片段获取
- Prompt 拼接与大模型生成
- 标准化响应输出

## 2. 项目结构
遵循系统统一规范，包含 core, prompt, utils, config, tests 目录，外加 `extensions/`。

> **`extensions/` 偏离声明**：检索链路增强组件（`rewriter.py` 的 `LLMQueryRewriter`、`reranker.py` 的 `LLMReranker` / `CrossEncoderReranker`、`BM25Retriever`）采用单文件直接包含 Base + 默认实现，不强制 `core/base.py + impl.py` 二分（小组件二分冗余）。详见架构总览 §3 设计偏离声明。

## 3. 核心接口
- `BaseRAG`: 抽象基类
- `SimpleRAG`: 默认实现类
- `run(query, top_k)`: 快速执行全流程
- `call_rag(request)`: 标准化接口调用

## 4. 使用示例
```python
from rag_module.core.impl import SimpleRAG
from llm_adapter_module.core.impl import LLMService

# 初始化
llm = LLMService()
rag = SimpleRAG(llm_client=llm)

# 调用
result = rag.run("什么是 RAG？", top_k=5)
print(result["data"]["answer"])
---
name: rag_query
description: RAG 检索类问题的提示
triggers:
  - 文档
  - 知识库
  - 检索
  - RAG
  - 这份文件
  - 在哪里写
priority: 5
tools:
  - rag_search
---

# RAG 检索任务

用户问的是文档/知识库相关问题, 优先走 rag_search 工具:

- 把用户原话作 query, top_k 默认 5 (问题宽泛可加到 10)
- 拿到 chunks 后, 答案严格基于上下文, 不要编造
- 用 `[CIT:doc_id#chunk_id]` 标引用, 让用户能跳预览
- 如果检索没命中相关 chunk, 老实说 "知识库里没找到相关内容", 不强答

避免:
- 多轮反复检索同一个查询 (浪费 token, 1 轮足够)
- 把已经在 chunk 里的事实再调 llm_generate 重述

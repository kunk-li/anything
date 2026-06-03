# 设计文档索引与状态说明

> **重要：当设计文档与代码冲突时，以代码为准。**
> 真相源优先级：`core/base.py`（接口契约） > `scripts/check_abc_alignment.py`（签名守护，19 对 base↔impl 全绿） > `CHANGELOG.md` > 本目录的设计说明书。

本项目经历了 #1 → #167 上百个迭代任务，部分设计文档（多停留在 v1.1 / 2026-03）滞后于代码实现。2026-06-03 的全项目审计（AUDIT）对文档做了校准，结论如下。

## 文档状态总览

| 文档 | 状态 | 说明 |
|---|---|---|
| RAG与Agent系统架构设计说明书 | ✅ 已校准 (AUDIT-3) | 模块清单补全横切模块 + Embedding，删除不存在的"接口封装模块" |
| 应用层-控制台交互模块 | ✅ 已重写 (AUDIT-3) | 原文件内容曾错挂为 common_utils 文档，已按实际代码完整重写 |
| 数据层（vector_db / document_store / state_store / llm_adapter / document_parser） | ⚠️ 部分滞后 | 主干流程准确；但**错误码命名**、部分**数据模型字段**、llm_adapter 的 `mode` vs `request_type` 命名与代码有偏差 → 以 `base.py` / `impl.py` 为准 |
| 核心业务层（rag / agent / embedding / orchestrator） | ⚠️ 部分滞后 | 实现质量高、能力齐全；但 doc 把**已默认开启**的能力（混合检索 / ReAct / Reflection / 长期记忆 / 流式）写成"未来可选"；embedding 的 `EmbeddingRequest/Response` 字段与代码不同 |
| 基础支撑层（config / log / exception） | ⚠️ ABC 描述滞后 | doc 第 4/5 章的抽象接口名与实际 `base.py` 不一致（例：config doc 写 `reload` / `set_config`，实际是 `load_config` / `update_config`；log doc 写 `get_audit_logger`，实际未实现）→ 以 `base.py` 为准 |
| 应用层-API服务模块 | ⚠️ 路由清单滞后 | 实际端点远多于 doc（新增 `/memory` `/scheduler` `/sessions` `/kb` `/feedback` 等域）；`/eval/run` 为 doc 规划但未实现；CORS 中间件 doc 提及但未接 |
| 通用工具模块（common_utils） | ✅ 基本一致 | 仅文档结构图未反映 OO 重构后的 utils/ 拆分（小滞后） |

## 已知偏差的真相源

- **接口契约**：所有 `BaseXxx` ABC 以各模块 `core/base.py` 为准，且被 `scripts/check_abc_alignment.py` 守护（当前 19 对 base↔impl 全部对齐）。
- **横切模块**（hooks / skills / quota / audit / state_backend / long_term_memory / project_memory / observability / schema / deps / chunker）：后期增补，暂无独立设计说明书 → 以代码 + `CHANGELOG.md` 为准。
- **错误码**：各模块实际错误码以 `impl` 抛出为准（历史 doc 的错误码表部分未同步）。
- **`run/` 启动层**：实际启动入口在 `run/`（bootstrap + factories + main_*），架构文档附录的仓结构映射未列出。

## 设计文档清单

**基础支撑层**：通用工具 / 配置管理 / 日志 / 异常处理
**数据层**：文档解析 / 文档存储 / 向量数据库 / 大模型对接 / 状态存储
**核心业务层**：RAG / Agent / Embedding / 协同调度
**接口层**：请求响应处理
**应用层**：API服务 / 控制台交互
**总览**：RAG与Agent系统架构设计说明书

> 横切模块、`docs/`（configuration-priority / development-setup / secrets-management / multi-tenancy-design）等补充文档另见对应目录。

---

**深度逐份回灌**（把上述 ⚠️ 文档逐章对齐代码）是一个独立的大工程，建议按需分模块进行；在那之前，本页 + `base.py` + `check_abc_alignment.py` 已能保证"不被过时文档误导"。

# Anything — Project Memory

这个文件被 Agent / RAG 在每次执行前注入到 prompt 顶部 (见
`basic_support/common_utils_module/utils/project_memory.py`). 写在这里的内容会
影响 LLM 的回答风格、工具选择、术语用法。

## 项目定位

Anything 是一个 Python 3.12 的 RAG + Agent 平台:
- **RAG**: 文档检索 + LLM 合成回答, 支持向量 + BM25 混合检索 (RRF 融合)
- **Agent**: ReAct 模式多轮工具调用, 支持 11+ 内置工具
- **应用层**: FastAPI HTTP + WebSocket 流式 + Web UI + Console UX
- **多租户**: tenant_id 隔离向量库 / 文档库 / 会话状态

## 架构 (5 层)

```
basic_support/   配置 / 日志 / 异常 / 依赖 / 通用工具 / observability / schema
                 + (v2.x) hooks / skills / quota / audit / project_memory / state_backend
data_layer/      向量库 (FAISS) / LLM 适配器 / 文档存储 / 文档解析 / Embedding / 会话存储
business/        RAG / Agent / Orchestrator / Embedding 业务
interface/       RequestHandler — 边界标准化 + 异常包装
application/     ApiService (FastAPI) / ConsoleApp
```

调用方向: application → interface → business → data_layer → basic_support

**v2.x 架构变更**: KK→YY 共 14 项重构 (god class 拆 mixin / cross-cutting 提模块 /
DI 字段扩展 / API 加 /v1 alias / StateBackend 跨进程抽象). 详见 `CHANGELOG.md`.

## 关键约定

1. **统一信封**: 所有响应都是 `{code, message, data, trace_id, retryable, details, cost_time}`.
   错误码看 `basic_support/schema_module` 或文档 12 章. SUCCESS 之外都视为失败.

2. **5 层不许跨层**: business 不能 import application, interface 不能 import application.
   每个 module 都是 `core/` (base.py + impl.py) + `model/` (data_model.py) 的结构.

3. **DI 优先**: 所有 module 接受 `deps: BasicDeps` 参数, 通过 `build_basic_deps()` 注入.
   不要在 module 内部 new 配置/日志/异常处理器.
   - v2.x (PP #76): `BasicDeps` 现在也带 7 个 cross-cutting registry —
     `hook_registry` / `skill_registry` / `quota_guard` / `audit_logger` /
     `project_memory` / `usage_tracker` / `health_tracker`. 优先走 `deps.X`,
     fallback `get_X()` 全局单例 (back-compat 路径).

4. **trace_id / session_id 单点生成**:
   - `trace_id` 由 ApiService middleware (或 ConsoleApp 入口) 生成
   - `session_id` 由 RequestHandler._standardize_request 补齐
   - business 层只透传, 不重新生成 (除非检测到上游漏传, 走 WARN)

5. **DEV_MODE**: 设 `ANYTHING_DEV_MODE=1` 让 secrets 缺失不挂, 适合本地. 生产部署不设.

6. **API 版本化** (VV #82): 所有 API 路由除了老 path, 都有 `/v1/<path>` 镜像别名.
   新调用方推荐用 `/v1/<path>`. 老 path 无限期保留. 排除清单: `/`, `/ui`,
   `/health`, `/healthz`, `/openapi.json`, `/docs`, `/redoc`.

7. **JSON 日志** (UU #81): 设 `ANYTHING_LOG_FORMAT=json` 让 SystemLogger 输出
   每行一个 JSON object (含 ts / level / message + extras + 自动注入 tenant_id /
   trace_id), 适合 ELK / Datadog ingest.

8. **跨进程 state** (TT #80, XX #84): UsageTracker 接 `backend=SqliteBackend(path)`
   让 gunicorn 多 worker 跨进程共享 token 计数. 其他 tracker 待 Phase 3 接入.

## 术语速查

| 名 | 含义 |
|---|---|
| chunk | 文档切片, 含 chunk_id / doc_id / file_name / content / start_char / end_char |
| citation | 引用标记, 形如 `[CIT:doc_id#chunk_id]` |
| RRF | Reciprocal Rank Fusion, BM25 + 向量两路检索结果融合公式 |
| ReAct | Reason + Act, Agent 多轮思考-动作-观察循环 |
| envelope | 统一响应信封, 顶层 dict 标准结构 |
| tenant | 租户, 通过 X-API-Key 解析或 body.tenant_id 显式传 |

## 回答偏好

- 中文为主, 技术名词保留英文 (RAG / Agent / FAISS / ReAct ...)
- 引用文档时尽量带 chunk_id 让用户能跳转预览
- 不要在没有上下文时编造细节; 答不出来就说"上下文不足"
- 如果检测到 trace_id, 在回答末尾附 `trace_id: xxx` 方便排查

## Agent 工具偏好

- 优先用 `rag_search` 拿上下文再回答; 工具调用尽量少 (≤ 3 轮)
- 涉及代码或文件操作时, 走 `py_sandbox` 不要直接 exec
- `http_request` 默认带 timeout, 不调外网生产 API
- `image_describe` 走 qwen-vl-plus, 不调 OpenAI gpt-4o-mini (除非用户显式指定)

## 当前已实现的能力快照

- ✅ Tasks #1-#85 全部完成 (含 RAG / Agent / 流式 / 混合检索 / 多租户 / 评测 / Docker;
  v2.x 架构优化 KK-YY 14 项 — 见 `CHANGELOG.md`)
- ✅ 14 个 Agent 工具: rag_search, llm_generate, web_search, calculator, code_lint,
  email_send, image_describe, weather, currency, py_sandbox, file_read, file_write,
  http_request, json_query
- ✅ Web UI: RAG / Agent / Hybrid 三 mode + 流式 + 拖拽附件 + 多文件批量 + Admin 面板
- ✅ Console UX (Task T): /mode /topk /attach /history /export 命令模式

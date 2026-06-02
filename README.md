# Anything — RAG + Agent platform

> 万物-一切皆有可能 · An open-source RAG-and-Agent stack with 25 tools, multi-tenant
> sessions, plan/reflect modes, hooks/skills, and a vanilla-JS frontend.

## 快速开始

```bash
# 1. 装依赖
pip install -r requirements.txt

# 2. 复制 env 模板, 填 API key
cp .env.example .env
# 编辑 .env: DASHSCOPE_API_KEY=sk-xxx (用通义千问的话) 等

# 3. 启动 (零配置, dev sentinel API key 会自动关 auth)
python -m application.api_service_module.bootstrap
# 默 http://127.0.0.1:8000 — 浏览器打开就能用
```

## 能力

### Agent 工具 (25 个)

| 类别 | 工具 |
|-----|------|
| 知识检索 | `rag_search` `wikipedia` `web_search` `http_get` `sql_query` `browser_visit` |
| 计算/转换 | `calculator` `datetime` `currency_convert` |
| 文本处理 | `regex_extract` `text_stats` `json_query` `code_lint` |
| 文件读写 | `document_read` `file_write` `pdf_read` `excel_read` |
| 系统执行 | `shell_exec` `py_sandbox` |
| LLM/生成 | `llm_generate` `image_describe` `image_generate` `spawn_subagent` |
| 通讯外部 | `email_send` `weather` |

### 三种 chat 模式

- **RAG**: 在已索引文档里检索, 返回带引用的答案
- **Agent**: ReAct 多轮工具调用 (max_iterations 默 15, 可调)
- **Hybrid**: 检索 + 推理结合

### 高级特性

- **Plan mode** — Agent 先输出计划等审批再执行 (Claude Code 风格)
- **Reflection** — 答案后跑 critique → revise 二阶段 (Reflexion / o1 风格)
- **流式输出** — token-level WebSocket streaming
- **长期记忆** — 自动抽事实 / 语义查重 / pin / search
- **多会话并行** — per-session inflight, 切会话不中断生成
- **多租户** — per-tenant 配额 / 鉴权 / 隔离
- **Hooks / Skills** — 启动期注入扩展逻辑
- **任务模板** — 输入存 IndexedDB, 一键复用

## 架构

四层 + 1 应用 (见 `doc/` 详设):

```
application/        # api_service_module (FastAPI), console_app_module
business/           # rag_module, agent_module, embedding_module, ...
data_layer/         # vector_db, document_store, state_store, llm_adapter, ...
basic_support/      # common_utils, log, config, deps, schema, ...
interface/          # request_response_module (pydantic schemas)
```

DI 走 `BasicDeps` (factories 在 `business/*/factories/`), ABC 钉死接口.

## 开发

```bash
# 单元测试
python -m pytest                           # ~580 测试, 应全绿
python -m pytest -k <pattern>              # 部分跑

# 业务质量评测 (需启动服务)
python evaluation/run_eval.py              # 自动扫 datasets/*.jsonl

# Docker
docker-compose up                          # 服务 + 必要后端

# 前端 (静态文件已嵌入 FastAPI, 无需单独构建)
# http://127.0.0.1:8000/  直接打开
```

## 关键文档

- [DEPLOYMENT.md](./DEPLOYMENT.md) — 生产上线 checklist (安全 / 配额 / 冒烟验证)
- [doc/](./doc/) — 模块设计说明书 (v2.0+)
- [CHANGELOG.md](./CHANGELOG.md) — 按 task ID 倒序的变更
- [AGENTS.md](./AGENTS.md) — Agent system prompt / 项目级记忆
- [evaluation/README.md](./evaluation/README.md) — 评测格式

## 凭据安全

- `.env` 必填: 至少一个 LLM provider key (DashScope / OpenAI / Anthropic / Ollama 本地)
- API key 视为密码: 不要 commit 到 git, 不要贴聊天里
- dev sentinel 模式 (`API_KEY_1=dev_api_key_1_change_in_prod`) 自动关 auth 便于本地试

## License

见 [LICENSE](./LICENSE).

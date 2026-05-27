# Anything Web UI

零构建 vanilla HTML/CSS/JS 前端,跟后端 `application/api_service_module` 同源运行。

## 文件结构

```
frontend/
├── index.html            主页 (聊天 + 侧栏)
├── static/
│   ├── style.css         样式 (dark theme, 单文件)
│   ├── api.js            API 客户端 (fetch 封装)
│   └── app.js            UI 逻辑 (vanilla JS, 状态在 localStorage)
└── README.md             本文件
```

## 启动

`ApiService` 启动期会自动挂载本目录:
- `GET /`           → `index.html`
- `GET /ui`         → 同上 (别名)
- `GET /static/*`   → 静态资源

启动 API + UI:

```bash
# 本地开发
cd run/
ANYTHING_DEV_MODE=1 uvicorn main_api:app --host 0.0.0.0 --port 8000 --reload

# 浏览器打开 http://localhost:8000/
```

启动后控制台会有 INFO 日志:
```
Web UI 已挂载: GET / 提供 index.html, 静态资源 from .../frontend/static
```

如果看到 `未找到 frontend/index.html, 跳过 Web UI 挂载`,说明工作目录不对,
回到 repo root 启动或检查 `_mount_frontend()` 的 candidates 路径。

## 功能

| 区域 | 说明 |
|---|---|
| 顶部 tenant 输入 | 透传到 `body.tenant_id` (内部白名单 IP 才生效, 见下) |
| 顶部健康灯 | 30s 自动 poll `/healthz` |
| ⚙ 设置抽屉 | API base / X-API-Key / session_id 持久化到 localStorage |
| 聊天区 | RAG / Agent / Hybrid 三种模式; Ctrl+Enter 发送 |
| 检索结果侧栏 | 显示最近一次 `data.retrieved_chunks` (score / file_name / content) |
| 工具调用侧栏 | 显示最近一次 Agent `data.steps` (tool_name / input_data) |
| 系统指标侧栏 | `/metrics` Prometheus 文本 |
| 文档上传侧栏 | `POST /documents/upload` 拖拽或选择文件; 之后可触发索引构建 |
| Citations 链 | 答案下方的引用 chip, 点击跳转到侧栏对应 chunk |
| 错误处理 | code != SUCCESS 时显示错误码 + 可重试按钮 (retryable=true) |

## 关于 tenant_id 透传

后端 `ApiService._reconcile_tenant_id` 规则 (见 `docs/multi-tenancy-design.md` §4.3):
- 走 X-API-Key 认证时,API key 绑定的 tenant 永远胜
- 没认证 + 不在 `security.internal_whitelist` 的 IP -> body tenant_id 被剥除

默认 `config.yaml` 已把 `127.0.0.1` / `localhost` / `::1` 加入白名单,本地开发
直接通过 tenant 输入框选 tenant 就行;**生产部署面向公网时必须把白名单清空或
改为内网网段**,否则任何客户端都能伪造 tenant_id。

## 不依赖外部 CDN

所有资源同源服务,即使断网也能跑 (不需要 unpkg/jsdelivr)。无构建步骤,
直接编辑 `static/app.js` / `style.css` 刷新页面即可。

## 后续可扩展

- WebSocket 流式回答 (当前是一次性 JSON 响应)
- Markdown / 代码高亮渲染 (当前 answer 是 `<pre>` 风格 plain text)
- chunk 跳转回原文件预览 (需要 `GET /documents/{doc_id}/preview` 端点)
- 多语言切换 (i18n)
- 移动端布局适配

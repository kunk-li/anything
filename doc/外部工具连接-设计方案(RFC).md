# 外部工具连接 — 设计方案 (RFC)

| 状态 | **Stage1+2 + 增量 已实现** (2026-06-03) — HTTP 连接器 + MCP(stdio+HTTP) + OpenAPI 自动生成 + 真实 server 集成测试 |
|---|---|
| 背景 | agent 缺失同外部工具的连接 (用户提出; 对标 codex/claude 验证为最大短板) |
| 决策人 | 用户 |

> **进度**: **Stage1 (HTTP/OpenAPI 连接器) 已落地** (commit `33dcc6b`) — `business/agent_module/tools/external/`
> (`ExternalToolProvider` 抽象 + `HttpToolProvider` + `make_http_tool` + 工厂接线), 复用 SSRF 防御 + 默认审批,
> 配置 `agent.external_tools` 声明; test_external_tools 13 例。取的默认决策见 §8 下方批注。
> **Stage2 (MCP stdio 客户端) 已落地** (`e08280e`) — 参考 codex/claude code 的标准 MCP 协议
> (JSON-RPC 2.0 over stdio), **最小自实现无 `mcp` SDK 依赖**; `McpStdioClient` + `McpToolProvider` 接同一抽象,
> 配置 `agent.mcp_servers`, 工具命名空间 `server.tool`, 默认审批, fail-safe; test_mcp_tools 10 例。
> SSE/HTTP 传输留后续。

---

## 1. 现状 (ground truth, 已核对代码)

- **工具模型**: 工具是简单 callable `func(payload: dict) -> envelope`(标准信封 `code/message/data/retryable`); 由 `ToolRegistry`/`DictToolRegistry` 管理 (`register(name, func, description, input_schema)` / `get` / `list_tools` / `unregister`)。
- **注册方式**: 23 个**内置**工具在 `run/factories/business_layer.py` **启动时硬编码** `tool_registry.register(...)`。agent 也有 `register_tool()` 可运行时动态注册。
- **触网工具**: `http_get` / `web_search` / `browser_visit` 能触网, 但都是**固定内置函数**, 不是"连接外部工具提供方"的机制。
- **缺口确认**: 全项目无 `mcp` / `openapi` / 外部连接器代码 → agent 能力被钉死在 23 个内置工具, **无法接入外部工具生态**。
- **可复用资产**:
  - `ToolRegistry` — 任意 callable 都能注册成工具。
  - **SSRF 防御** (`tools_impl/http_get.py`: `_is_private_ip` / `_resolve_safe`) — 私网/环回拒绝、超时、大小、禁跳转。
  - 配置 `ConfigManager.get_effective_value`(config + env + default)。
  - **敏感加密** Fernet (`SENSITIVE_CONFIG_SECRET`) — 存外部 API key。
  - **工具审批白名单** `tool_approval_required`(危险工具需 `extra_params.approve_tools`)。
  - **审计 hooks**(每次 tool_call 落 JSONL) + **配额** `quota_module`(限成本)。
- **依赖现状**: `requests` / `httpx` 已在 requirements; **无 `mcp` SDK**。项目奉行**少依赖哲学**(web_search 手写 HTML 解析避 bs4、scheduler 避 APScheduler)。

## 2. 两条路径详解

### 路径 A — HTTP/OpenAPI 外部工具连接器 (轻量)
**思路**: 配置驱动地把外部 REST API 声明为 agent 工具。
- 声明 spec(config 段或 JSON): `{name, description, method, url_template, headers, query/body 参数 schema, auth(引用加密 secret), timeout, response_path(从响应抽取的 JSONPath-lite)}`。
- 工厂 `make_http_tool(spec) -> callable`: 拼 URL/参数 → `urllib` 请求(**复用 http_get 的 SSRF/超时/大小限制**) → 按 `response_path` 抽取 → 包成标准 envelope。
- 启动时从配置加载所有 spec → `tool_registry.register` 每个。
- (可选增强) 解析 **OpenAPI** spec → 自动批量生成工具声明。

**复用**: ToolRegistry / SSRF 防御 / 配置 / 加密 secret(key) / 审批白名单 / 审计。
**工作量**: 中小。**新依赖**: 0(urllib/requests 已有)。
**局限**: 仅 REST; 私有声明非标准; 每个 API 要写声明(OpenAPI 自动化可缓解)。

### 路径 B — MCP 客户端 (标准, 生态最大)
**思路**: 实现 MCP(Model Context Protocol) 客户端, 连接外部 MCP server, 发现并注册其 tools。
- **传输**: stdio(本地子进程 server) / SSE·HTTP(远程 server)。
- **协议**: JSON-RPC 2.0; `initialize` → `tools/list` → `tools/call`。
- **集成**: 启动按配置连 server → `tools/list` → 每个远程 tool 包成 callable(内部 `tools/call`) → `tool_registry.register`; MCP `inputSchema` 映射工具 `input_schema`。
- **生命周期**: server 进程/连接管理、超时、断连重连、关闭清理。

**依赖取舍**:
- **B1 官方 `mcp` Python SDK**: 省事、跟标准同步; 但**加重依赖** + **asyncio** 模型需与本项目同步风格桥接。
- **B2 手写最小客户端**: JSON-RPC over stdio/HTTP; 符合少依赖哲学; 但要自己跟协议演进, 工作量大。

**复用**: ToolRegistry / 审批 / 审计; 但传输+协议是全新的。
**工作量**: 大。**收益**: 行业标准, 接入 filesystem/github/db/slack 等**几百个现成 server**, 面向未来。
**安全**: 外部 server(尤其 stdio 子进程)是**代码执行边界**, 信任模型更重。

## 3. 对比

| 维度 | A. HTTP 连接器 | B. MCP 客户端 |
|---|---|---|
| 工作量 | 中小 | 大 |
| 新依赖 | **0** | mcp SDK 或自写 |
| 标准化 | 私有声明 | **行业标准** |
| 生态 | 自写每个 API | **几百现成 server** |
| 能力边界 | REST only | 任意 MCP server |
| 并发模型 | 同步(契合本项目) | asyncio(需桥接) |
| 安全面 | egress/SSRF/key | + 子进程执行边界 |
| 少依赖哲学契合 | ✅ | ⚠️ SDK / 工作量大 |

## 4. 推荐: 分阶段
- **Stage 1 — HTTP/OpenAPI 连接器**: 快、零依赖、最大复用(SSRF/审批/审计/加密), 立即补上"能调外部 REST API"的核心缺口, 覆盖大量实际需求。
- **Stage 2 — MCP 客户端**: 待 Stage 1 稳后做标准化升级; 优先 stdio 本地 server; SDK vs 自写另议。
- **理由**: 以最小代价、最大复用先补核心缺口; MCP 作为标准化/生态扩展叠加。

## 5. 统一抽象 (两阶段共享, 契合本项目 ABC 风格)
建议引入 `ExternalToolProvider` 抽象(`core/base.py`): `discover() -> List[ToolSpec]` + `invoke(name, payload) -> envelope`。
- `HttpToolProvider`(Stage 1) / `McpToolProvider`(Stage 2) 都实现它。
- 启动时各 provider `discover()` → 注册进 `ToolRegistry`(带 `source` 标记便于审计/区分)。
- 好处: 接入点统一, 以后加 provider 不动 agent/registry; 纳入 `check_abc_alignment` 守护。

## 6. 安全设计 (两阶段必须)
- **默认全部外部工具入 `tool_approval_required`** — 须 `extra_params.approve_tools` 显式放行(human-in-loop, 与方向3/4 一致)。
- **egress 白名单**: 只允许声明的 host + 复用 `http_get` 的 SSRF 防御(拒私网)。
- **凭据**: API key 走加密 secret(复用 Fernet/`SENSITIVE_CONFIG_SECRET`), 不明文入配置/日志/审计。
- **审计**: 每次外部工具调用落审计(复用 hooks); **配额** `quota_module` 限外部调用成本。
- **超时/响应大小**: 复用 http_get 限制。
- **MCP**: stdio server = 子进程执行边界, 需**显式信任声明**, 绝不自动连未知 server。

## 7. 集成点 (代码位置, 落地时)
- 新组件: `business/agent_module/tools/external/`(provider 抽象 base + http/mcp impl)。
- 注册: `run/factories/business_layer.py` 启动时 `provider.discover()` → `tool_registry.register`。
- 配置: `config.yaml` 加 `agent.external_tools`(HTTP spec 列表) / `agent.mcp_servers`(MCP server 列表), 走 `get_effective_value`。
- 审批: 默认并入 `tool_approval_required`。
- 文档/测试: `doc/` + `CHANGELOG` + `tools/external/tests/`。

## 8. 待拍板的问题 + 已取决策 (2026-06-03)
1. **先 Stage 1(HTTP) 还是直接上 MCP?** → ✅ **Stage 1 先行**(已实现); MCP 作 Stage 2 接同一抽象。
2. **MCP 若做**: 官方 `mcp` SDK vs 最小自实现? → ✅ **最小自实现** (stdio JSON-RPC 2.0, 无 SDK 依赖, 参考 codex/claude code; `e08280e`)。SSE/HTTP 传输待后续。
3. **默认安全姿态**: → ✅ 外部工具**默认需审批**(并入 `tool_approval_required`) + SSRF 私网防御(复用 http_get); egress 限于声明的 host。
4. **配置形式**: → ✅ **`config.yaml` 的 `agent.external_tools`**(HttpToolSpec dict 列表) + 兼容运行时 `register_tool`。
5. **范围**: → ✅ 先建**机制 + 测试 spec**(用户之后填真实 API; 凭据建议走 env/加密 secret, 勿明文入配置)。

> 用户拍板"推进 RFC"+"参考 codex/claude code 实现"; Q1-Q5 均已定 + 落地。已完成: Stage1 HTTP 连接器 + Stage2 MCP stdio 客户端 + 增量(MCP **HTTP/SSE 传输** `102e942` / **真实 server 集成测试**(自带 echo server, 真子进程) `fe10cab` / **OpenAPI 自动生成 HTTP 工具** `062cccb`)。

> **余量收尾 (2026-06-08)**:
> - ✅ **OpenAPI `spec_url` 远程拉取**: `fetch_openapi_spec()` (SSRF 安全 + JSON/YAML 解析, fetch 可注入);
>   `agent.openapi_tools` 每项可填 `spec_url` (远程) 或 `spec` (inline), entry 的 auth_* 也用于拉取。fail-safe 跳过。
> - ✅ **连接生命周期管理**: `ExternalToolProvider.close()` (base no-op; `McpToolProvider` 覆写: 留已建连接引用,
>   close 逐个释放/杀 stdio 子进程, fail-safe + 幂等); business_layer 经 `result["external_tool_providers"]` 透出
>   providers 供管理。注: app 退出时统一 close 的 shutdown 钩子尚未接 (子进程随主进程退出回收, 影响小), 留作可选。
> - ⏸️ **MCP SSE 长连** (server→client 流式通知): **暂缓**。现 HTTP 已支持请求/响应 (含解析 SSE 响应帧),
>   但"持久 SSE 监听线程接收 server 主动通知"目前**无消费方** —— agent 启动时一次性 discover 工具表、不做动态刷新,
>   故 `notifications/tools/list_changed` 等通知无人消费。此时建持久监听线程=造无用复杂度 (违 minimal-change)。
>   **触发条件**: 待出现"动态刷新工具表/订阅 server 通知"的真实需求时再做 (届时与 close() 生命周期配套关闭监听)。

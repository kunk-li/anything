# 应用层-控制台交互模块（console_app_module）设计说明书

| 项 | 值 |
| :--- | :--- |
| 文档版本 | v2.0 |
| 最后更新 | 2026-06-03 |
| 维护责任人 | 控制台交互模块开发负责人 |
| 状态 | 已与代码对齐（AUDIT-3 重写；原文件内容曾错挂为 common_utils 设计文档，本次按 `core/base.py` + `core/impl.py` 实际实现重写） |

## 1. 模块概述

### 1.1 模块定位
`console_app_module` 是**应用层**的命令行入口，与 `api_service_module`（HTTP / WebSocket 入口）平级。它把用户在终端的输入标准化成统一请求，交给**接口层** `RequestHandler` 处理，再把统一响应信封渲染成人类可读的终端输出。

### 1.2 核心职责
- 解析终端输入：区分**斜杠命令**（如 `/mode agent`）与普通 query / task
- 维护**会话状态机**：mode / top_k / attachments / session_id / plan_only / approve_tools
- 三种运行形态：单次（`run_once`）、交互式 REPL（`run_interactive`）、批处理（`run_batch`）
- 把统一响应信封渲染为终端文本（friendly / plain 两种风格）
- 记录与导出对话历史（json / jsonl / markdown）

### 1.3 不负责（边界）
- 业务语义（检索 / Agent 执行）——交给 `RequestHandler` → `Orchestrator`
- `trace_id` 生成——由应用层单层生成后透传（见第 8 章），本模块不重新生成业务 trace_id
- 鉴权 / 配额——CLI 默认单用户本地场景，不做鉴权（与 HTTP 入口不同）

## 2. 架构与依赖

```
用户终端
   │  raw text / 批处理文件
   ▼
ConsoleApp (本模块)
   │  parse_input → handle_command? → build_request
   ▼
RequestHandler.handle(request, trace_id)   # 接口层, 构造注入
   ▼
Orchestrator → RAG / Agent                 # 业务层
   │  统一响应信封 {code,message,data,trace_id,...}
   ▼
ConsoleApp.render_response → 终端输出 + 写 history
```

- **基础依赖走 DI**：构造时接收 `BasicDeps`（`deps.utils / logger / config / exception_handler`），未注入时回退 `build_basic_deps()`（向后兼容）。
- **handler 构造注入**：`ConsoleApp(handler=...)`，`handler` 为 `RequestHandler` 实例，无状态调用 `handler.handle(...)`。
- 可选注入：`input_provider`（输入源，便于测试 / 批处理）、`renderer`（自定义渲染器）、`history_store`（历史存储，默认进程内 `_InMemoryHistoryStore`）、`session`（初始会话状态）。

## 3. 数据模型（`model/data_model.py`）

| 模型 | 关键字段 | 说明 |
| :--- | :--- | :--- |
| `ConsoleSessionConfig` | `mode='rag'` / `session_id` / `top_k=5` / `verbose` / `prompt_text` / `renderer='plain'` / `attachments[]` / `multiline` / `plan_only` / `approve_tools[]` | 会话状态机；`plan_only` / `approve_tools` 与 `SimpleAgent.extra_params` 对齐（Task X #58） |
| `ConsoleInput` | `raw_text` / `cleaned_text` / `is_command` / `command_name` / `command_arg` / `attachment_paths[]` / `metadata` | `parse_input` 的输出；命令与普通输入的统一载体 |
| `ConsoleRenderResult` | `success` / `title` / `body` / `footer` / `raw_response` | `render_response` 的输出 |
| `ConsoleHistoryItem` | `timestamp` / `session_id` / `request` / `response` / `duration_ms` / `source` / `tags[]` | 一条历史记录 |

## 4. 接口定义

### 4.1 抽象基类 `BaseConsoleApp`（`core/base.py`）
7 个抽象方法，`ConsoleApp` 全部实现：

| 方法 | 签名 | 职责 |
| :--- | :--- | :--- |
| `run` | `() -> None` | 启动入口（默认进交互式 REPL） |
| `parse_input` | `(text: str) -> ConsoleInput` | 原始输入 → 结构化；识别 `/` 开头的命令 |
| `build_request` | `(source: ConsoleInput \| Dict \| str) -> Dict` | 统一构造 request body，按 session 状态填默认值（mode/top_k/session_id 等） |
| `render_response` | `(response: Dict) -> ConsoleRenderResult` | 统一响应信封 → 终端可读结构 |
| `handle_command` | `(console_input: ConsoleInput) -> bool` | 执行斜杠命令；返回是否已被命令消费（True 则不发请求） |
| `run_batch` | `(items: Iterable[Dict] \| str) -> List[Dict]` | 批处理：逐条执行，返回响应列表 |
| `export_history` | `(export_path: str, fmt='json') -> str` | 导出历史到文件 |

> 注：`build_request` 的 `source` 形参接受 `ConsoleInput / dict / str` 三种（AUDIT-2c 已让 base 签名与 impl 一致）。

### 4.2 `ConsoleApp` 扩展方法（非抽象）
`run_once`（单次执行）、`run_interactive`（REPL 主循环）、`run_batch_file`（从文件批处理）、`execute_request`（build→handle→记 history 一站式）、`render_response` 的 `mode` 覆盖参数等。

## 5. 命令系统

`parse_input` 把 `/` 开头的输入解析为命令；`handle_command` 按 `_COMMAND_HANDLERS` 表分派。支持同义词。

| 命令（同义词） | 参数 | 作用 |
| :--- | :--- | :--- |
| `/mode` (`/m`) | `rag\|agent\|hybrid` | 切换执行模式 |
| `/topk` (`/k`) | int | 设置检索 top_k |
| `/attach` | 路径 | 给后续请求附加文件 |
| `/clear` (`/clear_attach`) | — | 清空附件 |
| `/help` (`/h`) | — | 显示帮助 |
| `/history` | — | 显示本会话历史 |
| `/exit` (`/quit`) | — | 退出 REPL |
| `/session_id` (`/sid`) | str? | 查看 / 设置 session_id |
| `/plan` | `on\|off\|toggle` | 切 plan_only（Agent 先规划后执行，Task V #56） |
| `/approve` | `tool1,tool2` | 给危险工具发白名单（Task W #57） |
| `/unapprove` | `tool1` | 撤白名单 |
| `/memory` | — | 显示当前生效的 ProjectMemory（AGENTS.md，Task U #55） |

## 6. 运行模式

- **`run_once(request, trace_id?)`**：单次执行一个 request，返回响应。脚本 / 一次性调用用。
- **`run_interactive()`**：REPL 主循环——读输入 → `parse_input` → 命令则 `handle_command`，否则 `build_request` → `_call_handler` → `render_response` → 打印 + 记 history，直到 `/exit`。
- **`run_batch(items | path)`**：批处理。`items` 可为请求列表，或 jsonl / 纯文本文件路径（`_load_jsonl_batch` / `_load_text_batch`）。逐条执行；`console_app.batch_fail_fast=true` 时遇错中止，否则跳过继续。

## 7. 渲染

`render_response(response, mode?)` 支持两种风格：
- **friendly**（默认，`console_app.default_render_mode`）：带标题 / 正文 / footer 的结构化展示，正文走 `_render_text`。
- **plain**：精简文本。

渲染从统一响应信封的 `data` 抽取 `answer` / `citations` / `steps` 等；失败响应（非 SUCCESS code）渲染为错误提示。

## 8. trace_id 与错误处理

- **trace_id 单层生成**：`_generate_trace_id()` 仅在入口（无上游 trace_id 时）生成一次，之后透传给 `handler.handle(request, trace_id=...)`，与系统"应用层单层生成"约定一致（架构总图第 8 章）。
- **异常兜底**：`_handle_exception` 走注入的 `exception_handler`，把异常包装成统一信封，CLI 不因单条请求异常而崩溃；批处理中单条失败按 `batch_fail_fast` 决定中止或继续。

## 9. 配置项（`config_module` 读取）

| 键 | 默认 | 说明 |
| :--- | :--- | :--- |
| `console_app.default_render_mode` | `friendly` | 默认渲染风格 |
| `console_app.enable_history` | `true` | 是否记历史 |
| `console_app.history_file` | `./console_history.jsonl` | 历史落盘路径 |
| `console_app.batch_fail_fast` | `false` | 批处理遇错是否中止 |

## 10. 历史与导出

- `history_store` 默认 `_InMemoryHistoryStore`（进程内，暴露 `list_items()` 便于测试）。
- `export_history(path, fmt)`：支持 `json` / `jsonl` / `md`（markdown）三种格式导出。
- 交互模式下每条问答自动 `_save_history_safe`（best-effort，失败不影响主流程）。

## 11. 测试

`tests/test_impl.py`（构造 / 单次执行 / 渲染）、`tests/test_commands.py`（命令解析 + plan/approve 进 extra_params）。`build_request` / `execute_request` 用注入的 stub handler 验证，无需真实业务层。

## 12. 变更记录

- **v2.0 (2026-06-03, AUDIT-3)**：本文件原内容错挂为 common_utils 设计文档，按实际代码重写为真正的 console_app 设计说明书；`build_request` 签名（`source` 接受 ConsoleInput/dict/str）与 base/impl 对齐（AUDIT-2c）。
- 历史能力沉淀：Task T（#54）session 状态机 + 批处理 + 历史导出；Task X（#58）`/plan` `/approve` `/memory` 命令。

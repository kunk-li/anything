
# 应用层 - 控制台交互模块（console_app_module）设计说明书（扩展版）

| 文档版本 | v1.1 |
| :--- | :--- |
| **最后更新** | 2026-03-18 |
| **维护责任人** | 控制台交互模块开发负责人 |
| **状态** | 正式发布 |

---

## 1. 文档概述

### 1.1 文档目的
本文档为 RAG 与 Agent 系统应用层 - 控制台交互模块的独立、完整设计说明书。文档严格遵循系统整体架构规范，结合总设计文档中“应用层只提供用户入口、不承载业务逻辑、仅调用接口层”的原则，对控制台交互模块的定位、目录结构、核心接口、调用流程、配置、测试要求及扩展能力进行详细定义，用于指导开发人员独立完成模块开发并与系统无缝集成。

### 1.2 适用人群
- **开发人员**：作为控制台交互模块开发、调试、维护的唯一标准依据。
- **测试人员**：作为编写命令行交互测试用例与验收模块行为的标准依据。
- **项目管理人员**：参考本说明书进行开发范围确认、交付物验收与集成排期。
- **演示/运维人员**：作为本地演示、故障排查、离线验证、批量脚本执行的操作依据。

### 1.3 核心需求回顾

| 需求类型 | 具体要求 |
| :--- | :--- |
| **模块功能** | 提供命令行交互入口，接收用户输入并转发给接口层，输出标准化响应结果，并支持帮助、模式切换、多轮会话、批处理、历史导出、文件输入、脚本执行等增强能力。 |
| **开发语言** | Python 3.10+，与系统整体保持一致。 |
| **开发模式** | 独立开发、低耦合，不依赖核心业务层内部实现，仅依赖接口层抽象接口及基础支撑层公共能力。 |
| **文档要求** | 详细、易懂，适配初学者，明确输入输出格式、交互流程、项目结构及测试要求。 |
| **模块约束** | 需包含抽象基类（ABC）；控制台模块不得内嵌业务逻辑，不得直接操作 RAG/Agent/向量库实现类。 |
| **扩展目标** | 在不破坏应用层边界的前提下，提升本地调试效率、演示表现、批量任务执行能力与可观测性。 |

### 1.4 术语定义

| 术语 | 定义 |
| :--- | :--- |
| **控制台交互** | 用户通过命令行输入问题、任务或控制指令，并接收文本化结果输出的交互方式。 |
| **命令模式** | 控制台识别特定前缀指令（如 `/help`、`/mode`、`/exit`）并执行本地控制逻辑的机制。 |
| **请求构建** | 将控制台原始输入转换为系统统一请求格式（`type/query/task/session_id/top_k` 等）的过程。 |
| **结果渲染** | 将标准化响应中的 `code`、`message`、`data`、`trace_id` 等字段格式化输出到终端的过程。 |
| **批处理模式** | 通过文件或脚本一次性执行多条请求，并输出批量结果的运行方式。 |
| **会话历史** | 控制台在当前或指定 session 下保留的输入、输出、trace_id、时间戳等轻量交互记录。 |

---

## 2. 模块核心设计

### 2.1 模块定位与职责
本模块属于系统应用层，是除 API 服务模块之外的另一类用户交互入口，主要面向本地开发调试、命令行演示、离线验证、教学示例、批量脚本执行与简易运维场景。模块本身不实现任何 RAG、Agent 或协同调度业务逻辑，仅负责采集控制台输入、构建标准请求、调用接口层 `RequestHandler`，并将返回结果以易读方式输出到终端。

核心职责如下：
- 接收用户在终端输入的问题、任务或控制指令；
- 根据当前交互模式（`rag / agent / hybrid`）构建标准请求；
- 调用接口层 `RequestHandler.handle` 完成统一处理；
- 将标准化响应渲染为终端可读输出，突出答案、错误码、`trace_id` 与耗时等信息；
- 管理轻量会话状态，如当前模式、默认 `top_k`、`session_id`、是否显示详细信息；
- 提供帮助信息、退出控制、输入清洗、异常兜底与诊断输出；
- 支持多轮会话、批量任务、脚本化执行、文件输入、结果导出等增强交互能力；
- 为开发调试和演示场景提供更友好的命令体系与终端展示效果。

### 2.2 输入输出规范

#### 2.2.1 输入

| 输入项 | 类型 | 必填 | 说明 | 默认值 |
| :--- | :--- | :--- | :--- | :--- |
| `用户文本输入` | str | 是 | 用户在控制台输入的问题、任务或命令。 | - |
| `模式 mode` | str | 否 | 当前请求模式：`rag / agent / hybrid`。 | `rag` |
| `session_id` | str | 否 | 当前控制台会话标识，用于连续请求追踪。 | 自动生成 |
| `top_k` | int | 否 | rag 模式下默认检索片段数量。 | 5 |
| `verbose` | bool | 否 | 是否输出 `data` 全量内容、`trace_id`、`cost_time`。 | true |
| `input_file` | str | 否 | 批处理文件、脚本文件或问题文本文件路径。 | - |
| `attachment_paths` | list[str] | 否 | 用户通过命令指定的本地附件路径，仅作为参数传递给接口层。 | [] |
| `export_path` | str | 否 | 会话历史或批处理结果导出路径。 | - |

#### 2.2.2 输出
控制台交互模块的对外输出分为两类：一类是终端展示文本，面向用户阅读；另一类是内部标准化响应字典，完全沿用接口层输出结构，不做语义变更。

```json
{
  "code": "SUCCESS",
  "message": "ok",
  "data": {
    "answer": "这是控制台输出示例"
  },
  "trace_id": "b3b1c6d7f2b24f5aa0d8e7c8b9a1c2d3",
  "retryable": false,
  "details": null,
  "cost_time": 0.38
}
```

### 2.3 依赖关系

| 依赖模块 | 用途 |
| :--- | :--- |
| **请求响应处理模块** (`request_response_module`) | 作为唯一业务调用入口，负责参数校验、请求标准化、调度执行和响应封装。 |
| **配置管理模块** (`config_module`) | 读取控制台默认模式、提示词、输出风格、超时阈值等配置。 |
| **日志模块** (`log_module`) | 记录控制台启动、命令输入、调用结果与异常日志。 |
| **异常处理模块** (`exception_module`) | 统一转换交互异常，避免未处理异常导致控制台直接崩溃。 |
| **通用工具模块** (`common_utils_module`) | 提供 `session_id` 生成、时间处理、字符串清洗等辅助能力。 |

### 2.4 设计原则
- **边界清晰**：控制台模块只处理输入、输出与交互状态，不承载业务决策。
- **易用优先**：默认即可运行，适合本地调试与演示。
- **可扩展**：支持新增命令、输出主题、更多终端渲染策略。
- **可替换**：上层仅依赖抽象接口，允许更换具体控制台实现（如 Rich/Typer/Prompt Toolkit 版本）。
- **可观测**：所有关键交互均可记录 `trace_id`、耗时、命令日志和错误上下文，便于排查问题。
- **脚本友好**：支持无人值守执行、批量任务、历史导出和失败重试。

### 2.5 功能扩展设计（本版新增）

| 扩展能力 | 功能说明 | 是否进入接口层 | 说明 |
| :--- | :--- | :--- | :--- |
| 多模式切换 | 支持 `rag / agent / hybrid` 动态切换 | 是 | 控制台仅修改请求字段 `type`。 |
| 多轮会话管理 | 支持同一 `session_id` 连续发起请求 | 是 | 接口层/状态层决定是否利用会话上下文。 |
| Slash 命令体系 | 支持 `/help`、`/mode`、`/topk`、`/verbose`、`/history` 等 | 否 | 本地命令，不进入业务层。 |
| 多行输入模式 | 支持长任务、复杂提示词、脚本片段输入 | 是 | 控制台组装文本后再转发。 |
| 批处理文件执行 | 从 jsonl / txt / yaml 中读取多条任务执行 | 是 | 每条仍走统一 `handler.handle`。 |
| 脚本执行模式 | 预先编排命令与请求，适合演示和回归 | 是/否 | 本地命令本地执行，请求仍转发。 |
| 文件输入扩展 | 通过命令指定本地文件路径，转为 `extra_params` 或上传入口 | 是 | 不直接解析文件内容。 |
| 会话历史导出 | 导出 markdown/json/csv 格式的交互记录 | 否 | 仅导出控制台历史。 |
| 错误诊断视图 | 更友好展示 `code/message/details/trace_id` | 否 | 只负责渲染。 |
| 主题渲染 | 纯文本版 / Rich 彩色版 / 简洁 CI 版 | 否 | 展示策略可配置。 |
| 回放与重试 | 可针对历史项重新执行 | 是 | 控制台重构请求再次发送。 |
| 指标摘要 | 汇总本次运行的成功率、平均耗时、错误码分布 | 否 | 仅做本地统计。 |

## 3. 统一项目结构规范

```text
console_app_module/
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
├── adapters/
│   ├── __init__.py
│   ├── renderer.py
│   └── input_provider.py
├── storage/
│   ├── __init__.py
│   └── history_store.py
├── config/
│   ├── __init__.py
│   └── config.py
├── tests/
│   ├── __init__.py
│   ├── test_impl.py
│   ├── test_commands.py
│   └── test_batch_mode.py
├── examples/
│   ├── demo_commands.txt
│   └── sample_batch.jsonl
├── README.md
└── requirements.txt
```

### 3.1 目录结构说明

| 目录/文件 | 说明 |
| :--- | :--- |
| `console_app_module` | 模块根目录，名称固定，与控制台交互职责精准对应。 |
| `core` | 定义抽象接口并实现交互循环、命令解析与调用流程。 |
| `model` | 定义控制台会话配置、输入结果、渲染结果和历史记录模型。 |
| `utils` | 封装命令判断、请求构建、终端美化输出等工具函数。 |
| `adapters` | 适配不同终端渲染方案、不同输入来源，隔离外部依赖。 |
| `storage` | 管理本地历史、结果导出与回放能力。 |
| `tests` | 覆盖命令解析、模式切换、成功/失败调用、异常、批处理与导出场景。 |
| `examples` | 提供演示脚本、批处理模板，帮助开发者快速启动。 |

## 4. 核心数据模型设计

### 4.1 控制台会话配置模型（ConsoleSessionConfig）
- `mode: str = "rag"`
- `session_id: Optional[str] = None`
- `top_k: int = 5`
- `verbose: bool = True`
- `prompt_text: str = "请输入问题/任务（/help 查看帮助，/exit 退出）：" `
- `renderer: str = "plain"`
- `attachments: list[str] = []`

### 4.2 控制台输入模型（ConsoleInput）
- `raw_text: str`
- `cleaned_text: str`
- `is_command: bool = False`
- `command_name: Optional[str] = None`
- `command_arg: Optional[str] = None`
- `attachment_paths: list[str] = []`
- `metadata: dict[str, Any] = {}`

### 4.3 控制台渲染结果模型（ConsoleRenderResult）
- `success: bool`
- `title: str`
- `body: str`
- `footer: Optional[str] = None`
- `raw_response: Optional[dict[str, Any]] = None`

### 4.4 历史记录模型（ConsoleHistoryItem）
- `timestamp: str`
- `session_id: str`
- `request: dict[str, Any]`
- `response: dict[str, Any]`
- `duration_ms: int = 0`
- `source: str = "interactive"`
- `tags: list[str] = []`

## 5. 核心接口设计

### 5.1 控制台交互抽象基类（BaseConsoleApp）
必须至少定义以下接口：
- `run() -> None`
- `parse_input(text: str) -> ConsoleInput`
- `build_request(console_input: ConsoleInput) -> dict[str, Any]`
- `render_response(response: dict[str, Any]) -> ConsoleRenderResult`
- `handle_command(console_input: ConsoleInput) -> bool`
- `run_batch(items: Iterable[dict[str, Any]]) -> list[dict[str, Any]]`
- `export_history(export_path: str, fmt: str = "json") -> str`

### 5.2 历史存储抽象接口（BaseHistoryStore）
- `append(item: ConsoleHistoryItem) -> None`
- `list_items(session_id: str | None = None) -> list[ConsoleHistoryItem]`
- `export(path: str, fmt: str = "json") -> str`

## 6. 交互命令体系设计（扩展重点）

| 命令 | 示例 | 作用 | 是否发起业务请求 |
| :--- | :--- | :--- | :--- |
| `/help` | `/help` | 显示帮助文档、当前配置与示例命令 | 否 |
| `/exit` | `/exit` | 退出控制台 | 否 |
| `/mode` | `/mode agent` | 切换模式：`rag / agent / hybrid` | 否 |
| `/topk` | `/topk 8` | 修改默认检索数量 | 否 |
| `/verbose` | `/verbose off` | 切换详细输出开关 | 否 |
| `/session` | `/session new` | 新建、查看或切换 session_id | 否 |
| `/history` | `/history 20` | 查看最近 N 条历史 | 否 |
| `/export` | `/export out/history.json` | 导出会话历史 | 否 |
| `/attach` | `/attach ./demo.pdf` | 设置附件路径参数 | 否 |
| `/clear_attach` | `/clear_attach` | 清空当前附件列表 | 否 |
| `/multiline` | `/multiline on` | 开启多行输入模式 | 否 |
| `/batch` | `/batch ./tasks.jsonl` | 执行批处理文件 | 是 |
| `/script` | `/script ./demo_commands.txt` | 执行脚本文件 | 视命令而定 |
| `/retry` | `/retry last` | 重新执行上一条业务请求 | 是 |
| `/stats` | `/stats` | 输出本次运行指标摘要 | 否 |
| `/theme` | `/theme rich` | 切换渲染主题 | 否 |

命令处理约束：
- 命令解析必须先于请求构建；
- 未识别命令需友好提示，不得导致控制台退出；
- 所有命令必须可单元测试；
- 本地命令修改的是控制台会话状态，不得绕过接口层直接触发核心业务模块。

## 7. 请求构建与调用流程设计

### 7.1 单次交互流程
1. 控制台读取用户原始输入。
2. 执行输入清洗与命令识别。
3. 若为本地命令，则直接本地处理并返回循环。
4. 若为业务请求，则根据当前 `mode` 构建统一请求字典。
5. 将请求提交给 `RequestHandler.handle`。
6. 接收标准化响应并完成终端渲染。
7. 将请求与响应写入历史存储，记录 `trace_id`、耗时、错误码等信息。

### 7.2 请求构建规则

| 当前模式 | 文本映射规则 | 构建示例 |
| :--- | :--- | :--- |
| `rag` | 文本映射到 `query` | `{"type": "rag", "query": text, "top_k": 5}` |
| `agent` | 文本映射到 `task` | `{"type": "agent", "task": text}` |
| `hybrid` | 文本映射到 `task`，并允许带 `extra_params` | `{"type": "hybrid", "task": text}` |

统一补充字段：
- `session_id`
- `top_k`
- `extra_params.attachments`
- `extra_params.source = "console_app"`
- `extra_params.console_meta`

### 7.3 批处理流程（新增）
1. 用户通过 `/batch path` 指定批处理文件。
2. 控制台解析文件内容，支持 `jsonl / json / txt / yaml`。
3. 对每条任务补齐默认 `mode`、`session_id` 与 `top_k`。
4. 顺序或并发受控地调用 `handler.handle`。
5. 输出批处理执行摘要：总数、成功数、失败数、平均耗时、失败项索引。
6. 结果按配置写入导出文件，便于回归测试或演示归档。

### 7.4 脚本模式流程（新增）
脚本模式允许开发者把一组控制台命令和业务请求写入文本文件，实现自动化演示或回归：
- 以 `/` 开头的行为控制台命令；
- 普通文本行为业务输入；
- 支持注释行（`#`）；
- 支持 `---` 作为多行输入分隔；
- 支持失败后继续或立即终止两种策略。

## 8. 终端渲染与用户体验设计

### 8.1 渲染目标
- 普通用户能够快速看懂答案主体；
- 开发人员能够快速看到 `trace_id`、耗时、错误细节；
- 批处理/CI 场景输出应简洁稳定，便于日志采集；
- 富文本终端场景支持彩色标题、分区边框与表格摘要。

### 8.2 输出区域建议

| 区域 | 内容 |
| :--- | :--- |
| 标题区 | 当前模式、执行结果、时间戳 |
| 主体区 | 答案正文 / 任务结果 / 错误摘要 |
| 附加区 | 引用、工具调用简表、检索结果摘要（若接口层返回） |
| 页脚区 | `trace_id`、`cost_time`、`session_id` |

## 9. 配置设计

```python
CONSOLE_APP_CONFIG = {
    "default_mode": "rag",
    "default_top_k": 5,
    "default_verbose": True,
    "default_renderer": "plain",
    "enable_multiline": True,
    "enable_batch": True,
    "enable_script_mode": True,
    "history_max_size": 500,
    "export_default_format": "json",
    "batch_continue_on_error": True,
    "show_trace_id": True,
    "show_cost_time": True
}
```

配置原则：
- 所有配置项需提供默认值，未配置时模块可独立运行；
- 扩展能力应可按需开关，避免给最小化运行场景增加复杂度；
- 控制台主题、导出格式、批处理行为等均应配置化；
- 不允许在控制台模块中硬编码核心业务参数或模型参数。

## 10. 错误处理与日志设计

| 错误类型 | 示例 | 处理方式 |
| :--- | :--- | :--- |
| 输入错误 | 空输入、非法 mode、top_k 非数字 | 本地拦截并提示 |
| 文件错误 | 批处理文件不存在、导出路径不可写 | 本地拦截并记录日志 |
| 调用错误 | `handler.handle` 抛出异常 | 交给异常模块封装后输出 |
| 响应错误 | 返回结果字段缺失或结构异常 | 输出兼容渲染结果并记录警告 |
| 中断错误 | Ctrl+C / EOF | 安全退出并可选保存历史 |

日志建议：
- 启动日志：记录版本、配置摘要、默认模式；
- 交互日志：记录输入类型、模式、session_id、执行耗时；
- 命令日志：记录命令名称、参数、是否执行成功；
- 错误日志：记录异常堆栈、trace_id、失败请求快照；
- 批处理日志：记录文件来源、总任务数、失败项索引与导出路径。

## 11. 测试设计

### 11.1 单元测试范围
- 普通文本输入能否正确映射为 `rag / agent / hybrid` 请求；
- Slash 命令是否正确解析与执行；
- 空输入、未知命令、非法参数是否被友好处理；
- 渲染器是否能兼容成功、失败与字段缺失响应；
- 历史存储与导出是否符合预期；
- 批处理文件解析是否正确。

### 11.2 集成测试范围
- 控制台与 `RequestHandler` 的集成调用是否正常；
- 多轮 session 下请求字段是否连续一致；
- 批处理模式是否能正确统计成功/失败结果；
- 脚本模式是否能按顺序执行命令与请求；
- Ctrl+C / EOF 退出时是否能安全收尾。

### 11.3 推荐测试用例

| 用例编号 | 测试场景 | 预期结果 |
| :--- | :--- | :--- |
| TC-01 | 输入普通问题，默认 rag 模式 | 构建 `type=rag` 请求并成功输出 |
| TC-02 | `/mode agent` 后输入任务 | 构建 `type=agent` 请求 |
| TC-03 | `/topk 10` 后发起 rag 问题 | 请求中 `top_k=10` |
| TC-04 | `/history 5` | 输出最近 5 条本地历史 |
| TC-05 | `/batch ./sample_batch.jsonl` | 批量执行并输出统计摘要 |
| TC-06 | `/script ./demo_commands.txt` | 命令与请求按顺序执行 |
| TC-07 | 请求处理器抛异常 | 控制台不崩溃，输出统一错误结果 |
| TC-08 | `/export ./history.json` | 导出成功且文件内容完整 |
| TC-09 | 非法命令 `/mode xxx` | 友好提示并保持原模式 |
| TC-10 | Ctrl+C 中断 | 安全退出并打印退出提示 |

## 12. 交付物清单（强制）
1. `core/base.py`：抽象基类，定义控制台交互核心接口；
2. `core/impl.py`：具体实现类，包含交互循环、命令调度、批处理与脚本模式；
3. `model/data_model.py`：控制台会话、输入、渲染结果、历史记录数据模型；
4. `utils/tool_functions.py`：输入清洗、命令解析、请求构建、输出格式化等工具函数；
5. `adapters/renderer.py`：纯文本/Rich 渲染器实现；
6. `adapters/input_provider.py`：标准输入、脚本输入、批处理输入适配器；
7. `storage/history_store.py`：本地历史记录写入、读取、导出；
8. `config/config.py`：控制台模块配置读取逻辑；
9. `tests/test_impl.py`：核心流程测试；
10. `tests/test_commands.py`：命令解析与状态切换测试；
11. `tests/test_batch_mode.py`：批处理与导出能力测试；
12. `examples/demo_commands.txt`：演示脚本；
13. `examples/sample_batch.jsonl`：批处理样例文件；
14. `README.md`：模块说明与使用示例；
15. `requirements.txt`：依赖包清单（如需 Rich/Typer/Prompt Toolkit）。

## 13. 可替换性与边界约束（强制）
1. 应用层控制台模块只允许依赖接口层公开入口 `RequestHandler`，禁止直接依赖协同调度、RAG、Agent 具体实现；
2. 任何新增能力都只能发生在“输入组织、状态管理、结果展示、历史导出”四类职责中；
3. 文件输入功能仅负责传递路径或上传入口参数，不允许在控制台层实现文档解析、向量化、检索等业务逻辑；
4. 批处理与脚本执行只是多次调用统一入口的封装，不得新增绕过接口层的快捷通道；
5. 渲染器、历史存储、输入适配器都必须可替换，不影响上层使用方式；
6. 错误码、响应结构必须完全遵循系统统一规范，禁止控制台模块自行发明业务状态码。

## 14. 示例调用规范

### 14.1 最小运行示例
```python
from request_response_module.core.impl import RequestHandler
from console_app_module.core.impl import ConsoleApp

handler = RequestHandler(orchestrator=...)
app = ConsoleApp(handler=handler)
app.run()
```

### 14.2 批处理示例
```python
from console_app_module.core.impl import ConsoleApp

app = ConsoleApp(handler=handler)
app.run_batch_file("./examples/sample_batch.jsonl")
```

### 14.3 脚本模式示例
```text
# demo_commands.txt
/mode hybrid
/topk 8
请根据当前项目结构，给出最小可行开发计划
/history 5
/export ./out/history.json
/exit
```

## 15. 常见问题（FAQ）
1. **为什么控制台模块不能直接调用 RAG 或 Agent？**  
   因为系统架构明确要求应用层只作为用户入口，统一调用接口层，保证边界一致、便于替换与集成。

2. **为什么要支持批处理和脚本模式？**  
   这两类能力适合本地回归、演示彩排和大批量样例验证，能显著提升调试效率，同时不改变核心业务链路。

3. **文件输入是不是意味着控制台模块要解析文件？**  
   不是。控制台模块只负责收集文件路径或上传参数，实际文件解析与索引构建仍由数据层/接口层处理。

4. **多轮会话的上下文记忆在哪里实现？**  
   控制台只维护 `session_id`，是否真正使用会话上下文由接口层和状态存储模块决定。

5. **能否把控制台改成 Rich/Typer 风格？**  
   可以。只要实现相同抽象接口并保持输入输出协议不变，即可替换渲染和命令框架。

## 16. 版本变更说明

| 版本 | 日期 | 变更内容 |
| :--- | :--- | :--- |
| v1.0 | 2026-03-18 | 首版控制台交互模块独立设计说明书。 |
| v1.1 | 2026-03-18 | 在保持应用层边界不变的前提下，扩展多轮会话、Slash 命令体系、多行输入、批处理、脚本执行、文件输入、历史导出、指标摘要与渲染适配器设计。 |

返回[系统架构设计说明书](RAG与Agent系统架构设计说明书.md)
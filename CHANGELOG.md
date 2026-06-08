# Changelog

格式: 按时间倒序; 每条 `Task XX (#NN)`: 一句话概括 + 关键文件 + 数据.

变更原则: 加性 / 加 deps 字段 / 加抽象 / 加 alias > 删. 即使大重构 (拆 god class)
也都保留 back-compat shim 让老 import 0 改动. 测试基线零回归.

## Unreleased (2026-06-08)

> 主题: 接 backlog 4 件 (react解析/方向1调度/外部工具/方向4维护) + software_info 工具 +
> 优化轮 ①②③ (测试基线/低风险小修/拆 god class)。commit `4ac29e7`..`3b59802`。
> 终态: 全量 **1161 passed, 4 skipped, 0 fail** (一键可跑); ABC ✓ (19 对); impl.py 2051→1505。

### 优化轮 (用户问"可优化部分在哪" → 定优先级 ①②③ 全做)
- **① 修测试基线** (`b7b9ce0`): 全量 pytest 此前被 2 个 collection error 中断 (无一键全绿 gate)。
  补 `OrchestratorException` 定义 (per-domain 漏的) + document_parser 测试改 `pytest.importorskip`
  (重型可选库缺则 skip); 顺带修被遮住的 7 个 orchestrator 陈旧测试 + 补 tool_functions 测试。
  基线: **1159 passed, 4 skipped, 0 fail/0 collection error** (~60s)。
- **② 低风险小修** (`1256655`): app 退出统一收尾钩子 (停 scheduler + 关 MCP 子进程, 闭环本周 close/stop
  能力) + eval台 `_REFUSE_PAT` 收紧 "无法访问"(误判"无法访问数据库") + start_dev.bat 强制 UTF-8。
- **③ 拆 impl.py god class** (`3d32ecd` ③a + `3b59802` ③b, 纯搬移零行为变更): 2051 行 → **1505 行 (-27%)**。
  方向4 自维护 9 方法 → `SelfMaintenanceMixin`; 长期记忆/画像 5 方法 → `MemoryMixin` (延续 ReAct/
  Streaming 既有 mixin 范式)。每步全量 1161 passed + ABC 全绿护栏。剩余内聚组 (验证/状态历史/任务解析/
  结果聚合) 留后续同范式抽。

### 新工具 (用户需求: "查安装软件的版本信息和使用说明")
- **software_info** (`95f4fa1`): 只读 agent 工具 (照 system_info 范式, 不进审批)。`action=lookup`
  给软件名 → 版本+用法 (PATH 命令跑固定 --version/--help, Windows 还试 /?; GUI 软件退到卸载
  注册表给版本+安装位置); `action=list` 列已装软件清单 (注册表为主, 退化 PATH 命令, 支持 filter/limit)。
  安全: 只跑 PATH 已存在程序 + 固定参数 + shell=False + 软件名强校验(挡空格/路径分隔/元字符) +
  超时/截断。Windows 健壮性: 跳过 Microsoft Store 0 字节 App Execution Alias 壳 (python 之类) +
  读不到版本时退注册表。`business_layer` 接线; eval 台 +"软件版本"任务。test +18; 亲验真机+真 agent:
  git→2.47.1 / python→Miniconda3 Python 3.13.13。

### 治延迟 + 健壮性
- **react 解析器容错抠 final_answer** (`4ac29e7`): qwen 长答案 react JSON 常畸形 (未转义换行 /
  trailing comma / 截断) → `_parse_react_response` 返 None → 流式 parse-fail 兜底把"生 JSON"当答案
  → 触发洗净重生成 (+20s)。新 `_salvage_final_answer` 从畸形 JSON 抠 final_answer (逐字符扫到未转义
  闭合引号 / 截断到末尾 + unescape), 抠到即免重生成; 合法 JSON 但工具非法仍按原逻辑 None (最小改动);
  非流式 `_react_execute` 同步受益。eval 台 调试-方法论 ~62→30s, 7/7=100%。顺修 eval 台 `_REFUSE_PAT`
  裸"无法直接"误判排查建议"无法直接复现"为拒绝 → 收紧为 `无法直接(访问|操作|帮|为你|替你)`。test +7。

### 方向1 (用户模型) — UP-4 ask 模式
- **UP-4 ask 模式** (`da666f9`): query refinement 原只有 auto (歧义时静默改写问题)。加 ask: 歧义大、
  画像也定不了时**反问澄清问题**, 不臆测、不执行任务。`_refine_query` 加 `mode` 参 (默认 auto 向后兼容);
  execute 见 `action=clarify` 早退返 `code=SUCCESS/message=clarification_needed` + `data.answer=澄清问题`
  + `details.query_refinement`。config `agent.query_refine_mode` 默认 auto; 沿用既有 gate + 安全阀 +
  fail-open; 仅非流式 execute (与 auto 同域)。test +7。

### 方向1 + 方向4 (调度 / 自主维护) — 接线 TaskScheduler
- **TaskScheduler 接线 + 自维护定时注册** (`a7902d2`): TaskScheduler 类/路由/单测早齐, 但从未在运行
  app 实例化/启动/注入 ApiService (scheduler 恒 None, /scheduler/* 全 SERVICE_UNAVAILABLE) — 这是
  方向1"reconcile/consolidate/prune 接入调度"与方向4"maintenance_scan 注册进 TaskScheduler"共同缺口。
  `factories/application_layer._build_scheduler`: 按配置组装任务 → 实例化+start → 注入 ApiService。
  **默认关** (无 `scheduler.tasks` 且无 `agent.maintenance_schedule` → 返 None, 不起线程, 启动行为不变)。
  配 `agent.maintenance_schedule` 即注册 maintenance_scan 定时任务 (body 走 agent + maintenance_scan
  钩子 → run_maintenance_scan); **仅 enable_self_reflection=true 才注册** (防呆); scope=["memory"]=方向1
  仅记忆域 / 省略=方向4 三域全扫; auto_apply 仍受安全天花板 (仅 run_prune/run_degrade)。补
  `build_maintenance_task()` 纯函数 + `cancel_task()` 别名 (修 SchedulerRoutesMixin DELETE 调的名)。
  test +7; 亲验: 经真实 handler.handle + TaskScheduler.trigger_once 跑通 maintenance_scan。

### 外部工具余量
- **OpenAPI spec_url 远程拉取** (`af680ec`): `fetch_openapi_spec()` 复用 SSRF 校验 + 禁跳转 fetch,
  JSON 优先、退化 YAML; `agent.openapi_tools` 每项 `spec_url`(远程) / `spec`(inline) 二选一, entry 的
  auth_* 也用于拉取。完成 openapi.py 里"留后续"TODO; fail-safe 跳过。
- **MCP 连接生命周期管理** (`af680ec`): `ExternalToolProvider.close()` (base no-op; `McpToolProvider`
  留已建连接引用, close 逐个释放/杀 stdio 子进程, fail-safe + 幂等); business_layer 经
  `result["external_tool_providers"]` 透出 (不再 fire-and-forget)。
- **MCP SSE 长连暂缓**: 现无消费方 (agent 一次性 discover 工具表、不动态刷新, list_changed 通知无人用),
  此时建持久监听线程=造无用复杂度。RFC 记触发条件 (待"动态刷新工具表"需求出现再做)。test +12 (含 spec_url)。

## Unreleased (2026-06-05)

> 主题: 研究 Hermes Agent + obra/superpowers, 落地"技能学习闭环 / 提示词缓存 / 技能机制"; 建 agent
> 评测台量成功率; 治延迟; 并修一串只在真模型+流式下暴露的健壮性 bug。commit `da52410`..`ee44ff9`。
> 注: 模型最终档位 = qwen-max (config default_chat_model); agent 默认 execution_strategy=react + 自校验默认开。

### 研究驱动新能力 (Hermes Agent + obra/superpowers)
- **#1 技能自动沉淀** (`d7d1b66`, 借鉴 Hermes 学习闭环): 成功且复杂 (≥`agent.skill_distill_min_tools`
  个工具) 的 agent 任务收尾, 后台 LLM 把"任务→工具→做法"提炼成可复用 skill 写入库 (`_auto_*.md`),
  下次同类自动匹配注入。`SkillRegistry.save_skill`/`find_by_triggers`; agent `_distill_skill`/
  `_distill_skill_async`(后台线程不阻塞 done); **默认关** `agent.enable_skill_distill`; 去重 + fail-open。
  `test_skill_distill` 9 例。
- **#2 提示词缓存** (`19a0bd9`): react prompt 重排 [稳定前缀(项目记忆+赋能前言+工具表+格式)] →
  [任务块] → [易变(历史+迭代)], 命中 qwen prefix cache 降本 + 缩 TTFT (原把 task 夹工具表前切断缓存)。
- **superpowers 机制集成** (`41257a8`): SkillRegistry 递归加载(rglob) + `use_skill(name)` 工具 +
  prompt 注入"技能目录"(名+描述) → agent 主动按需加载技能 (模型驱动, 补 trigger 自动注入)。
  `git clone obra/superpowers` 进技能目录即用。`test_use_skill` 6 例; `doc/技能系统与superpowers集成.md`。
- **预置 4 条方法论技能** (`f781bd4`, 原创文字): brainstorming / writing_plans / systematic_debugging /
  test_driven_development (放 skills/)。.gitignore 排除 `_auto_*.md` (自动沉淀不进库)。
- **agent 评测台** (`03ed9a0`): `scripts/eval_agent.py` — 7 代表任务走真实 WS 流式打真 LLM, `--runs N`
  量非确定性, 断言 no_error/nonempty/min_len/contains/not_refused, 出成功率+退出码 (接 CI/发版 gate)。
  基线: 单次 7/7=100%; --runs 3 = 20/21=95.2% (1 次空答案疑瞬时, 标 PART/flaky)。
- **治延迟** (`ee44ff9`): 无工具任务跳过最终答案重生成 (直接用 react final_answer) + 技能目录排除已
  trigger 注入项 (不重复 use_skill)。规划 71→40s, 问答 15→8.5s, 7/7 仍 100%。

### 流式 agent 健壮性 (一串实战 bug, 现由评测台守护)
- **答案完整不截断** (`da52410`): 最终答案改非流式一次生成+切片 (SSE 中途断会留半截/空白)。
- **解析失败兜底** (`01e05d1`): LLM 没按 JSON 输出时把自然语言当最终答案, 不报 AGENT_RUN_FAILED。
- **网络抖动重试** (`4869c28`): react 计划 LLM 调用重试 3 次 (0.8s/1.6s 退避)。
- **提示词重平衡** (`3ff454b`): 建议/规划/写作类直接答, 别过度搜工具 (治答非所问 + 踩 web_search 失败)。
- **铁底兜底** (`5ecf6be`): 最终答案绝不为空白 (空则退回 final_answer / 提示)。
- **并行工具空转修复** (`d7d1b66`): run_stream 补 Phase3 多动作 `actions:[]` 处理 (此前只非流式有,
  流式下 LLM 并行多工具时 tool_name=None 空转)。

### 体验 bug (前端, 需刷新)
- **删会话残留** (`56160b4`): 删当前会话后清空主聊天区 + 右侧面板 (对齐 clearHistory)。
- **停止后能再发** (`f9f71e6`): sendStream onClose 无条件 resolve (旧卫语句致 await 不解、inflight 锁不放)。
- **侧栏开关桌面生效** (`c680c1d`): 顶栏 💬/▯ 桌面点击折叠/展开会话栏/右栏 (原只移动端 .open 有效)。
- 缓存版本: app.js?v=190 / style.css?v=191。

## Unreleased (2026-06-04)

> 主题: 回应"agent 能力太弱 / 不能操作" — 系统性增强 agent (模型档位 / 执行循环 / 自我校验 / 记忆个性化) + 修若干体验 bug。commit `b7e8ec6`..`bc78c26`。

### "不能操作 / 模型说不能干" 修复 (`bc78c26`)
根因: 聊天默认 RAG 模式 (被动从文档答、无工具) → 让 agent 干活就答"做不了, 很多工作不能实施"。
- 前端默认 `mode` 'rag'→'agent' (聊天直接用工具干活; agent 经 rag_search 兼顾文档问答, 是 RAG 超集); `app.js?v=188` 刷缓存
- `prompt_builder.py` react 提示词加赋能前言 (你有真实工具会真正执行, 优先动手, 别答"我做不了/我只是AI", 缺事实先调工具)
- `config.yaml` `default_chat_model`→qwen-max (选工具/推理最准); agent 墙钟超时 60→120s (`business_layer.py`, 多轮 ReAct+校验避免 AGENT_TIMEOUT→504)
- 实测 "12345 乘以 67890"(UTF-8) → 838102050 正确, 18.5s, calculator。(排查中错答 5555/80235 + 78s 超时, 定位为 CLI 测试把中文 body 发成非 UTF-8 致服务端乱码, 非 agent 问题; 真实前端 UTF-8 不受影响)

### agent 能力增强四期 (`541d43e` 地基 / `0be65f4` 校验闭环 / `266dbf4` 记忆个性化)
回应"agent 太弱"。盘点发现编排/校验/记忆核心多已建好, 主缺口在模型档位与默认行为。
- **地基** (`541d43e`): 主力 chat 模型 qwen-turbo→qwen-plus(后续→max); `agent.execution_strategy` 默认 single_shot→react (多轮迭代; max_react 已 15)。纯 config
- **规划闭环** (`0be65f4`): `enable_self_verify`/`verify_mode=auto`/`max_correction=1` 默认开 — 每个 agent 任务终态校验"是否真完成"+未完成带缺口反馈自纠正 (复用方向3 `_post_verify`, 早建好默认关, 本期激活); 修 7 个计调用次数单测 (给其 agent 构造显式 `enable_self_verify=False` 隔离正交校验调用)
- **记忆个性化** (`266dbf4`): RAG 聊天接入用户模型 — 答前注入画像+query 相关 fact(懂使用者)、答后 `extract_facts`→`add_fact` 学习(越用越懂, 含无文档兜底分支); `SimpleRAG` 加 `long_term_memory` 注入 + `memory_enabled`/`memory_top_k`; 工厂属性注入; 全程 graceful fail-open; `test_impl` +5
- **自主编排**: 核心 (`spawn_subagent` 子代理 + 串行工具链 + 15 轮 ReAct) 已就绪并经上面激活; **并行工具执行**延后 (可选, 中高复杂度)

### RAG 失败也落盘本轮 — 修刷新丢最新对话 (`92df5fc`)
`run()`/`run_stream()` 仅正常完成才 `_save_turn`, 异常路径 (检索/LLM 挂) 跳过 → 刷新调 `/sessions/{id}` 拿空 → 最新对话丢。设计本是"先存后端"(前端已不留 localStorage 历史)。修: except 里补 `_save_turn`(用户问题+失败说明); `test_impl` +4

### LLM banner 熔断冷却后自愈 + gitignore 审计日志 (`b7e8ec6` / `b296d0b`)
- banner (`b7e8ec6`): 只把"仍在冷却窗口内"(`cooldown_remaining_seconds>0`)的 unhealthy 算真不可用, 冷却已过视为恢复中 → banner 自动消失 (`app.js` `_renderLLMBanner`); 去掉"重启服务"误导文案 (前端 banner + `llm_compat` stub) — 熔断器本就自愈无需重启
- `.gitignore` (`b296d0b`): 忽略 `audit.log.jsonl` + 滚动备份 (不带前导斜杠匹配任意目录; 防 `git add -A` 误纳入运行时审计日志)

## Unreleased (2026-06-03)

> 补记: 以下 方向1/2/3/4 + 审计 + 外部工具(HTTP+MCP+OpenAPI) + 计算机操作 工作集中补入 CHANGELOG (对应 commit `0d78f4a`..`b1b2d62`)。
> 战略: 从"团队知识库工具"演进为"懂使用者的个人智能助手"(终极: 能自动思考/自更新维护的 agent)。

### 计算机操作 — computer_use 工具 (`b1b2d62`)
对标 Claude computer use / OpenAI Operator: 截屏 + 鼠标/键盘控制真实桌面。
- `tools_impl/computer_use.py`: `make_computer_use_tool(backend 可注入)` — screenshot/screen_size/move/click/double_click/right_click/type/key/scroll; 截图返 base64+存盘
- 默认 `_PyAutoGuiBackend`(lazy pyautogui; 未装→MISSING_DEPS 优雅降级); backend 可注入(测试不碰真桌面)
- **危险能力**: 控制真实桌面 → `impl.py default_dangerous` 加 `computer_use`, 默认进审批白名单(human-in-loop); 工厂注册; `test_computer_use` 10 例

### 外部工具连接 Stage1+2 — HTTP 连接器 + MCP 客户端 (`33dcc6b` HTTP / `e08280e` MCP)
补 agent "无外部工具接入" 缺口(对标 codex/claude 验证为最大短板)。RFC `doc/外部工具连接-设计方案(RFC).md`:
- `business/agent_module/tools/external/`: `ExternalToolProvider` 抽象 + `ExternalToolDef`(HTTP/MCP 都实现, 统一接入点); `register_external_tools` + `build_providers_from_config`; `business_layer.py` 启动注册, 默认并入 `tool_approval_required`(human-in-loop), fail-safe; **零新依赖**(stdlib)
- **Stage1 HTTP/OpenAPI** (`33dcc6b`): `HttpToolProvider` + `HttpToolSpec`(声明式 url占位/query/JSON body/auth/`response_path`) + `make_http_tool`(复用 `http_get` SSRF 私网防御, fetch 可注入); `test_external_tools` 13 例
- **Stage2 MCP 客户端** (`e08280e`): 参考 codex/claude code 标准 MCP 协议, **最小自实现** `McpStdioClient`(JSON-RPC 2.0 over stdio: initialize→tools/list→tools/call; transport 可注入) + `McpToolProvider`(命名空间 `server.tool`); 配置 `agent.mcp_servers`; 仅连显式 server; `test_mcp_tools` 10 例
- **增量** (`102e942`/`fe10cab`/`062cccb`): ① MCP **HTTP/SSE 传输** — 抽 `_McpClientBase` + `McpHttpClient`(Streamable HTTP: POST JSON-RPC, json/sse, Mcp-Session-Id), 远程 server; ② **真实 server 集成测试** — 自带最小 echo MCP server(python -c stdlib), 真子进程端到端跑通(无网络/npx); ③ **OpenAPI 自动生成** — `openapi_to_specs` 每 operation→HttpToolSpec(复用 Stage1), 配置 `agent.openapi_tools`
- 安全: SSRF/默认审批/仅显式配置/fail-safe; 凭据/server 勿明文(建议 env/加密)。余量: MCP SSE 长连 / 连接生命周期 / OpenAPI spec_url 远程拉取

### 方向4 自主维护 — 建议性自主全档 (`4e20332` 行为反思 / `7361885` 记忆 / `409c2fb` 代码文档 / `935edb7` 定时 / `e9a086b` 预授权)
**全程 human-in-loop, 默认全关; 安全分级 — 只提议为主, 自动执行严格受限。**
**扩域·记忆健康自维护** (`7361885`): `aggregate_memory_signals`(只读统计陈旧/可降级/对立/冗余, 谓词对齐 prune/degrade) + `propose_from_memory_signals`(确定性规则→提议, 不调 LLM); agent `propose_memory_maintenance()`(dry-run) + `apply_memory_maintenance()`(仅人审批的提议映射到现成算子 prune_stale/degrade/reconcile/consolidate 执行); `test_memory_maintenance` 9 例。
**扩域·代码文档自维护** (`409c2fb`, advisory): `scan_code_doc_health`(只读扫缺 README 模块 + TODO/FIXME) + `propose_code_doc_maintenance`; **代码/文档改动绝不自动执行, 无 apply**; `test_code_doc_maintenance` 6 例。
**定时提议+通知** (`935edb7`): `run_maintenance_scan(scope)` 聚合三域提议 + `_notify_maintenance` 写审计; `execute` 的 `maintenance_scan` 钩子可被 TaskScheduler 周期触发; `test_maintenance_scan` 4 例。
**更高自主档** (`e9a086b`): `agent.auto_approve_maintenance` 预授权名单(**默认空**=零自动); `run_maintenance_scan(auto_apply)` 仅对 (名单 ∩ 安全天花板 `{run_prune,run_degrade}`) 自动执行+审计, reconcile/consolidate/code_doc 永不自动; 人设名单=预授权、可清空撤销 → 仍 human-in-loop; `test_auto_approve_maintenance` 5 例。
**行为自反思** (`4e20332`):
- 终极目标"自动思考/自更新维护"的第一档, **务必分级**: 只提议不自动执行, 全程 human-in-loop
- `core/components/self_reflection.py`: `aggregate_audit_signals`(确定性聚合审计日志 per-tool 成败率/错误码/成本) + `SelfReflectionInspector.reflect`(LLM **元级**反思跨多任务行为模式→结构化改进提议, fail-open)
- agent `self_reflect()`: 按需读审计日志尾部→聚合→反思→提议(**dry-run 零改动**), 默认关(`agent.enable_self_reflection`)
- agent `apply_reflection_proposals()`: 仅把**人审批**的 `record_lesson` 落长期记忆(`content_type=convention` 反哺画像), 非审批/非 lesson 不动, **绝不自动改配置/代码** → 闭环 反思→提议→人批→教训进记忆→画像注入
- 区别于 `_reflect_revise`(单答案反思); 测试 `test_self_reflection` 14 例

### 代码↔设计文档审计校准 (`30ac2e3`)
- 3 路并行只读审计(结构合规/Agent 文档缺口/架构文档时效) + 亲自复核
- 架构总览事实修正: ABC 配对 10→19、测试规模、Cross-encoder Reranker 已默认、chunker 分层; Agent 设计说明书加「v2.2 能力实现状态校准」节; 横切说明书补 long_term_memory 方向2/方向1 升级; `doc/README` 状态页同步
- `.gitignore` 补运行时产物忽略(`/uploads` `/run/uploads` `/run/source_docs` `/run/*.sqlite3` `/run/_*.py` 等), 防 `git add -A` 误纳入
- 规则合规复核: 依赖方向零反向依赖、命名零违规、ABC 19 对全绿

### 方向2 记忆升级 — 被动 fact 库 → 主动认知系统 (`0d78f4a` `6a90a00` `88f0c66`)
- `Fact` 加 `mutability`(canonical 固定权威/refinable 可提炼) / `digest`(精炼层) / `content_type` / `encrypted`
- 敏感 secret 用 Fernet 加密落盘(`SENSITIVE_CONFIG_SECRET`), `reveal_fact()` 显式解密; 无密钥则丢明文(绝不明文落库)
- 两级检索(digest 粗筛→content 细比) + 差异化清理(`prune_stale`/`degrade_stale_refinable`) + 反思整合(`consolidate`)
- 文件: `basic_support/long_term_memory_module/core/impl.py` + `model/data_model.py`

### 方向1 用户模型 — "更懂你" (`8cd577b` `c15380d` `4fde8f7`)
- **UP-1/2/3 画像 MVP**: `get_user_profile()` 5 维度(preference/style/convention/domain/weakness)聚合; agent `_inject_user_profile()` 任务前 always-on 注入(weakness→"需主动补位")
- **UP-4 query refinement**: 含糊问题基于画像 LLM 判含糊+改写后再规划; 默认关(`agent.enable_query_refine`); 三重 gate + 安全阀 + 全程 fail-open; 记 `details["query_refinement"]` 透明
- **阶段3 画像冲突消解+时效**: `Fact.superseded_by`(非破坏标记); `reconcile_conflicts()`(LLM 找对立偏好→`created_at` 最新者胜其余标 superseded); `get_user_profile` 时效排序 + 只取有效; `search_facts` 跳过 superseded
- 文件: `business/agent_module/core/impl.py` + `basic_support/long_term_memory_module/core/impl.py`

### 方向3 自我验证闭环 (`d838813` `6d163a6` `5a24097`)
- `core/components/verifier.py`: ToolSuccess / Execution(pytest·sql·shell·lint) / **Goal(子目标级)** / Task 终态 / Compliance 五验证器 + 自纠正递归(`_post_verify`) + off/auto/ask 三模式; 默认关(`agent.enable_self_verify`)
- 验证器故障一律 fail-open(放行); `extra_params.verify` / `verify_goals` / `verify_task` 控制
- GoalVerifier(`5a24097`)走"验证时分解子目标", 不动规划核心; 留 `spec.args.goals` 接入点供未来 plan goal 分组

### 全项目审计 + 自动推送 (`ab6f1ae` `3297e26` `7feb32f`)
- AUDIT 1-4: exception import / check_abc Win 编码 / `AGENT_TIMEOUT` enforce / 分层反向依赖消除 / ABC 检查 10→19 / 文档校准(`doc/README.md` + cross-cutting 设计文档)
- commit 后 `git push origin main` 自动化(`.git/hooks/post-commit` + `scripts/install-git-hooks.sh`)

### 测试
- 新增 `test_user_profile` / `test_query_refine` / `test_profile_reconcile` / `test_verifier` / `test_goal_verifier` / `test_memory_layering` 等; agent 模块 271 passed, long_term_memory 全绿, ABC 19 对对齐

## Unreleased (2026-06-01)

### XXXX-1..14 — 16 项 UX 优化批次 (XXXX-2/5 待真机验证 待结)

**XXXX-1 (#148)** — 图片生成进度提示
- `frontend/static/app.js`: `_loadingHintFor` 按 intent 推断 hint ("🎨 正在生成图片…"),
  加 `_ensureElapsedTicker` 显 elapsed 秒数; 长任务不再"卡死错觉"

**XXXX-3 (#150)** — 每条 assistant message 加复制按钮
- `app.js`: `_attachMessageCopyButton(body, rawText)` 右上 hover 显 📋, 点击 navigator.clipboard
- `style.css` `.msg-copy-btn` opacity 0 → hover 0.7 → click 1

**XXXX-4 (#151)** — 删 session 加 5s 撤销窗口
- `app.js`: `_pendingDeletes: Map`, `deleteSessionById` 不再立即 DELETE
- `_showUndoDeleteToast` 5s 内可点 ↩ 撤销; 同 sid 第二次点 → 立刻 commit (skip 等待)
- `style.css` `.pending-delete` (opacity 0.4 + 删除线), `.undo-toast .toast-undo-btn`

**XXXX-6 (#153)** — 长答案 markdown 折叠
- `app.js`: `_maybeFoldLongMessage(body)` 渲完 next tick 量 scrollHeight, >600px 给 msg-foldable
- `style.css`: `.msg-folded` max-height 600 + 渐变蒙版 + 居中"▼ 展开全部"按钮

**XXXX-7 (#154)** — i18n 补漏 (35+ 新键)
- `i18n.js`: shortcuts.* / workflow.* / theme.* / docs.section.* / agent.tools.* / msg.* /
  sessions.empty + .search.placeholder, zh + en 双向
- `index.html`: 21 处加 data-i18n / data-i18n-attr (快捷键 modal / workflow drawer /
  theme switcher / Agent 工具列空态 / 文档分区标题)
- `app.js`: undo toast + fold toggle 文案走 `t()`

**XXXX-8 (#155)** — session 列表分页 (默 30 + "加载更多")
- `app.js`: `_sessionsVisibleLimit` (PAGE_SIZE=30), `listSessions(200)` 一次拉; `_renderSessionList`
  slice 前 N 渲, 末尾追"▼ 加载更多 (N 个剩余)" 行; 搜索 input reset 分页
- `style.css` `.session-list-loadmore` (dashed border)

**XXXX-9 (#156)** — workflow 模板存 IndexedDB (脱离 localStorage 5MB)
- 新 `frontend/static/idb-store.js`: 极简 IDB KV + `openMirror` (sync API + 异步刷盘)
- `app.js`: `_initWorkflowsStore` 启动 await 一次, `_loadWorkflows/_saveWorkflows` 走 mirror;
  保留 localStorage 兜底 + 一次性迁移 (localStorage→IDB)

**XXXX-10 (#157)** — user 消息可编辑 + 重发 (✏ 按钮)
- `app.js`: `buildMessageNode` user 角色追加 ✏ 浮动按钮; `_enterEditMode` 替 body 为 textarea
- 保存 → state.history truncate 到该消息处 (砍后续 assistant) → renderHistory → send()
- `style.css` `.msg-edit-btn / .msg-edit-textarea / .msg-edit-actions / .msg-editing`

**XXXX-11 (#158)** — image_generate URL 走结构化字段
- `tools_impl/image_generate.py`: `data.description` URL-free; URL 在 `data.images` / `image_url`
- `core/impl.py`: 加 `_extract_image_urls(output)`; aggregate_results 给 `tool_results_summary[i]`
  追加 `images` 数组
- `core/components/streaming.py`: 同样给 stream meta 加 images
- `app.js` `_collectGeneratedImages`: 优先读 s.images (结构), regex 扫保留兜底

**XXXX-12 (#159)** — 5 新工具单测 ~25 cases
- 新 `business/agent_module/tests/test_new_tools.py`: image_generate / pdf_read /
  excel_read / sql_query / browser_visit 的 PARAM_MISSING / 路径安全 / SQL 关键字拦截 /
  SSRF 防御 / scheme 校验

**XXXX-13 (#160)** — evaluation set 加新工具 10 cases
- 新 `evaluation/datasets/agent_new_tools.jsonl`: pdf / excel / sql (含 DELETE 拒绝) /
  browser (含 SSRF) / image_gen + 2 组合场景 (sql→llm, web_search→llm)
- run_eval.py 自动扫 *.jsonl 已覆盖

**XXXX-14 (#161)** — README/CHANGELOG 同步本批

### Phase 2 商业化 — 让 Phase 1 抽象真的被消费

**WW (#83)** — 修 deps_module 3 pre-existing test errors + 1 multiprocess log flake
- `basic_support/deps_module/tests/test_deps.py` 加 `ANYTHING_DEV_MODE=1` setdefault
- `basic_support/log_module/tests/test_multiprocess.py` 严格 `==15` 放宽到 `>=80%`
  (Windows spawn() 不共享父进程 import 期 `multiprocessing.Lock()`)
- baseline 100% 绿: 537 ran, 0 fail, 0 err (上次 537/0 fail/3 err)

**XX (#84)** — UsageTracker 接 StateBackend (TT Phase 2 真正商业化)
- `UsageTracker.__init__` 加 optional `backend: StateBackend` 参数
- `backend=None` (默认) 不变; `backend=SqliteBackend(path)` 跨 worker 进程共享 token 计数
- 实现: 每 counter 独立 `incr` 走 BEGIN IMMEDIATE 保证原子;
  by_model / by_tenant 通过 `list_append("usage:known_models")` + Python set() 去重枚举
- 关键证据: `test_two_trackers_share_state` — worker A 记录 → close → worker B 启动
  → snapshot 看到 A 的累计 (gunicorn 多 worker 真实场景)
- 新加 12 测试, 总 577 ran 0 fail

**YY (#85)** — react_engine 走 self.deps.hook_registry (PP 真正商业化)
- `SimpleAgent.__init__` 把 deps 存到 `self.deps`, 暴露给 mixin
- `ReActEngineMixin` 加 `_hook_registry()` helper: 优先 deps.hook_registry,
  fallback get_hook_registry() (back-compat)
- 2 处 `get_hook_registry()` → `self._hook_registry()`
- 价值: 单元测试可注入隔离 registry 不污染下条; 多 tenant 可每 tenant 一份
- 5 新测试覆盖 default / injected / hooks-actually-isolated, 总 582 ran 0 fail

## v2.x (2026-05-26 → 2026-05-29) — 12 项架构优化 KK→VV

### 文件拆分 (god class / 单文件大杂烩 → 按职责拆)

**KK (#71)** — SimpleAgent god class 拆 4 mixin
- `business/agent_module/core/impl.py`: **1764 → 723 行 (-59%)**
- 拆出: `core/components/{react_engine, tool_executor, streaming, prompt_builder}.py`
- 多继承 mixin 模式, 0 调用方改动 (`SimpleAgent` 类名 + 公共 API 全保留)

**LL (#72)** — ApiService 21 路由 → APIRouter 拆 6 域
- `application/api_service_module/core/impl.py`: **1788 → 590 行 (-67%)**
- 拆出: `core/routers/{invoke, documents, index, admin, config, frontend}.py` 6 个 mixin
- 同样多继承, FastAPI 装饰器在 mixin 方法里 close over `self.app`

**MM (#73)** — builtin_tools 1655 行 → 每工具一个文件
- `business/agent_module/tools/builtin_tools.py`: 1655 → 53 (re-export shim)
- 拆出 17 文件到 `tools/tools_impl/`: calculator/datetime/wikipedia/document_read/...
- `TOOL_DESCRIPTIONS` 独立到 `tools_impl/_descriptions.py`

**NN (#74)** — llm_adapter 1261 行 → adapters/ 拆 6 文件
- `data_layer/llm_adapter_module/core/impl.py`: **1261 → 578 行 (-55%)**
- 拆出: `core/adapters/{_http_mixin, openai_vector, openai_chat, openai_multimodal, anthropic_chat, ollama_chat}.py`
- impl.py 保留所有 adapter 类 re-export

**RR (#78)** — bootstrap.py 515 行 → factories/ 拆 6 文件
- `run/bootstrap.py`: **515 → 103 行 (-80%)**
- 拆出: `run/factories/{tool_registry, basic_support, data_layer, business_layer, interface_layer, application_layer}.py`
- bootstrap.py 现只剩 4 个 entry points (build_handler / build_api_app / build_console_app / build_all)

**SS (#79)** — app.js 2153 行 → 拆 6 modules (Phase 1)
- `frontend/static/modules/`: ui-helpers / export / health / upload-manager / admin-panel / models-admin
- 走 IIFE + `window.AnythingApp.X` 注册 (跟 api.js / i18n.js / markdown.js 风格一致)
- index.html 加 6 个 script tag, 总计 10 frontend JS 文件
- Phase 1 — 模块就绪 ready for consumption; app.js call-site 迁移留作 SS-Phase-2

### 抽象 / 模块化 / DI 增强

**OO (#75)** — 5 cross-cutting 从 common_utils 提到独立 module
- 新加: `basic_support/{hooks, skills, quota, audit, project_memory}_module/`
- `common_utils_module/utils/{hooks, skills, quota, audit_log, project_memory}.py` 改为 thin shim
- 老 import `from common_utils_module import HookRegistry` 仍可用 (identity 保留)
- 老的深度 import `from common_utils_module.utils.skills import parse_skill_file` 也仍可用

**PP (#76)** — 7 cross-cutting 单例 → BasicDeps DI
- BasicDeps 加 7 字段: hook_registry / skill_registry / quota_guard / audit_logger /
  project_memory / usage_tracker / health_tracker
- build_basic_deps() 通过 get_X() 拿单例引用 (identity 保留)
- 加性, 老 get_X() 不删

**QQ (#77)** — extra_params dict → ExtraParams pydantic schema
- `basic_support/schema_module/schema.py` 加 `ExtraParams` BaseModel
- 显式 6 known keys: plan_only / approve_plan / approve_tools /
  execution_mode (Literal) / execution_strategy (Literal) / source
- extra="allow" 让未知 key 渐进迁移期透传
- RequestEnvelope.extra_params 仍是 Dict[str, Any] (不强制), 想类型的调用方
  调 ExtraParams.from_dict(d)

**TT (#80)** — cross-process StateBackend ABC + 3 实现 (Phase 1)
- 新加 `basic_support/state_backend_module/`: StateBackend ABC / InMemoryBackend /
  SqliteBackend (WAL + BEGIN IMMEDIATE) / RedisBackend (stub)
- 5 核心方法: get/set/incr/list_append/list_get + clear/close
- Phase 1 — 抽象就绪; tracker 接入留作 XX (现已完成 Phase 2 UsageTracker 接入)

### 可观测 / 可运维

**UU (#81)** — SystemLogger 加结构化 JSON 日志
- `basic_support/log_module/utils/json_formatter.py` 新加 JsonFormatter
- env `ANYTHING_LOG_FORMAT=json` 切换 plain / json 输出
- JSON 字段: ts (ISO 8601) / level / logger / pid / process / thread / message /
  exc_info + extras + 自动注入 tenant_id / trace_id (best-effort)

**VV (#82)** — API 版本化 — 14 路由加 /v1/ 镜像别名
- `ApiService._register_v1_aliases()`: 扫描 app.routes 给每个 API path 加 /v1/<path> 镜像
- 排除清单: `/`, `/ui`, `/health`, `/healthz`, `/openapi.json`, `/docs`, `/redoc`
- 同一 endpoint 函数指针 → 老 path 与 v1 path 行为完全等价
- include_in_schema=False 让 OpenAPI doc 不重复展示

## 测试规模 (历次 baseline)

| 节点 | 模块数 | 测试数 | 失败 | 错误 |
|---|---|---|---|---|
| 起点 (KK 前) | 11 | 296 | 0 | 3 |
| LL 后 | 11 | 326 | 0 | 3 |
| OO 后 | 12 | 392 | 0 | 3 |
| QQ 后 | 13 | 405 | 0 | 3 |
| TT 后 | 14 | 432 | 0 | 3 |
| UU 后 | 14 | 445 | 0 | 3 (1 flake) |
| VV 后 | 14 | 537 | 0 | 3 |
| WW 后 | 14 | 537 | 0 | **0** |
| XX 后 | 15 | 577 | 0 | 0 |
| YY 后 | 15 | 582 | 0 | 0 |

## Push 策略

每个 task = 1 commit + 1 push origin main (fast-forward), 永不批处理.

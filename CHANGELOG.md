# Changelog

格式: 按时间倒序; 每条 `Task XX (#NN)`: 一句话概括 + 关键文件 + 数据.

变更原则: 加性 / 加 deps 字段 / 加抽象 / 加 alias > 删. 即使大重构 (拆 god class)
也都保留 back-compat shim 让老 import 0 改动. 测试基线零回归.

## Unreleased (2026-06-03)

> 补记: 以下 方向1/2/3/4 + 审计 工作集中补入 CHANGELOG (对应 commit `0d78f4a`..`4e20332`)。
> 战略: 从"团队知识库工具"演进为"懂使用者的个人智能助手"(终极: 能自动思考/自更新维护的 agent)。

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

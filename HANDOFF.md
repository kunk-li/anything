# 会话交接文档 (Session Handoff)

> 给下一个会话的接力棒。无需读全程 transcript，读这份 + `MEMORY.md`(auto-memory 自动注入) 即可接上。
> 最后更新: 2026-06-05 · 最新 commit: `12bf5b4` · 分支: `main`(已同步 origin; 注: 网络间歇不稳, post-commit 自动 push 偶失败, 手动 `git push origin main` / 连通后重试即可)

---

## 1. 项目一句话
**Anything** — 自部署的 RAG + Agent 平台 (Python 四层架构 `basic_support/data_layer/business/interface/application` + `run/` 启动层 + vanilla-JS 前端)。
当前战略: 从"团队知识库工具"演进为**懂使用者的个人智能助手** (终极目标: 能自动思考/自更新维护的智能 agent)。

## 2. 用户的产品愿景 + 4 方向路线 (用户定的优先级: 3→2→1→4)
| 方向 | 内容 | 状态 |
|---|---|---|
| **方向 3** 自我验证闭环 | 执行类任务"做到位"验证 + 自纠正 | ✅ 阶段1-2 + GoalVerifier done (plan 分组可选深化) |
| **方向 2** 记忆升级 | 被动 fact 库 → 主动认知系统 | ✅ 阶段1-3 done (用户 5 点需求全兑现) |
| **方向 1** 用户模型 | "更懂你" — 画像 + 主动应用 | ✅ MVP(UP-1/2/3)+UP-4+阶段3 done (收尾) |
| **方向 4** 自主维护 | 自动思考/更新 (终极目标) | 🔧 建议性自主全档 done: 行为自反思/记忆健康/代码文档/定时提议通知/预授权自动(默认空); 终极目标持续演进 |

## 3. 已完成 (累计, 全在 origin/main)
0新. **最新会话 (2026-06-05): 研究 Hermes+superpowers→落地技能学习闭环 + 建评测台 + 治延迟 + 修流式健壮性串 (全在 origin/main, `da52410`..`ee44ff9`)** —
   ① **#1 技能自动沉淀** (`d7d1b66`, 借鉴 NousResearch/hermes-agent 学习闭环): 成功且复杂(≥`skill_distill_min_tools`工具)的 agent 任务收尾, 后台线程 LLM 把"任务→工具→做法"提炼成可复用 skill 写库(`skills/_auto_*.md`), 下次同类自动匹配。`SkillRegistry.save_skill`/`find_by_triggers`; **默认关** `agent.enable_skill_distill`; fail-open。test 9 例
   ② **#2 提示词缓存** (`19a0bd9`): react prompt 重排 [稳定前缀(记忆+赋能前言+工具表+格式)]→[任务块]→[易变(历史+迭代)], 命中 qwen prefix cache 降本+缩 TTFT
   ③ **superpowers 机制集成** (`41257a8`, obra/superpowers): SkillRegistry 递归加载(rglob) + `use_skill(name)` 工具 + prompt 注入"技能目录"(名+描述)→ agent 主动按需加载(模型驱动, 补 trigger 自动注入); `git clone` superpowers 进技能目录即用。test 6 例; `doc/技能系统与superpowers集成.md`。**预置 4 条方法论技能** (`f781bd4`, 原创): brainstorming/writing_plans/systematic_debugging/test_driven_development
   ④ **agent 评测台** (`03ed9a0`): `scripts/eval_agent.py` — 7 代表任务走**真实 WS 流式打真 LLM**, `--runs N` 量非确定性, 断言 no_error/nonempty/min_len/contains/not_refused, 出成功率+退出码(可接发版 gate)。抓只在真模型/真流式下暴露的回归。基线单次 7/7=100%、--runs 3=20/21=95.2%
   ⑤ **治延迟** (`ee44ff9`→`12bf5b4`): 无工具任务跳过最终答案重生成(直接用 react final_answer, 省一次 LLM 调用+不丢上下文)。`12bf5b4` 用评测台当复现器诊断后收尾两件事: (a) 技能目录**有 trigger 命中就整个抑制**(原只排除命中那条, 不够 — agent 仍会去加载目录里其它技能, 偶发 +20~25s 无增益); (b) **流式收尾洗净"生 JSON"** —— 上面跳过重生成意外暴露: react 解析失败时 final_answer 退化成原始 JSON 串(parse-fail 兜底)会被直接喷给用户, 故 final_answer 是生 JSON 时即便无工具也重生成一次干净作答 + 铁底兜底加挡生 JSON。规划 71→37s、问答 15→8s、--runs 2 = 14/14 100%
   ⑥ **流式 agent 健壮性串** (评测台守护): 答案完整不截断(`da52410` 最终答案改非流式生成+切片) / 解析失败兜底(`01e05d1` 自然语言当 final_answer 不报错) / 网络抖动重试(`4869c28` 3 次退避) / 提示词重平衡(`3ff454b` 建议规划类直接答别过度搜工具) / 铁底兜底(`5ecf6be` 答案绝不空白) / 并行工具空转修复(`d7d1b66` run_stream 补多动作 actions:[] 处理) / system_info 只读本机状态工具
   ⑦ **前端体验** (需刷新): 删当前会话清空主区+右栏(`56160b4`) / 侧栏开关桌面生效(`c680c1d` 顶栏💬▯) / 停止后能再发(`f9f71e6`)。`app.js?v=190`/`style.css?v=191`
   全程 test: agent **387** (含 run_stream 首批 4 例 — 流式路径此前零单测, 是本会话流式 bug 盲区) + 既有全套 passed; ABC 19 全绿
0旧. **会话 (2026-06-04): "agent 太弱/不能操作" 系统性增强 + 体验修复 (全在 origin/main, `b7e8ec6`..`bc78c26`)** —
   ① **"不能操作/模型说不能干"修复** (`bc78c26`): 根因=聊天**默认 RAG 模式**(被动从文档答、无工具)→让 agent 干活就答"做不了"。前端默认 `mode` rag→**agent**(state.mode @ app.js, agent 经 rag_search 兼顾文档问答) + react 提示词**赋能**(prompt_builder: 有真实工具会真执行/优先动手/别说"我做不了") + `default_chat_model`→**qwen-max** + agent 墙钟超时 60→**120s**(business_layer, 避免多轮循环 AGENT_TIMEOUT→504)。实测 12345×67890→838102050 对、18.5s。`app.js?v=188`
   ② **agent 增强四期** ("能力太弱"): 地基(`541d43e`: qwen-turbo→plus(后→max) + `agent.execution_strategy` 默认 single_shot→**react**; max_react 已 15) / 规划闭环(`0be65f4`: `enable_self_verify`+`verify_mode=auto`+`max_correction=1` **默认开**, 复用方向3 `_post_verify` 执行→校验→自纠正; 修 7 个计次单测) / 记忆个性化(`266dbf4`: **RAG 聊天接入用户模型** — 答前注入画像+相关 fact、答后 `extract_facts`→`add_fact` 学习含无文档兜底; SimpleRAG 加 `long_term_memory`+工厂属性注入; graceful) / 自主编排(核心 spawn_subagent+串行链+15轮 ReAct 已就绪, **并行执行延后**)
   ③ **RAG 失败补存** (`92df5fc`): `run()`/`run_stream()` 异常路径(检索/LLM 挂)也 `_save_turn`(用户问题+失败说明), 修"刷新丢最新对话"(设计本是先存后端, 前端不留 localStorage 历史)
   ④ **banner 自愈** (`b7e8ec6`): 熔断冷却结束 banner 自动消失(cooldown_remaining>0 才算挂)+去"重启服务"误导(熔断器本自愈); **gitignore** (`b296d0b`): 忽略 `audit.log.jsonl`+滚动备份
   全程 test: agent 356 + rag 26 + 既有全套 passed; ABC 19 全绿
1新. **上一会话 (方向1→2→3→4 + 文档审计 + 外部工具 Stage1+2, 全在 origin/main)** —
   ① **方向4 自主维护 (建议性自主全档)** — a) 行为自反思 (`4e20332`: `self_reflect`/`apply_reflection_proposals`, 审计日志+LLM 元级反思); b) 扩域·记忆健康 (`7361885`: `propose/apply_memory_maintenance`, 确定性+复用 prune/degrade/reconcile/consolidate); c) 扩域·代码文档 (`409c2fb`: `propose_code_doc_maintenance`, advisory 只读不自动改); d) 定时提议+通知 (`935edb7`: `run_maintenance_scan` 聚合三域+审计通知, `execute` 的 `maintenance_scan` 钩子可被 TaskScheduler 周期触发); e) 更高自主档 (`e9a086b`: `auto_approve_maintenance` 预授权策略**默认空**=零自动, 仅安全确定性算子 run_prune/run_degrade 可自动)。**全程 human-in-loop, 默认全关**
   ② **代码↔设计文档审计校准** (`30ac2e3`): 3 路并行审计 + 亲验; 架构总览事实修正(ABC 10→19/测试规模/reranker 默认/chunker 分层)、Agent 文档加 v2.2 能力校准节、横切文档补记忆升级、CHANGELOG 补方向1/2/3、`.gitignore` 加固(防 `git add -A` 误纳入运行时产物); 规则合规复核(依赖/命名零违规)
   ③ **方向3 GoalVerifier** (`5a24097`): 子目标级验收, opt-in, 不动规划核心
   ④ **方向1阶段3** (`4fde8f7`): 画像冲突消解(`reconcile_conflicts`/`superseded_by`) + 时效排序
   ⑤ **UP-4 query refinement** (`c15380d`): 含糊问题基于画像改写, 默认关, fail-open
   ⑥ **外部工具连接 Stage1+2+增量** (`33dcc6b`/`e08280e`/`102e942`/`fe10cab`/`062cccb`): 补 codex/claude 对标最大短板。HTTP 连接器 + **MCP 客户端(stdio + HTTP/SSE, 参考 codex/claude code 标准协议自实现无 SDK)** + OpenAPI 自动生成工具 + 真实 server 集成测试(自带 echo server 真子进程) — 统一 `ExternalToolProvider` 抽象 + 工厂接线; 配置 `agent.external_tools`/`mcp_servers`/`openapi_tools`; SSRF+默认审批+fail-safe; test 37 例
   全程 test 共 83 例 + agent 322/long_term_memory 全套 passed + ABC 19 全绿
1. **全项目审计** — 5 个并行 agent 扫描代码 vs 设计规范，亲自复核 3 条 CRITICAL (修正 2 处误报)
2. **AUDIT 1-4** (`ab6f1ae` `3297e26`) — exception 补 import json、check_abc Win 编码、`AGENT_TIMEOUT` 真 enforce、deps 反向依赖消除、ABC 检查覆盖 10→19、console_app 设计文档重写(原错挂)、架构图模块清单校准、横切模块文档、`doc/README.md` 文档状态校准页
3. **自动推送双保险** (`7feb32f`) — `MEMORY.md` 规则 + `.git/hooks/post-commit`(由 `scripts/install-git-hooks.sh` 装)
4. **方向 3 自我验证** (`d838813` `6d163a6`) — `verifier.py`: ToolSuccess/Execution(pytest·sql·shell·lint)/Task 终态/Compliance 验证器 + 自纠正闭环 + off/auto/ask 三模式
5. **方向 2 记忆升级** (`0d78f4a` `6a90a00` `88f0c66`) — Fact 加 `mutability`/`digest`/`content_type`/`encrypted`、敏感加密、两级检索、差异化清理、反思整合
6. **方向 1 用户画像 MVP** (`8cd577b`) — `get_user_profile` 5 维度聚合 + agent always-on 注入

## 4. 未完成 backlog (按优先级)
- ~~agent 并行工具执行~~ ✅ **done** (`185dca6`+`d7d1b66`): LLM 输出 `actions:[]` 多动作 JSON → `_run_actions_parallel`(ThreadPool ≤4)一步并发彼此独立的工具; `_react_execute`(非流式) + `run_stream`(流式)双路径均支持。注: 评测台见 qwen-max 偶有"对同一工具连调 3 次"的非收敛(如 datetime×3), 属模型侧选择噪声非编排 bug, 未阻塞; 若要治可加"重复(tool,input)去重/提前收敛"提示。
- **外部工具连接 余量 (可选)** — HTTP 连接器 + MCP(stdio+HTTP) + OpenAPI 生成 + 真实 server 集成测试 **均已落地** (`business/agent_module/tools/external/`)。剩: (a) MCP **SSE 长连**(server→client 流式通知, 现 HTTP 仅请求/响应); (b) 连接**生命周期管理**(现 discover 启动连, 进程随 app 存活); (c) OpenAPI **`spec_url` 远程拉取**(现仅 inline spec dict)。详见 `doc/外部工具连接-设计方案(RFC).md`。
- **方向 4 续探 (可选)** — 建议性自主全档已落地(行为/记忆/代码文档自维护 + 定时提议通知 + 预授权自动, **默认全关**)。再往前: (a) 把 `maintenance_scan` 真正注册进 `TaskScheduler` 定时任务(现是能力就绪, 未默认注册); (b) 真正"执行性自主"(超出预授权安全算子, 如自动改配置/代码)——**风险高, 须专门设计 + 强护栏(沙箱/审批/可回滚), 不轻易做**
- **react JSON 解析器容错 (可选, 治调试类延迟)** — qwen 给长答案 (如"系统排查思路") 时 react JSON 常畸形 → `_parse_react_response` 返 None → 流式走 parse-fail 兜底 → 触发洗净重生成 (+20s, 调试因此 ~62s, 正确但慢)。深层提速: 让解析器从畸形 JSON 里也尽量抠出 `final_answer` 字段 (容错 trailing comma / 未转义换行 / 截断), 抠到就不必重生成。风险: 解析器改动面广, 须配 run_stream 单测护; 现状已正确 (评测台 100%), 仅慢, 故标可选。
- **pre-existing**: `#149 XXXX-2` 测流式 toggle 是否真生效 (一直 pending)
- **方向 1 可选增强**: `reconcile_conflicts`/`consolidate`/`prune` 接入调度定期触发 (现都只外部手动调, 无定时); 给 UP-4 加 "ask 模式"(歧义大反问澄清而非静默改写) + 默认开启策略
- **方向 3 可选深化**: 给 plan 加 "goal 分组" 结构, 让 GoalVerifier 用**真实计划分组**而非现场拆 (触及规划核心; 现用显式子目标/现场拆已够用, GoalVerifier 已留 `spec.args.goals` 接入点)
- **可选治理债**: 文档债深度逐份回灌 (doc/README 已标注哪些滞后, 未重写)；7 个 cross-cutting 模块补 base.py/测试归位 (AUDIT-4 评估为低优先, 已补集中设计文档)

## 5. 关键机制 (下个会话必须知道)
- **自动推送**: commit 后**自动** `git push origin main` 不询问 (双保险: MEMORY 规则 + git post-commit hook)。push 后仍一句话告知; 疑似含密钥/凭据/大体积运行时数据时先提示。
- **评测台 (回归护栏, 2026-06-05 起)**: `scripts/eval_agent.py` — 改完 agent **先起服务再跑它**, 不要只靠单测。它走**真实 WS `/invoke/stream` + 真 LLM**(单测用 mock, 抓不到"流式路径漂移""模型选错工具""答案截断/空"这类只在真环境暴露的回归 — 本会话大半 bug 都是这类)。用法: 起服务(端口 18877)→ `python scripts/eval_agent.py [--runs N]`; 全过 exit 0、有失败 exit 1。7 个代表任务(算数/多工具并行/本机状态/规划直接答/实时时间/调试方法论/问答直接答)各带断言(no_error/nonempty/min_len/contains/not_refused); `--runs N` 量非确定性(同任务多跑, PART=偶发 flaky vs FAIL=稳定坏)。**新增 agent 能力时往 TASKS 里加一条**。注: 中文请求务必 UTF-8(评测台已对; 手动 `Invoke-RestMethod` 要 `[Text.Encoding]::UTF8.GetBytes` 否则 GBK→服务端收到乱码→模型答错, 是测试姿势问题非产品 bug)。
- **技能系统 (3 来源, 互补)**: skill = `skills/**/*.md`(frontmatter `name/description/triggers/tools/priority` + 正文)。① **trigger 自动注入** — `SkillRegistry.match(task)` 子串命中 → body 直接拼进 react prompt(零额外调用); ② **use_skill 按需加载**(superpowers 式) — prompt 注入"技能目录"(名+描述, **已 trigger 注入的排除**免重复), agent 自己决定 `use_skill(name)` 拉完整指南; ③ **自动沉淀**(Hermes 式, 默认关) — 成功复杂任务后台提炼 `_auto_*.md`。`git clone obra/superpowers` 进 `skills/` 即用(rglob 递归加载)。`_auto_*.md` 已 gitignore。
- **流式 = ReAct 真路径 + 健壮性层 (本会话重点)**: 前端走 `run_stream`(流式 generator), **不是** `execute`(非流式) — 两者历史上**漂移**致大量"只在流式坏"的 bug。现 `run_stream` 真走 ReAct(`_effective_strategy` 含 `self.execution_strategy=react`, 不再退化成纯 chat)。收尾铁律: **最终答案非流式一次生成+切片**(不用 chat_stream, 防 SSE 中途断) → **仅"有工具结果"才重生成**(无工具直接用 react `final_answer`, 治延迟) → **铁底兜底答案绝不空白**。改流式逻辑后**务必跑评测台**(单测 mock 覆盖不到)。
- **自我验证** (`verifier.py`): **2026-06-04 起默认 on** (config `agent.enable_self_verify=true` / `verify_mode=auto` / `max_correction=1`; 每个 agent 任务终态校验+未完成自纠正; 关回 `false`/`off` 即停, 单测构造均显式关以隔离)。手动也可: `agent.enable_self_verify=true` + `verify_mode=off|auto|ask`；请求带 `extra_params.verify=[{"type":"pytest","target":"tests/"}]`。**三层模型 (Step/Goal/Task)**: ToolSuccess/Execution(pytest·sql·shell·lint)/Compliance/Task 终态之外, **GoalVerifier 子目标级**(`extra_params.verify_goals=True` opt-in, 默认关; 可带 `goals=[...]` 显式子目标, 否则 LLM 现场拆; 给"plan goal 分组"留接入点但未启用——不动规划核心)。`_post_verify` 是接入点(`collect_specs→make_registry→run_verifiers`+auto 自纠正递归)。验证器故障一律 fail-open(放行)。
- **记忆分层**: `Fact.mutability` = `canonical`(密码/路径, 永久不删不改写) / `refinable`(偏好/做法, 可提炼、老了丢原始留 digest)；`digest`=精炼层(粗筛+加密 secret 仍可检索)；`content_type` 含画像 5 维度。
- **用户画像 5 维度**: `preference/style/convention/domain/weakness`；`get_user_profile()` 聚合，agent `_inject_user_profile()` 任务前 always-on 注入(`weakness`→"需主动补位"=规避缺陷)。
- **query refinement (UP-4)**: 默认 **off** (`agent.enable_query_refine` / env `ANYTHING_AGENT_QUERY_REFINE`)。用户问得含糊时 `_refine_query()` 基于画像 + LLM 判含糊并改写问题(只补"知道偏好就能定的缺省"=语言/技术栈/格式/范围, 严格保留原意), 再走记忆/画像/历史注入与规划。**只改 task(规划输入), `original_task` 保持用户原话**(history 展示/记忆抽取用)。三重 gate(长问题>`query_refine_max_len`默认200 / 无画像 / 无 LLM) + 安全阀(改写非空且不比原问题短太多) + 全程 fail-open(用原问题)。改写记 `details["query_refinement"]={original,refined,reason}` 透明可回溯; 纠正递归跳过; 单次可 `extra_params.enable_query_refine` 覆盖。区别于 `_inject_user_profile`(画像当上下文附前面): 这里直接改写"问题本身"。
- **画像冲突消解 + 时效 (方向1阶段3)**: `Fact.superseded_by`(被哪条新 fact 取代; `None`=有效, 非破坏性标记**不删数据**保审计链)。`reconcile_conflicts(tenant_id)` 用 LLM 找对立偏好分组→组内 `created_at` 最新者胜、其余标 `superseded_by`(无 `llm_client` 返 0; 循 `consolidate` 的独立 LLM 批处理模式, **不在每轮 add_fact 跑**, 现由外部手动调)。`get_user_profile` 时效排序 `(pinned, created_at, access_count)` → 新偏好领先(用 `created_at` 不受 `mark_accessed` bump 污染), 且只取有效项。`search_facts` 三阶段都跳过 superseded(旧偏好不再作为"当前上下文"注入)。`reconcile`(消解对立) vs `consolidate`(归纳重复) 是两件事。
- **自主维护 / 行为自反思 (方向4 第一级)**: 默认 **off** (`agent.enable_self_reflection`)。`self_reflect()` 按需读审计日志(`run/audit.log.jsonl`)聚合 per-tool 成败率/错误码/成本 → `SelfReflectionInspector` LLM **元级反思** → 结构化改进提议(**dry-run 零改动**)。`apply_reflection_proposals(proposals, approved_ids)` 仅把**人审批**的 `record_lesson` 落长期记忆(`content_type=convention` 反哺画像→改进未来行为), 非审批/非 lesson 一律不动, **绝不自动改配置/代码**。建议性自主全程 human-in-loop; 区别于 `_reflect_revise`(单答案反思)——这是**跨多任务行为模式**。组件 `core/components/self_reflection.py`。
- **自主维护·记忆健康 (方向4 扩域)**: 默认 **off** (同 `enable_self_reflection`)。`propose_memory_maintenance(tenant)` 只读检视记忆健康(陈旧/可降级/对立/冗余, **确定性**不调 LLM)→ dry-run 提议; `apply_memory_maintenance(proposals, approved_ids)` 仅把**人审批**的提议映射到**现成算子**执行(`run_prune→prune_stale` / `run_degrade→degrade_stale_refinable` / `run_reconcile→reconcile_conflicts` / `run_consolidate→consolidate`)。候选谓词须与那些算子一致(改算子时同步 `aggregate_memory_signals`)。
- **自主维护·代码文档 (方向4 扩域, advisory)**: `propose_code_doc_maintenance(root)` 只读扫项目(缺 README 模块 / TODO·FIXME 计数)→ advisory 清单; **代码/文档改动绝不自动执行, 无 apply**(仅供人处理)。
- **自主维护·定时提议+通知 + 更高自主档 (方向4)**: `run_maintenance_scan(scope, auto_apply)` 聚合 行为/记忆/代码文档 三域提议 + `_notify_maintenance` 写审计(通知); `execute` 的 `extra_params.maintenance_scan=true` 钩子可被 **TaskScheduler** 周期触发(`maintenance_scope` 选域)。**更高自主档**: 配置 `agent.auto_approve_maintenance`(预授权 action_type 名单, **默认空**=零自动=纯提议); `auto_apply` 时仅对 (名单 ∩ **安全天花板** `{run_prune,run_degrade}`) 自动执行+审计; reconcile/consolidate(LLM)/code_doc(advisory) **永不**自动。人设名单=预授权、被通知、清空=撤销 → 仍全程 human-in-loop。
- **外部工具连接 (RFC Stage1+2)**: `business/agent_module/tools/external/` — `ExternalToolProvider` 抽象(HTTP/MCP 都实现)。**Stage1 HTTP**: `HttpToolProvider`+`HttpToolSpec`(声明式 url占位/query/body/auth/`response_path`)+`make_http_tool`(复用 `http_get` SSRF 防御)。**Stage2 MCP**: `McpToolProvider` + `_McpClientBase`(传输无关) → `McpStdioClient`(stdio 子进程) / `McpHttpClient`(Streamable HTTP: POST JSON-RPC, json/sse 响应, Mcp-Session-Id); 标准 MCP 协议 JSON-RPC2.0, 最小自实现无 SDK; initialize→tools/list→tools/call; 命名空间 `server.tool`。**OpenAPI**: `openapi_to_specs(openapi dict)` 每 operation → HttpToolSpec(复用 Stage1)。配置 `agent.external_tools`/`agent.mcp_servers`/`agent.openapi_tools` → 启动 `business_layer.py` 注册, **默认并入 `tool_approval_required`**(human-in-loop), fail-safe。零新依赖。凭据/server 仅显式配置(勿明文; 不自动连未知 server)。真实 server 互通有自带 echo server 集成测试守护。
- **敏感加密**: `content_type=secret` 的 content 用 Fernet 加密(`SENSITIVE_CONFIG_SECRET`)，`reveal_fact()` 显式解密；无密钥则丢明文(绝不明文落库)。
- **ABC 守护**: `scripts/check_abc_alignment.py` (19 对 base↔impl)。pre-commit 未实际安装(`.git/hooks` 当前只有 post-commit)。

## 6. 用户偏好/约定 (务必遵守 — 详见 MEMORY.md)
- **中文**沟通
- **minimal change**: 只改对应功能, 不动旁边已实现好的; **禁词"顺带/顺手"**; 修后验证旁边没倒
- **任务三段式**: 先列计划清单 → 过程有记录 → 结果有反馈
- **已完成任务不重读**, 直接用既有结论推进
- **不瞎猜瞎改**: 基于 ground truth, 亲自验证(用户反复强调过)
- 自动 push (见 §5)

## 7. 怎么跑 (环境)
```bash
# Python (Windows + git bash)
PY=/c/ProgramData/miniconda3/python.exe
# PYTHONPATH 需含 6 层 + 根:
PYTHONPATH="<根>\basic_support;<根>\data_layer;<根>\business;<根>\interface;<根>\application;<根>\run;<根>"
# 跑测试 (中文输出需 UTF-8):
PYTHONIOENCODING=utf-8 PYTHONPATH=... $PY -m pytest <module>/tests -q -p no:cacheprovider
# ABC 检查:
PYTHONPATH=... $PY scripts/check_abc_alignment.py
```
⚠️ **全量 `pytest` 会被 2 个 pre-existing collection error 中断** (orchestrator 的 `OrchestratorException` import + document_parser 缺 `pandas`)。跑指定模块, 或加 `--continue-on-collection-errors`。这俩与本 session 改动无关。

## 8. 关键文件位置
| 用途 | 路径 |
|---|---|
| 自我验证器 (方向3, 含 GoalVerifier) | `business/agent_module/core/components/verifier.py` |
| 自反思 / 自维护 (方向4) | `business/agent_module/core/components/self_reflection.py` |
| 外部工具连接 (HTTP 连接器 + MCP stdio/HTTP + OpenAPI 生成) | `business/agent_module/tools/external/` (base/http_provider/mcp_provider/openapi/__init__) + `run/factories/business_layer.py` 接线 |
| 计算机操作 (computer_use 工具, 危险/默认审批) | `business/agent_module/tools/tools_impl/computer_use.py` (pyautogui lazy, backend 可注入) |
| Agent 核心(验证接入 `_post_verify` / 画像注入 `_inject_user_profile` / 超时 enforce) | `business/agent_module/core/impl.py` |
| 长期记忆(分层/加密/两级检索/画像/整合) | `basic_support/long_term_memory_module/core/impl.py` |
| ABC 检查脚本 | `scripts/check_abc_alignment.py` |
| git hook 安装 | `scripts/install-git-hooks.sh` |
| 文档状态(哪些 doc 滞后) | `doc/README.md` |
| 产品定位 | `PRODUCT.md` · 上线清单 `DEPLOYMENT.md` |

## 9. 下个会话建议第一步
1. 读本文件 + `MEMORY.md`(自动注入) — 5 分钟接上上下文
2. 确认 `git status` 干净、`git log origin/main..HEAD` 为空
3. 问用户: 新需求 (方向1/2/3 收尾; 方向4 建议性自主全档; 外部工具 HTTP+MCP(stdio/HTTP)+OpenAPI 均落地, 余量见 §4)
4. 任何 commit 后会由 git hook 自动 push (无需手动)

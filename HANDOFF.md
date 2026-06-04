# 会话交接文档 (Session Handoff)

> 给下一个会话的接力棒。无需读全程 transcript，读这份 + `MEMORY.md`(auto-memory 自动注入) 即可接上。
> 最后更新: 2026-06-03 · 最新 commit: `b1b2d62` · 分支: `main`(⚠️ 本地或领先 origin: 网络不稳定时 push 可能失败, 需确认 `git push origin main`)

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
0. **本次会话 (方向1→2→3→4 + 文档审计 + 外部工具 Stage1+2, 全在 origin/main)** —
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
- **外部工具连接 余量 (可选)** — HTTP 连接器 + MCP(stdio+HTTP) + OpenAPI 生成 + 真实 server 集成测试 **均已落地** (`business/agent_module/tools/external/`)。剩: (a) MCP **SSE 长连**(server→client 流式通知, 现 HTTP 仅请求/响应); (b) 连接**生命周期管理**(现 discover 启动连, 进程随 app 存活); (c) OpenAPI **`spec_url` 远程拉取**(现仅 inline spec dict)。详见 `doc/外部工具连接-设计方案(RFC).md`。
- **方向 4 续探 (可选)** — 建议性自主全档已落地(行为/记忆/代码文档自维护 + 定时提议通知 + 预授权自动, **默认全关**)。再往前: (a) 把 `maintenance_scan` 真正注册进 `TaskScheduler` 定时任务(现是能力就绪, 未默认注册); (b) 真正"执行性自主"(超出预授权安全算子, 如自动改配置/代码)——**风险高, 须专门设计 + 强护栏(沙箱/审批/可回滚), 不轻易做**
- **pre-existing**: `#149 XXXX-2` 测流式 toggle 是否真生效 (一直 pending)
- **方向 1 可选增强**: `reconcile_conflicts`/`consolidate`/`prune` 接入调度定期触发 (现都只外部手动调, 无定时); 给 UP-4 加 "ask 模式"(歧义大反问澄清而非静默改写) + 默认开启策略
- **方向 3 可选深化**: 给 plan 加 "goal 分组" 结构, 让 GoalVerifier 用**真实计划分组**而非现场拆 (触及规划核心; 现用显式子目标/现场拆已够用, GoalVerifier 已留 `spec.args.goals` 接入点)
- **可选治理债**: 文档债深度逐份回灌 (doc/README 已标注哪些滞后, 未重写)；7 个 cross-cutting 模块补 base.py/测试归位 (AUDIT-4 评估为低优先, 已补集中设计文档)

## 5. 关键机制 (下个会话必须知道)
- **自动推送**: commit 后**自动** `git push origin main` 不询问 (双保险: MEMORY 规则 + git post-commit hook)。push 后仍一句话告知; 疑似含密钥/凭据/大体积运行时数据时先提示。
- **自我验证** (`verifier.py`): 默认 **off**。开: `agent.enable_self_verify=true` + `verify_mode=off|auto|ask`；请求带 `extra_params.verify=[{"type":"pytest","target":"tests/"}]`。**三层模型 (Step/Goal/Task)**: ToolSuccess/Execution(pytest·sql·shell·lint)/Compliance/Task 终态之外, **GoalVerifier 子目标级**(`extra_params.verify_goals=True` opt-in, 默认关; 可带 `goals=[...]` 显式子目标, 否则 LLM 现场拆; 给"plan goal 分组"留接入点但未启用——不动规划核心)。`_post_verify` 是接入点(`collect_specs→make_registry→run_verifiers`+auto 自纠正递归)。验证器故障一律 fail-open(放行)。
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

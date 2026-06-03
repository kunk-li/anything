# 会话交接文档 (Session Handoff)

> 给下一个会话的接力棒。无需读全程 transcript，读这份 + `MEMORY.md`(auto-memory 自动注入) 即可接上。
> 最后更新: 2026-06-03 · 最新 commit: `c15380d` · 分支: `main`(已同步 origin)

---

## 1. 项目一句话
**Anything** — 自部署的 RAG + Agent 平台 (Python 四层架构 `basic_support/data_layer/business/interface/application` + `run/` 启动层 + vanilla-JS 前端)。
当前战略: 从"团队知识库工具"演进为**懂使用者的个人智能助手** (终极目标: 能自动思考/自更新维护的智能 agent)。

## 2. 用户的产品愿景 + 4 方向路线 (用户定的优先级: 3→2→1→4)
| 方向 | 内容 | 状态 |
|---|---|---|
| **方向 3** 自我验证闭环 | 执行类任务"做到位"验证 + 自纠正 | ✅ 阶段1-2 done；GoalVerifier 缓做 |
| **方向 2** 记忆升级 | 被动 fact 库 → 主动认知系统 | ✅ 阶段1-3 done (用户 5 点需求全兑现) |
| **方向 1** 用户模型 | "更懂你" — 画像 + 主动应用 | ✅ MVP(UP-1/2/3)+UP-4 done；阶段3 待做 |
| **方向 4** 自主维护 | 自动思考/更新 (终极目标) | ⬜ 未开始 |

## 3. 已完成 (累计, 全在 origin/main)
0. **方向 1 UP-4 query refinement** (`c15380d`, 本次会话) — 含糊问题基于画像 LLM 判含糊+改写后再规划; 三重 gate + 安全阀 + 全程 fail-open; 默认关; 记 `details["query_refinement"]` 透明; test 9 例 + agent 全套 258 passed + ABC 19 全绿
1. **全项目审计** — 5 个并行 agent 扫描代码 vs 设计规范，亲自复核 3 条 CRITICAL (修正 2 处误报)
2. **AUDIT 1-4** (`ab6f1ae` `3297e26`) — exception 补 import json、check_abc Win 编码、`AGENT_TIMEOUT` 真 enforce、deps 反向依赖消除、ABC 检查覆盖 10→19、console_app 设计文档重写(原错挂)、架构图模块清单校准、横切模块文档、`doc/README.md` 文档状态校准页
3. **自动推送双保险** (`7feb32f`) — `MEMORY.md` 规则 + `.git/hooks/post-commit`(由 `scripts/install-git-hooks.sh` 装)
4. **方向 3 自我验证** (`d838813` `6d163a6`) — `verifier.py`: ToolSuccess/Execution(pytest·sql·shell·lint)/Task 终态/Compliance 验证器 + 自纠正闭环 + off/auto/ask 三模式
5. **方向 2 记忆升级** (`0d78f4a` `6a90a00` `88f0c66`) — Fact 加 `mutability`/`digest`/`content_type`/`encrypted`、敏感加密、两级检索、差异化清理、反思整合
6. **方向 1 用户画像 MVP** (`8cd577b`) — `get_user_profile` 5 维度聚合 + agent always-on 注入

## 4. 未完成 backlog (按优先级)
- **方向 1 阶段 3** — 画像冲突消解 + 时效 (偏好变了更新); 也可考虑给 UP-4 加 "ask 模式"(歧义大时反问澄清, 而非静默改写) 与默认开启策略
- **方向 3 GoalVerifier** — 子目标级验证, 需先给 plan 加 "goal 分组" 结构 (改动触及 Agent 规划核心)
- **方向 4 自主维护** — 终极目标; **务必分级**: 先"建议性自主"(agent 提议、人审批), 绝不一上来"执行性自主"; 全程 human-in-loop (方向1 的 `ask` 模式 + 方向3 的 ComplianceVerifier 是雏形)
- **pre-existing**: `#149 XXXX-2` 测流式 toggle 是否真生效 (一直 pending)
- **可选治理债**: 文档债深度逐份回灌 (doc/README 已标注哪些滞后, 未重写)；7 个 cross-cutting 模块补 base.py/测试归位 (AUDIT-4 评估为低优先, 已补集中设计文档)

## 5. 关键机制 (下个会话必须知道)
- **自动推送**: commit 后**自动** `git push origin main` 不询问 (双保险: MEMORY 规则 + git post-commit hook)。push 后仍一句话告知; 疑似含密钥/凭据/大体积运行时数据时先提示。
- **自我验证** (`verifier.py`): 默认 **off**。开: `agent.enable_self_verify=true` + `verify_mode=off|auto|ask`；请求带 `extra_params.verify=[{"type":"pytest","target":"tests/"}]`。验证器故障一律 fail-open(放行)。
- **记忆分层**: `Fact.mutability` = `canonical`(密码/路径, 永久不删不改写) / `refinable`(偏好/做法, 可提炼、老了丢原始留 digest)；`digest`=精炼层(粗筛+加密 secret 仍可检索)；`content_type` 含画像 5 维度。
- **用户画像 5 维度**: `preference/style/convention/domain/weakness`；`get_user_profile()` 聚合，agent `_inject_user_profile()` 任务前 always-on 注入(`weakness`→"需主动补位"=规避缺陷)。
- **query refinement (UP-4)**: 默认 **off** (`agent.enable_query_refine` / env `ANYTHING_AGENT_QUERY_REFINE`)。用户问得含糊时 `_refine_query()` 基于画像 + LLM 判含糊并改写问题(只补"知道偏好就能定的缺省"=语言/技术栈/格式/范围, 严格保留原意), 再走记忆/画像/历史注入与规划。**只改 task(规划输入), `original_task` 保持用户原话**(history 展示/记忆抽取用)。三重 gate(长问题>`query_refine_max_len`默认200 / 无画像 / 无 LLM) + 安全阀(改写非空且不比原问题短太多) + 全程 fail-open(用原问题)。改写记 `details["query_refinement"]={original,refined,reason}` 透明可回溯; 纠正递归跳过; 单次可 `extra_params.enable_query_refine` 覆盖。区别于 `_inject_user_profile`(画像当上下文附前面): 这里直接改写"问题本身"。
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
| 自我验证器 | `business/agent_module/core/components/verifier.py` |
| Agent 核心(验证接入 `_post_verify` / 画像注入 `_inject_user_profile` / 超时 enforce) | `business/agent_module/core/impl.py` |
| 长期记忆(分层/加密/两级检索/画像/整合) | `basic_support/long_term_memory_module/core/impl.py` |
| ABC 检查脚本 | `scripts/check_abc_alignment.py` |
| git hook 安装 | `scripts/install-git-hooks.sh` |
| 文档状态(哪些 doc 滞后) | `doc/README.md` |
| 产品定位 | `PRODUCT.md` · 上线清单 `DEPLOYMENT.md` |

## 9. 下个会话建议第一步
1. 读本文件 + `MEMORY.md`(自动注入) — 5 分钟接上上下文
2. 确认 `git status` 干净、`git log origin/main..HEAD` 为空
3. 问用户: 继续 **方向1阶段3(画像冲突/时效)**、**方向3 GoalVerifier**、开 **方向4(自主维护)**, 还是新需求
4. 任何 commit 后会由 git hook 自动 push (无需手动)

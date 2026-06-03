# 基础支撑层-横切模块（cross-cutting）设计说明书

| 项 | 值 |
| :--- | :--- |
| 文档版本 | v1.0 |
| 最后更新 | 2026-06-03 |
| 状态 | 新建（AUDIT-4：补齐此前缺失的横切模块设计文档） |

## 1. 概述

这批模块是在核心四层之外、按需增补的**横切关注点（cross-cutting concerns）**。它们大多由 Task OO（#75）从 `common_utils_module` 中提升为独立模块，服务于 Agent / RAG / ApiService 等多个上层，但不属于某一条业务主链路。

> 为什么单列一份文档：审计（AUDIT）发现这 7+ 个模块此前**没有任何设计文档**，且结构与核心模块的 `core/base.py + core/impl.py` 约定不完全一致。本文档补齐文档缺口并说明结构现状。

## 2. 模块清单与职责

| 模块 | 关联任务 | 核心类 | 进程单例入口 | DI 注入字段 |
| :--- | :--- | :--- | :--- | :--- |
| `hooks_module` | Z (#60) | `HookRegistry` / `BlockedError` | `get_hook_registry()` | `deps.hook_registry` |
| `skills_module` | AA (#61) | `SkillRegistry` / `Skill` | `get_skill_registry()` | `deps.skill_registry` |
| `quota_module` | BB (#62) | `QuotaGuard` | `get_quota_guard()` | `deps.quota_guard` |
| `audit_module` | CC (#63) | `AuditLogger` | `get_audit_logger()` | `deps.audit_logger` |
| `project_memory_module` | U (#55) | `ProjectMemory` | `get_project_memory()` | `deps.project_memory` |
| `observability_module` | UU (#81) | `UsageTracker` | `get_usage_tracker()` | `deps.usage_tracker` |
| `state_backend_module` | TT (#80) | `StateBackend`(ABC) + `InMemoryBackend`/`SqliteBackend`/`RedisBackend` | — | （被 tracker 持有） |
| `long_term_memory_module` | DDD (#90) | `BaseLongTermMemory`(ABC) + `LongTermMemoryImpl` | — | `deps.long_term_memory` |

### 2.1 各模块职责
- **hooks**：可插拔钩子系统。`HookRegistry.fire(event, ...)` 在 `pre_tool_call` / `post_tool_call` 等点触发；钩子可改写输入/输出或 `raise BlockedError` 拦截（危险工具守护）。
- **skills**：`skills/*.md` 技能卡自动注入 LLM prompt（`inject_skills_into_prompt`）。
- **quota**：per-tenant USD 配额 + rate limit；`QuotaGuard` 在调用前校验、调用后累加；后端可走 `StateBackend` 跨进程（AAA #87）。
- **audit**：append-only JSONL 审计日志，记录每次 LLM / 工具调用，可经 hooks 安装（`install_audit_hooks`）。
- **project_memory**：项目级记忆（`AGENTS.md`），Agent / RAG init 时注入为前置上下文。
- **observability**：`UsageTracker` 统计 token / cost，metrics 端点数据源；可走 `StateBackend`（XX #84）。
- **state_backend**：跨进程状态后端抽象。`InMemoryBackend`(默认) / `SqliteBackend`(跨进程持久化) / `RedisBackend`(高吞吐，需 redis-py)。让 UsageTracker / QuotaGuard / ModelHealthTracker 可在多实例间共享状态。
- **long_term_memory**：长期记忆。LLM 抽取 fact + embedding 语义查重 + CRUD + pin / search（DDD/EEE/FFF #90-92）。
  - *(2026-06-03 校准补)* **方向2 记忆升级**：`Fact` 加 `mutability`(canonical 固定权威/refinable 可提炼) / `digest`(精炼层) / `content_type` / `encrypted`；敏感 secret 用 Fernet 加密(`reveal_fact()` 显式解密，无密钥丢明文绝不明文落库)；两级检索(digest 粗筛→content 细比)；差异化清理(`prune_stale` 删 / `degrade_stale_refinable` 丢原始留精炼) + 反思整合(`consolidate`)。
  - *(2026-06-03 校准补)* **方向1 用户画像 + 时效**：`get_user_profile()` 5 维度(preference/style/convention/domain/weakness)聚合，按 `created_at` 时效排序且只取有效项；`reconcile_conflicts()` 用 LLM 消解对立偏好(组内最新者胜、其余标 `superseded_by`)；`Fact.superseded_by` 非空者不进画像/检索。供 agent always-on 画像注入与 query refinement。

## 3. 共同模式

### 3.1 进程单例 + DI 注入双通道
大多数横切模块提供两种访问方式（见 `deps_module/deps.py` BasicDeps docstring）：
```python
# 推荐 — DI 注入（便于测试隔离）
agent = SimpleAgent(deps=deps);  reg = deps.hook_registry
# back-compat — 直接拿进程单例
from hooks_module import get_hook_registry;  reg = get_hook_registry()
```
`build_basic_deps()` 在启动时通过 `get_X()` 把这些单例引用塞进 `BasicDeps`，上层优先用注入、未注入时 fallback 单例。

> 注（AUDIT-2b）：`health_tracker` 属 data_layer（`llm_adapter_module`），**不**由 basic_support 的 `build_basic_deps` 反向获取，以保持分层纯净；其消费方直接 `get_health_tracker()`。

## 4. 结构现状与治理说明

| 模块 | 结构 | 是否标准（`core/base.py`+`core/impl.py`） |
| :--- | :--- | :--- |
| `long_term_memory_module` | `core/base.py` + `core/impl.py` + `model/` + `tests/` | ✅ 标准 |
| `state_backend_module` | `base.py` + `impl.py` + `tests/`（平铺，未进 `core/`） | 半标准（有 ABC，未分 core/） |
| `hooks` / `skills` / `quota` / `audit` / `project_memory` / `observability` | 仅 `impl.py`（+ 部分 `tests/`） | 扁平（无独立 ABC） |

### 4.1 为何不强行标准化（minimal-change 取舍）
- 这些模块多为**单实现的注册表 / 工具类**（HookRegistry/SkillRegistry/QuotaGuard/AuditLogger），不存在多实现切换需求，补 ABC 的收益低。
- 它们已稳定工作且有测试覆盖；强行重构目录（移到 `core/` + 加 `base.py` + 改 import）有破坏风险，违反"只改对应功能、不动旁边"的原则。
- 部分能力（hooks/skills 前端面板）按 PRODUCT.md §6 已退化为"保后端、隐前端"，不宜再投入大改。

### 4.2 测试归位（已知欠账，低优先）
`hooks` / `skills` / `project_memory` 的测试目前寄居在 `common_utils_module/tests/`（OO #75 提升时未一并搬迁）；`quota` / `audit` / `state_backend` / `long_term_memory` 自身已有 `tests/`。**所有 7 个模块均有测试覆盖且通过**，仅文件位置不够整洁——建议作为独立清理任务按需归位，不在功能迭代中夹带。

### 4.3 ABC 检查覆盖
`scripts/check_abc_alignment.py` 已纳入 `state_backend`(3 实现) 与 `long_term_memory`（AUDIT-2c）；其余扁平模块无 ABC，不在检查范围。

## 5. 变更记录
- **v1.0 (2026-06-03, AUDIT-4)**：新建。补齐此前完全缺失的横切模块设计文档；说明结构现状与 minimal-change 治理取舍。

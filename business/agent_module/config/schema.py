# -*- coding: utf-8 -*-
"""
Agent 配置中枢 (执行计划②, 原建议第6条 + "注意2": 给统一配置界面当数据源)

把散落在 SimpleAgent.__init__ 的 ~26 个 agent.* 开关集中声明成一份**可发现、可校验、可导出**
的 schema:
    - AGENT_CONFIG_SCHEMA  : 全部 flag 的元数据 (key/attr/type/default/env/group/choices/desc)
    - dump_agent_config()  : 元数据 + 当前生效值 → 喂前端统一配置界面 (⑦)
    - validate_agent_config(): 启动期校验 config.yaml 的 agent.* 未知 key → WARN (防拼写错/废弃漂移)

注: __init__ 仍各自 get_effective_value 读 (不动热路径); 本 schema 是 catalog/校验/导出 的 SSOT,
由 test_config_schema 守护"schema 字段 ↔ agent 真实属性"不漂移。type ∈ {bool,int,str,list}。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, List, Optional


@dataclass(frozen=True)
class ConfigField:
    key: str                       # 全局配置键, 如 "agent.execution_strategy"
    attr: Optional[str]            # SimpleAgent 实例属性名 (None=非 agent 属性, 如调度类只在工厂读)
    type: str                      # bool | int | str | list (给 UI 渲染选控件)
    default: Any                   # 代码默认 (get_effective_value 的 fallback)
    env: Optional[str]             # 环境变量覆盖名 (None=无 env 覆盖)
    group: str                     # UI 分组
    desc: str                      # 一句话说明 (给 UI/文档)
    choices: Optional[List[str]] = None   # 枚举可选值 (给 UI 下拉)


# ── 全部 agent.* 配置项 (SSOT) ──────────────────────────────────────────────
AGENT_CONFIG_SCHEMA: List[ConfigField] = [
    # 执行
    ConfigField("agent.execution_strategy", "execution_strategy", "str", "single_shot",
                "ANYTHING_AGENT_EXECUTION_STRATEGY", "执行",
                "执行策略: single_shot 一次性规划 / react 多轮 observe→think→act",
                choices=["single_shot", "react"]),
    ConfigField("agent.max_react_iterations", "max_react_iterations", "int", 15,
                "ANYTHING_AGENT_MAX_REACT_ITER", "执行", "ReAct 模式最大轮数"),
    ConfigField("agent.use_llm_planner", "use_llm_planner", "bool", True,
                "ANYTHING_AGENT_USE_LLM_PLANNER", "执行", "用 LLM 规划工具序列 (失败回退规则式)"),
    ConfigField("agent.max_planner_steps", "max_planner_steps", "int", 3,
                "ANYTHING_AGENT_MAX_PLANNER_STEPS", "执行", "single_shot 规划最大步数"),
    ConfigField("agent.default_execution_mode", "default_execution_mode", "str", "agent",
                None, "执行", "默认执行模式"),
    ConfigField("agent.timeout", "timeout", "int", 30,
                "ANYTHING_AGENT_TIMEOUT", "执行", "单任务墙钟超时 (秒)"),
    ConfigField("agent.max_retries", "max_retries", "int", 3,
                "ANYTHING_AGENT_MAX_RETRIES", "执行", "工具调用最大重试次数"),
    ConfigField("agent.session_prefix", "session_prefix", "str", "agent_session",
                None, "执行", "默认会话 ID 前缀"),
    # 记忆与画像
    ConfigField("agent.memory_enabled", "memory_enabled", "bool", True,
                "ANYTHING_AGENT_MEMORY_ENABLED", "记忆与画像", "启用长期记忆注入 (需接 long_term_memory)"),
    ConfigField("agent.memory_top_k", "memory_top_k", "int", 5,
                "ANYTHING_AGENT_MEMORY_TOP_K", "记忆与画像", "每次注入的相关 fact 条数上限"),
    ConfigField("agent.enable_query_refine", "enable_query_refine", "bool", False,
                "ANYTHING_AGENT_QUERY_REFINE", "记忆与画像", "UP-4: 含糊问题基于画像改写/澄清 (默认关)"),
    ConfigField("agent.query_refine_max_len", "query_refine_max_len", "int", 200,
                "ANYTHING_AGENT_QUERY_REFINE_MAX_LEN", "记忆与画像", "超此长度的问题视为已具体, 不改写"),
    ConfigField("agent.query_refine_mode", "query_refine_mode", "str", "auto",
                "ANYTHING_AGENT_QUERY_REFINE_MODE", "记忆与画像",
                "auto 静默改写 / ask 歧义大时反问澄清", choices=["auto", "ask"]),
    # 自我验证 (方向3)
    ConfigField("agent.enable_self_verify", "enable_self_verify", "bool", False,
                "ANYTHING_AGENT_SELF_VERIFY", "自我验证", "任务后做终态确认 + 未完成自纠正"),
    ConfigField("agent.verify_mode", "verify_mode", "str", "off",
                None, "自我验证", "off 关 / auto 自动纠正 / ask 反馈缺口", choices=["off", "auto", "ask"]),
    ConfigField("agent.max_correction", "max_correction", "int", 2,
                "ANYTHING_AGENT_MAX_CORRECTION", "自我验证", "自纠正重跑预算次数"),
    # 自主维护 (方向4)
    ConfigField("agent.enable_self_reflection", "enable_self_reflection", "bool", False,
                "ANYTHING_AGENT_SELF_REFLECTION", "自主维护", "行为自反思/记忆健康/代码文档 建议性自维护 (默认关)"),
    ConfigField("agent.auto_approve_maintenance", "auto_approve_maintenance", "list", [],
                "ANYTHING_AGENT_AUTO_APPROVE_MAINTENANCE", "自主维护",
                "预授权可自动执行的维护算子名单 (默认空=纯提议; 安全天花板仅 run_prune/run_degrade)"),
    ConfigField("agent.maintenance_schedule", None, "str", "",
                None, "自主维护", "自维护扫描定时 (every Xs/m/h 或 @daily HH:MM; 需 enable_self_reflection)"),
    ConfigField("agent.maintenance_scope", None, "list", ["behavior", "memory", "code_doc"],
                None, "自主维护", "定时扫描的域: behavior/memory/code_doc"),
    ConfigField("agent.maintenance_auto_apply", None, "bool", None,
                None, "自主维护", "定时扫描是否自动 apply (null=按 auto_approve_maintenance 名单)"),
    ConfigField("agent.enable_self_update", "enable_self_update", "bool", False,
                "ANYTHING_AGENT_SELF_UPDATE", "自主维护",
                "影子模式自更新: 改动提议先在隔离 worktree 跑全量测试当安全闸 (绝不自动应用; 默认关)"),
    # 用户分析 (analyze_user, 方向1 深化)
    ConfigField("agent.enable_user_analysis", "enable_user_analysis", "bool", False,
                "ANYTHING_AGENT_USER_ANALYSIS", "用户分析",
                "主动分析使用者交互历史 → 工作流/习惯洞察 + 画像增强提议 (审批反哺; 默认关)"),
    ConfigField("agent.user_analysis_every_n", "user_analysis_every_n", "int", 0,
                "ANYTHING_AGENT_USER_ANALYSIS_EVERY_N", "用户分析",
                "每 N 个任务后台自动分析一次 (0=关; 需 enable_user_analysis)"),
    # 技能
    ConfigField("agent.enable_skill_distill", "enable_skill_distill", "bool", False,
                "ANYTHING_AGENT_SKILL_DISTILL", "技能", "成功复杂任务后台提炼成可复用 skill (默认关)"),
    ConfigField("agent.skill_distill_min_tools", "skill_distill_min_tools", "int", 2,
                "ANYTHING_AGENT_SKILL_DISTILL_MIN_TOOLS", "技能", "触发沉淀的最少工具调用数"),
    # 安全
    ConfigField("agent.tool_approval_required", "tool_approval_required", "list",
                # 名字须与 business_layer 注册名一致 (启动会校验 WARN);
                # 原 py_sandbox/http_request/file_write 是错名, 已对齐为 python_sandbox/http_get (file_write 无此工具删除)。
                ["python_sandbox", "http_get", "email_send", "computer_use"],
                "ANYTHING_AGENT_APPROVAL", "安全", "危险工具白名单: 被选中须 extra_params.approve_tools 显式放行"),
    # 成本 (模型分级路由)
    ConfigField("agent.model_routing_enabled", "model_routing_enabled", "bool", False,
                "ANYTHING_AGENT_MODEL_ROUTING", "成本",
                "按任务复杂度分级路由模型 (简单→便宜模型降本; 拿不准→强模型)。默认关"),
    ConfigField("agent.model_simple", "model_simple", "str", "",
                None, "成本", "简单任务用的模型名 (如 qwen-plus); 空=不路由该档"),
    ConfigField("agent.model_complex", "model_complex", "str", "",
                None, "成本", "复杂任务用的模型名 (如 qwen-max); 空=不路由该档"),
    ConfigField("agent.max_task_tokens", "max_task_tokens", "int", 0,
                "ANYTHING_AGENT_MAX_TASK_TOKENS", "成本", "单任务 LLM max_tokens 上限 (0=不限)"),
    # 性能
    ConfigField("agent.cacheable_tools", "cacheable_tools", "list",
                ["rag_search", "calculator", "currency_convert", "weather", "wikipedia",
                 "datetime", "text_stats", "regex_extract", "json_query", "code_lint"],
                "ANYTHING_AGENT_CACHEABLE_TOOLS", "性能", "结果可缓存的只读工具 (同输入命中缓存)"),
    ConfigField("agent.tool_cache_max_size", "tool_cache_max_size", "int", 256,
                "ANYTHING_AGENT_TOOL_CACHE_SIZE", "性能", "工具结果 LRU 缓存容量"),
]

# 已声明的 agent.* 短键 (校验用)
KNOWN_AGENT_KEYS = frozenset(f.key.split("agent.", 1)[1] for f in AGENT_CONFIG_SCHEMA)


def _jsonify(v: Any) -> Any:
    if isinstance(v, (set, frozenset)):
        return sorted(str(x) for x in v)
    return v


def dump_agent_config(config: Any, agent: Any = None) -> List[dict]:
    """导出全部 flag 的元数据 + 当前生效值 (给前端统一配置界面)。
    优先从 live agent 实例属性读当前值 (最准); 无 agent 或非属性项 → 从 config 读 (fallback 默认)。"""
    out: List[dict] = []
    for f in AGENT_CONFIG_SCHEMA:
        if agent is not None and f.attr and hasattr(agent, f.attr):
            current = _jsonify(getattr(agent, f.attr))
        else:
            try:
                current = config.get_config(f.key, f.default)
            except Exception:
                current = f.default
        out.append({
            "key": f.key, "group": f.group, "type": f.type,
            "default": f.default, "env": f.env, "choices": f.choices,
            "description": f.desc, "current": current,
        })
    return out


# ── 能力档位 preset (执行计划⑦+增强: 一键开一组合理组合) ──────────────────
# 只列与默认不同的项; 未列项保持当前。model_routing 都不在 preset (需先配 model_simple/complex)。
AGENT_CONFIG_PRESETS: Dict[str, Dict[str, Any]] = {
    "conservative": {   # 保守: 仅自我验证, 其余自主能力全关
        "agent.enable_self_verify": True, "agent.enable_query_refine": False,
        "agent.enable_skill_distill": False, "agent.enable_self_reflection": False,
    },
    "balanced": {       # 均衡: + 含糊问题改写 + 技能沉淀
        "agent.enable_self_verify": True, "agent.enable_query_refine": True,
        "agent.query_refine_mode": "auto", "agent.enable_skill_distill": True,
        "agent.enable_self_reflection": False,
    },
    "aggressive": {     # 进取: + 反问澄清 + 建议性自维护 (提议, 仍人审批)
        "agent.enable_self_verify": True, "agent.enable_query_refine": True,
        "agent.query_refine_mode": "ask", "agent.enable_skill_distill": True,
        "agent.enable_self_reflection": True,
    },
}


def _coerce(field: ConfigField, val: Any, current: Any) -> Any:
    """把传入值按 field.type 强转 (给运行期 setattr); str 校验 choices; list 跟随 current 是否 set。"""
    if field.type == "bool":
        return val if isinstance(val, bool) else str(val).strip().lower() in ("1", "true", "yes", "on")
    if field.type == "int":
        return int(val)
    if field.type == "str":
        s = str(val)
        if field.choices and s not in field.choices:
            raise ValueError(f"{s!r} 不在允许值 {field.choices}")
        return s
    if field.type == "list":
        items = val if isinstance(val, list) else [x.strip() for x in str(val).split(",") if x.strip()]
        items = [str(x) for x in items]
        return set(items) if isinstance(current, (set, frozenset)) else items
    return val


def apply_agent_config(agent: Any, updates: Dict[str, Any]) -> Dict[str, Any]:
    """运行期把 updates 写到 agent 实例属性 (live, 无需重启)。仅 schema 声明且有 attr 的项可改;
    未知/无 attr(如 maintenance_*, 工厂期读) 跳过。强类型校验 + per-key fail-safe。
    返回 {applied:[], skipped:[], errors:[]}。"""
    by_key = {f.key: f for f in AGENT_CONFIG_SCHEMA}
    applied: List[str] = []
    skipped: List[str] = []
    errors: List[Dict[str, str]] = []
    for key, val in (updates or {}).items():
        fld = by_key.get(key) or by_key.get(f"agent.{key}")
        if fld is None or not fld.attr or not hasattr(agent, fld.attr):
            skipped.append(key)
            continue
        try:
            setattr(agent, fld.attr, _coerce(fld, val, getattr(agent, fld.attr, None)))
            applied.append(fld.key)
        except Exception as e:
            errors.append({"key": key, "error": str(e)})
    return {"applied": applied, "skipped": skipped, "errors": errors}


def apply_preset(agent: Any, name: str) -> Dict[str, Any]:
    """套用能力档位 preset 到 agent (conservative/balanced/aggressive)。未知档名 → error。"""
    preset = AGENT_CONFIG_PRESETS.get(str(name or "").strip().lower())
    if preset is None:
        return {"applied": [], "skipped": [], "errors": [{"key": "preset", "error": f"未知档位: {name}"}]}
    result = apply_agent_config(agent, preset)
    result["preset"] = name
    return result


def validate_agent_config(config: Any, logger: Any = None) -> List[str]:
    """启动期校验: config.yaml 的 agent.* 顶层 key 若不在 schema 中 → 返回并 WARN (防拼写错/废弃漂移)。
    fail-safe: 读不到配置返 []。"""
    try:
        section = config.get_config("agent", {}) or {}
    except Exception:
        return []
    if not isinstance(section, dict):
        return []
    unknown = sorted(k for k in section.keys() if k not in KNOWN_AGENT_KEYS)
    if unknown and logger is not None:
        logger.warning(
            f"[config] agent.* 未知配置项 (schema 未声明, 可能拼写错/已废弃): {unknown}"
        )
    return unknown

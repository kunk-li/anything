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
    # 技能
    ConfigField("agent.enable_skill_distill", "enable_skill_distill", "bool", False,
                "ANYTHING_AGENT_SKILL_DISTILL", "技能", "成功复杂任务后台提炼成可复用 skill (默认关)"),
    ConfigField("agent.skill_distill_min_tools", "skill_distill_min_tools", "int", 2,
                "ANYTHING_AGENT_SKILL_DISTILL_MIN_TOOLS", "技能", "触发沉淀的最少工具调用数"),
    # 安全
    ConfigField("agent.tool_approval_required", "tool_approval_required", "list",
                ["py_sandbox", "http_request", "file_write", "email_send", "shell_exec", "computer_use"],
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

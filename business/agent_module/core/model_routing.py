# -*- coding: utf-8 -*-
"""
模型分级路由 (执行计划③, 原建议第7条: 简单任务用便宜模型降本)

启发式把任务分 simple/complex, 选对应模型。**保守偏置** (用户强调"不误判简单任务用错模型"):
只有"明确简单"(短 + 无多步/分析/工具关键词) 才判 simple→便宜模型; 拿不准一律 complex→强模型
(宁可贵也别用弱模型跑砸复杂任务)。纯函数, 确定性, 易测; 由 SimpleAgent 在 execute/run_stream
入口调用 → set_model_routing 写 ContextVar → LLMService.generate 读取路由。
"""
from __future__ import annotations

from typing import Optional

# 出现这些信号即视为 complex (多步 / 分析 / 编排 / 长输出意图)
_COMPLEX_KEYWORDS = (
    "然后", "接着", "依次", "分别", "逐个", "遍历", "批量", "多个", "同时", "并且",
    "对比", "比较", "分析", "规划", "设计", "方案", "架构", "调试", "排查", "重构",
    "实现", "开发", "步骤", "step", "plan", "首先", "其次", "最后",
    "为什么", "如何优化", "怎么实现", "写一个", "写个", "生成一份", "整理出",
)


def classify_task_complexity(task: Optional[str], simple_max_len: int = 60) -> str:
    """任务复杂度: 'simple' (短且无复杂信号) / 'complex' (其余, 含拿不准)。

    保守: 仅"明确简单"才 simple; 长问题 / 含多步·分析·编排关键词 → complex。
    """
    t = (task or "").strip()
    if not t:
        return "simple"                      # 空任务无所谓, 给便宜的
    if len(t) > simple_max_len:
        return "complex"                     # 长 → 多半复杂, 用强模型
    low = t.lower()
    if any(k.lower() in low for k in _COMPLEX_KEYWORDS):
        return "complex"
    return "simple"                          # 短 + 无复杂信号 → 明确简单


def pick_model(complexity: str, simple_model: Optional[str], complex_model: Optional[str]) -> Optional[str]:
    """按复杂度选模型名。对应档为空则返回 None (= 不路由, 用 default_chat_model)。"""
    if complexity == "simple":
        return simple_model or None
    return complex_model or None


def begin_routing(agent, task):
    """按 agent 配置 + 任务复杂度设当前请求路由 (model + token 预算) → 写 ContextVar。
    返回 reset token (或 None 未路由)。默认关 / 两档都空且无预算 / 异常 → 不路由。fail-safe。
    返回的 token 须配对 end_routing。"""
    if not getattr(agent, "model_routing_enabled", False):
        return None
    try:
        from observability_module import set_model_routing
        chosen = pick_model(classify_task_complexity(task or ""),
                            getattr(agent, "model_simple", ""), getattr(agent, "model_complex", ""))
        budget = getattr(agent, "max_task_tokens", 0) or None
        if not chosen and not budget:
            return None
        return set_model_routing(chosen, budget)
    except Exception:
        return None


def end_routing(token) -> None:
    """重置 begin_routing 设的 ContextVar (与之配对)。token=None 时 no-op。fail-safe。"""
    if token is None:
        return
    try:
        from observability_module import reset_model_routing
        reset_model_routing(token)
    except Exception:
        pass

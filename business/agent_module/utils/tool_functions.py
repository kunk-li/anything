# -*- coding: utf-8 -*-
"""
Agent 模块专属工具函数
提供任务解析与结果汇总的辅助函数
"""

from typing import Dict, Any, List


def parse_task_by_rules(task: str) -> Dict[str, Any]:
    """
    基于规则的任务解析（简化版，可扩展为 LLM 解析）
    :param task: 用户任务描述
    :return: 任务计划字典
    """
    import datetime
    task_lower = task.lower()
    plan = []

    # 规则 1：包含检索关键词 → 使用 rag_search 工具
    if any(k in task_lower for k in [
        "查", "检索", "资料", "根据文档", "知识库",
        "search", "retrieve", "find", "lookup"
    ]):
        plan.append({
            "tool": "rag_search",
            "input": {"query": task, "top_k": 5},
            "description": "知识库检索"
        })

    # 规则 2：包含计算关键词 → 使用 calculator 工具
    if any(k in task_lower for k in [
        "计算", "求", "多少", "加", "减", "乘", "除",
        "calculate", "sum", "add", "subtract", "multiply", "divide"
    ]):
        plan.append({
            "tool": "calculator",
            "input": {"expression": task},
            "description": "数学计算"
        })

    # 规则 3：默认使用 LLM 生成
    if not plan:
        plan.append({
            "tool": "llm_generate",
            "input": {"prompt": task},
            "description": "大模型生成"
        })

    return {
        "plan": plan,
        "created_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    }


def aggregate_results(results: List[Dict]) -> str:
    """
    汇总工具执行结果
    :param results: 工具执行结果列表
    :return: 汇总后的文本
    """
    if not results:
        return "无执行结果"

    summary_parts = []
    for r in results:
        tool_name = r.get("tool", "unknown")
        output = r.get("output", {})
        summary_parts.append(f"[{tool_name}] 执行完成")

    return "\n".join(summary_parts)
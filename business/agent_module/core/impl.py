# -*- coding: utf-8 -*-
"""
Agent 模块具体实现类
实现完整 Agent 全流程，串联所有依赖模块，是系统默认使用的 Agent 实现类
"""

import time
import uuid
from typing import Dict, Any, Optional, List, Callable

from .base import BaseAgent
from ..model.data_model import (
    AgentRequest,
    AgentResponse,
    TaskPlan,
    TaskStep,
    ToolResult,
    StateEvent
)
from ..tools.tool_registry import ToolRegistry
from ..utils.tool_functions import parse_task_by_rules, aggregate_results

# 依赖模块导入（遵循设计文档依赖关系）
from state_store_module.core.impl import LocalStateStore
from common_utils_module.core.impl import CommonUtils
from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from exception_module.core.impl import AgentException


class SimpleAgent(BaseAgent):
    """标准 Agent 实现类：基于规则的任务拆解 + 工具调用，系统默认实现"""

    def __init__(self, tools: Optional[Dict[str, Callable]] = None):
        """
        初始化 Agent 模块，加载系统配置，注册默认工具
        :param tools: 初始工具字典（可选），格式：{"tool_name": callable}
        """
        # 基础支撑层初始化
        self.utils = CommonUtils()
        self.logger = SystemLogger()
        self.config = ConfigManager()
        self.config.load_config()

        # 状态存储模块初始化
        self.state_store = LocalStateStore()

        # 工具注册表初始化
        self.tool_registry = ToolRegistry()

        # 注册初始工具（若传入）
        if tools:
            for tool_name, tool_func in tools.items():
                self.tool_registry.register(tool_name, tool_func)

        # 读取系统 Agent 核心配置
        self.max_retries = int(self.config.get_config("agent.max_retries", 3))
        self.timeout = int(self.config.get_config("agent.timeout", 30))
        self.session_prefix = self.config.get_config(
            "agent.session_prefix",
            "agent_session"
        )

        self.logger.info("Agent 模块初始化完成，加载系统默认配置")

    def parse_task(self, task: str) -> Dict[str, Any]:
        """实现抽象方法：任务解析，基于规则拆解任务为子步骤"""
        try:
            # 1. 基于关键词规则解析任务（可扩展为 LLM 解析）
            plan_dict = parse_task_by_rules(task)

            # 2. 记录任务解析事件到状态存储
            session_id = self._get_or_create_session()
            event = StateEvent(
                event_type="plan",
                data=plan_dict,
                timestamp=self.utils.get_assist_tool().get_current_time()
            )
            self.state_store.append_event(session_id, {
                "event_type": event.event_type,
                "data": event.data,
                "timestamp": event.timestamp
            })

            return plan_dict

        except Exception as e:
            self.logger.error(f"Agent 任务解析失败：{str(e)}")
            raise AgentException("AGENT_TASK_PARSE_FAILED", f"任务解析失败：{str(e)}")

    def execute(self, task: str, session_id: Optional[str] = None) -> Dict[str, Any]:
        """实现抽象方法：任务执行，调用工具并汇总结果"""
        start_time = time.time()
        session_id = session_id or self._get_or_create_session()

        try:
            # 1. 参数校验
            if not task or not task.strip():
                raise AgentException("PARAM_MISSING", "任务描述不能为空")

            # 2. 任务解析
            plan_dict = self.parse_task(task)
            plan_steps = plan_dict.get("plan", [])

            # 3. 执行子步骤
            results = []
            for step in plan_steps:
                # 超时检查
                if time.time() - start_time > self.timeout:
                    raise AgentException("AGENT_TIMEOUT", "Agent 执行超时")

                tool_name = step.get("tool")
                tool_input = step.get("input", {})

                # 工具调用（含重试机制）
                tool_result = self._call_tool_with_retry(
                    tool_name,
                    tool_input,
                    session_id
                )
                results.append({
                    "tool": tool_name,
                    "output": tool_result
                })

            # 4. 结果汇总
            aggregated_result = aggregate_results(results)

            # 5. 结果封装
            cost_time = time.time() - start_time
            return {
                "code": "SUCCESS",
                "message": "Agent 执行成功",
                "data": {
                    "task": task,
                    "session_id": session_id,
                    "plan": plan_dict,
                    "results": results,
                    "aggregated_result": aggregated_result
                },
                "cost_time": cost_time
            }

        except AgentException:
            raise
        except Exception as e:
            self.logger.error(f"Agent 执行失败：{str(e)}")
            raise AgentException("AGENT_EXECUTE_FAILED", str(e))

    def call_agent(self, request: AgentRequest) -> AgentResponse:
        """实现抽象方法：标准化 Agent 调用入口，请求校验 + 异常封装"""
        try:
            # 1. 调用全流程
            result_dict = self.execute(
                task=request.task,
                session_id=request.session_id
            )

            # 2. 转换为 AgentResponse 模型
            trace_id = self.utils.get_assist_tool().get_current_time(
                format_="YYYYMMDDHHmmss"
            )

            response = AgentResponse(
                code=result_dict["code"],
                message=result_dict["message"],
                data=result_dict.get("data"),
                cost_time=result_dict.get("cost_time"),
                trace_id=trace_id
            )
            return response

        except AgentException as e:
            trace_id = self.utils.get_assist_tool().get_current_time(
                format_="YYYYMMDDHHmmss"
            )
            return AgentResponse(
                code=e.code,
                message=e.message,
                data=None,
                trace_id=trace_id
            )
        except Exception as e:
            trace_id = self.utils.get_assist_tool().get_current_time(
                format_="YYYYMMDDHHmmss"
            )
            return AgentResponse(
                code="AGENT_EXECUTE_FAILED",
                message=str(e),
                data=None,
                trace_id=trace_id
            )

    def register_tool(self, tool_name: str, tool_func: Callable,
                      description: str, input_schema: Dict) -> bool:
        """实现抽象方法：注册工具到工具池"""
        try:
            self.tool_registry.register(
                tool_name,
                tool_func,
                description,
                input_schema
            )
            self.logger.info(f"工具注册成功：{tool_name}")
            return True
        except Exception as e:
            self.logger.error(f"工具注册失败：{tool_name}, {str(e)}")
            return False

    def unregister_tool(self, tool_name: str) -> bool:
        """实现抽象方法：从工具池移除工具"""
        try:
            self.tool_registry.unregister(tool_name)
            self.logger.info(f"工具注销成功：{tool_name}")
            return True
        except Exception as e:
            self.logger.error(f"工具注销失败：{tool_name}, {str(e)}")
            return False

    def _get_or_create_session(self) -> str:
        """私有方法：获取或创建会话 ID"""
        return f"{self.session_prefix}_{uuid.uuid4().hex[:12]}"

    def _call_tool_with_retry(self, tool_name: str, tool_input: Dict,
                              session_id: str) -> Dict:
        """私有方法：工具调用（含重试机制）"""
        last_err = None

        for attempt in range(self.max_retries):
            try:
                # 1. 获取工具函数
                tool_func = self.tool_registry.get(tool_name)
                if not tool_func:
                    raise AgentException("TOOL_NOT_FOUND", f"工具不存在：{tool_name}")

                # 2. 调用工具
                result = tool_func(tool_input)

                # 3. 记录工具调用事件
                event = StateEvent(
                    event_type="tool",
                    data={
                        "tool": tool_name,
                        "input": tool_input,
                        "output": result
                    },
                    timestamp=self.utils.get_assist_tool().get_current_time()
                )
                self.state_store.append_event(session_id, {
                    "event_type": event.event_type,
                    "data": event.data,
                    "timestamp": event.timestamp
                })

                return result

            except Exception as e:
                last_err = str(e)
                self.logger.warning(
                    f"工具调用失败：{tool_name}, 尝试 {attempt + 1}/{self.max_retries}, err={last_err}"
                )

        # 所有重试均失败
        raise AgentException("TOOL_CALL_FAILED", f"工具调用失败：{tool_name}, err={last_err}")
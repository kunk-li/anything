# -*- coding: utf-8 -*-
"""
自我验证闭环 (方向 3 / 阶段 1): 执行类任务的分层验证 + 自纠正。

设计:
    三层验证模型 (Step / Goal / Task 终态确认) —— 阶段 1 先落地 Task 终态 + Execution。
    验证器可插拔, 验证器自身故障一律 fail-open (放行 + 标注), 绝不因"验证这件事"
    本身出错而阻断主流程 —— 验证是增强, 不是新的失败源。

验证器:
    ToolSuccessVerifier  基线, 看 tool_result.success (零成本)
    ExecutionVerifier    pytest / sql / shell / lint 四场景, 跑验证命令判 exit/输出,
                         失败把 stderr/stdout 作为 self-correct 的 feedback
    TaskVerifier         LLMJudge 终态确认, 对照原始目标判整个任务是否真完成
    (阶段 2) ComplianceVerifier  对照 AGENTS.md / MEMORY.md 规范验"过程是否合规"

模式 (verify_mode): off / auto / ask  —— 由接入层根据 config 决定如何调度本模块。

本模块零外部依赖 (runner / llm_call 由接入层注入), 可独立单测。
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional


@dataclass
class VerifyResult:
    """单次验证的结果。"""
    passed: bool
    feedback: str = ""           # 失败时的具体原因/错误, 喂给 self-correct
    fixable: bool = True         # 能否通过纠正修复; False → 交回上层换策略, 不再原地纠正
    score: Optional[float] = None
    verifier: str = ""           # 产出该结果的验证器名


@dataclass
class VerifySpec:
    """一条验证规格 (来自 extra_params.verify 或 plan step 声明)。"""
    type: str                                    # pytest / sql / shell / lint / task / tool_success
    target: str = ""                             # 验证目标: 路径 / SQL / 命令
    args: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_obj(cls, obj: Any) -> Optional["VerifySpec"]:
        """从 dict / str 宽松构造, 非法返回 None。"""
        if isinstance(obj, VerifySpec):
            return obj
        if isinstance(obj, str):
            return cls(type=obj.strip())
        if isinstance(obj, dict) and obj.get("type"):
            return cls(type=str(obj["type"]).strip(),
                       target=str(obj.get("target", "")),
                       args=dict(obj.get("args") or {}))
        return None


class Verifier(ABC):
    name: str = "base"

    @abstractmethod
    def verify(self, *, goal: str, result: Any, spec: VerifySpec,
               ctx: Optional[Dict[str, Any]] = None) -> VerifyResult:
        ...


class ToolSuccessVerifier(Verifier):
    """基线验证器: 工具是否成功 (零成本, 复用已有 tool_result.success)。"""
    name = "tool_success"

    def verify(self, *, goal, result, spec, ctx=None) -> VerifyResult:
        ok = bool(isinstance(result, dict) and result.get("success"))
        if ok:
            return VerifyResult(passed=True, verifier=self.name)
        err = result.get("error") if isinstance(result, dict) else result
        return VerifyResult(passed=False, feedback=f"工具未成功: {err}", verifier=self.name)


# 接入层注入: 跑一条验证命令, 返回 {exit_code:int, stdout:str, stderr:str}
ExecutionRunner = Callable[[VerifySpec], Dict[str, Any]]


class ExecutionVerifier(Verifier):
    """确定性执行验证: pytest / sql / shell / lint。

    统一内核: 经注入的 runner 跑验证命令, exit_code==0 视为通过, 否则把
    stderr(优先)/stdout 作为可纠正的 feedback。runner 由接入层映射到现有工具
    (shell_exec / sql_query / code_lint), 本类不关心具体工具。
    """
    name = "execution"
    SUPPORTED = ("pytest", "sql", "shell", "lint")

    def __init__(self, runner: ExecutionRunner):
        self.runner = runner

    def verify(self, *, goal, result, spec, ctx=None) -> VerifyResult:
        if spec.type not in self.SUPPORTED:
            return VerifyResult(passed=True, fixable=False, verifier=self.name,
                                feedback=f"(execution 不支持 type={spec.type}, 跳过)")
        try:
            out = self.runner(spec) or {}
        except Exception as e:
            # 执行验证本身异常 (环境/工具不可用): 视为未通过但可重试, 由 max_correction 兜底
            return VerifyResult(passed=False, verifier=self.name,
                                feedback=f"[{spec.type}] 验证执行异常: {e}")
        code = out.get("exit_code", out.get("code", 1))
        if code == 0:
            return VerifyResult(passed=True, score=1.0, verifier=self.name)
        detail = (out.get("stderr") or out.get("stdout") or "").strip()
        return VerifyResult(
            passed=False, verifier=self.name,
            feedback=f"[{spec.type}] 验证未通过 (exit={code}):\n{detail[:1500]}",
        )


# 接入层注入: prompt -> text (复用 agent 的 llm planner 通道)
LLMCall = Callable[[str], str]


class TaskVerifier(Verifier):
    """终态确认 (LLMJudge): 对照原始目标判整个任务是否真完成。

    fail-open: LLM 不可用 / 输出无法解析时一律放行 (passed=True + 标注),
    不因"验收这件事"失败而把已完成的任务判死。
    """
    name = "task"

    def __init__(self, llm_call: LLMCall):
        self.llm_call = llm_call

    @staticmethod
    def _extract_answer(result: Any) -> str:
        if isinstance(result, dict):
            data = result.get("data") if isinstance(result.get("data"), dict) else {}
            return str(result.get("answer") or data.get("answer") or result)[:2000]
        return str(result)[:2000]

    def verify(self, *, goal, result, spec, ctx=None) -> VerifyResult:
        answer = self._extract_answer(result)
        prompt = (
            "你是严格的验收员。判断下面的执行结果是否真正、完整地达成了用户目标。\n"
            f"【用户目标】\n{goal}\n\n【执行结果】\n{answer}\n\n"
            '只输出 JSON, 不要其他文字: '
            '{"completed": true/false, "reason": "判断理由", "missing": "未完成的缺口(若全部完成则空串)"}'
        )
        try:
            raw = self.llm_call(prompt) or ""
        except Exception as e:
            return VerifyResult(passed=True, fixable=False, verifier=self.name,
                                feedback=f"(task 验证器 LLM 异常, 放行: {e})")
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return VerifyResult(passed=True, fixable=False, verifier=self.name,
                                feedback="(task 验证器输出无 JSON, 放行)")
        try:
            data = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return VerifyResult(passed=True, fixable=False, verifier=self.name,
                                feedback="(task 验证器 JSON 解析失败, 放行)")
        if bool(data.get("completed")):
            return VerifyResult(passed=True, score=1.0, verifier=self.name)
        return VerifyResult(
            passed=False, verifier=self.name,
            feedback=f"任务未完成: {data.get('reason', '')}; 缺口: {data.get('missing', '')}",
        )


def run_verifiers(
    *,
    goal: str,
    result: Any,
    specs: List[VerifySpec],
    registry: Dict[str, Verifier],
    ctx: Optional[Dict[str, Any]] = None,
) -> List[VerifyResult]:
    """按 specs 逐条调度对应验证器, 汇总结果。未注册的 type 跳过(放行)。"""
    results: List[VerifyResult] = []
    for spec in specs:
        verifier = registry.get(spec.type)
        if verifier is None:
            # execution 四场景共用一个 ExecutionVerifier 实例 (注册在 'execution')
            if spec.type in ExecutionVerifier.SUPPORTED:
                verifier = registry.get("execution")
        if verifier is None:
            results.append(VerifyResult(passed=True, fixable=False, verifier="none",
                                        feedback=f"(无 {spec.type} 验证器, 跳过)"))
            continue
        results.append(verifier.verify(goal=goal, result=result, spec=spec, ctx=ctx))
    return results


def collect_specs(extra_params: Optional[Dict[str, Any]]) -> List[VerifySpec]:
    """从 extra_params.verify 解析 Execution specs, 末尾追加 task 终态确认。

    extra_params.verify 形如 [{"type":"pytest","target":"tests/"}, "lint", ...];
    extra_params.verify_task=False 可关掉终态确认 (默认开)。
    """
    specs: List[VerifySpec] = []
    raw = (extra_params or {}).get("verify") or []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            s = VerifySpec.from_obj(item)
            if s is not None:
                specs.append(s)
    if (extra_params or {}).get("verify_task", True):
        specs.append(VerifySpec(type="task"))
    return specs


def make_registry(runner: ExecutionRunner, llm_call: LLMCall) -> Dict[str, Verifier]:
    """构造默认验证器注册表 (tool_success / execution / task)。"""
    return {
        "tool_success": ToolSuccessVerifier(),
        "execution": ExecutionVerifier(runner=runner),
        "task": TaskVerifier(llm_call=llm_call),
    }

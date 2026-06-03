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
    GoalVerifier         LLMJudge 子目标级 (三层模型的 Goal 层), 拆子目标逐个验收, 比
                         Task 整体判定更细; 子目标可由调用方显式给 (留 plan goal 分组接入点)
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


class GoalVerifier(Verifier):
    """子目标级验收 (方向3 三层模型的 Goal 层): 把目标拆成若干子目标, 逐个对照执行结果
    判是否达成 —— 比 TaskVerifier 的整体判定更细粒度, 给自纠正更准的缺口反馈
    (能抓出"整体看着完成、实则漏了某子目标"的半成品)。

    子目标来源: 优先用调用方显式给的 (spec.args['goals'] 或 ctx['sub_goals']), 否则让 LLM
    现场从 goal 拆。这给"plan 真做了 goal 分组"留了无缝接入点(把分组喂进 spec.args 即可),
    当前无需改动规划核心。

    fail-open: LLM 不可用 / 输出无法解析时一律放行 (passed=True + 标注), 同 TaskVerifier ——
    不因"验收这件事"失败而把已完成的任务判死。
    """
    name = "goal"

    def __init__(self, llm_call: LLMCall):
        self.llm_call = llm_call

    @staticmethod
    def _explicit_goals(spec: VerifySpec, ctx: Optional[Dict[str, Any]]) -> List[str]:
        raw = (spec.args or {}).get("goals")
        if not raw and isinstance(ctx, dict):
            raw = ctx.get("sub_goals")
        if isinstance(raw, (list, tuple)):
            return [str(g).strip() for g in raw if str(g).strip()]
        return []

    def verify(self, *, goal, result, spec, ctx=None) -> VerifyResult:
        answer = TaskVerifier._extract_answer(result)
        explicit = self._explicit_goals(spec, ctx)
        if explicit:
            goals_block = ("【需逐项验收的子目标】\n"
                           + "\n".join(f"{i + 1}. {g}" for i, g in enumerate(explicit)) + "\n\n")
            instruction = "对照【执行结果】逐项判断上面列出的每个子目标是否达成。"
        else:
            goals_block = ""
            instruction = "先把【总目标】拆成几个具体、可验收的子目标, 再对照【执行结果】逐个判断是否达成。"
        prompt = (
            "你是严格的验收员。" + instruction + "\n"
            f"【总目标】\n{goal}\n\n" + goals_block + f"【执行结果】\n{answer}\n\n"
            '只输出 JSON, 不要其他文字: '
            '{"sub_goals": [{"goal": "子目标", "met": true/false, "missing": "未达成的缺口(达成则空串)"}], '
            '"all_met": true/false}'
        )
        try:
            raw = self.llm_call(prompt) or ""
        except Exception as e:
            return VerifyResult(passed=True, fixable=False, verifier=self.name,
                                feedback=f"(goal 验证器 LLM 异常, 放行: {e})")
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return VerifyResult(passed=True, fixable=False, verifier=self.name,
                                feedback="(goal 验证器输出无 JSON, 放行)")
        try:
            data = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return VerifyResult(passed=True, fixable=False, verifier=self.name,
                                feedback="(goal 验证器 JSON 解析失败, 放行)")
        sub = data.get("sub_goals")
        if not isinstance(sub, list) or not sub:
            # 没拆出子目标 → 退回整体 all_met 判定, 不阻断
            return VerifyResult(passed=bool(data.get("all_met", True)), fixable=False,
                                verifier=self.name, feedback="(goal 未拆出子目标, 按整体判定)")
        unmet = [sg for sg in sub if isinstance(sg, dict) and not sg.get("met", False)]
        total = len(sub)
        if not unmet:
            return VerifyResult(passed=True, score=1.0, verifier=self.name)
        gaps = "\n".join(
            f"- 子目标未达成: {sg.get('goal', '')}; 缺口: {sg.get('missing', '')}" for sg in unmet
        )
        return VerifyResult(
            passed=False, verifier=self.name,
            score=round((total - len(unmet)) / total, 2),
            feedback=f"{len(unmet)}/{total} 个子目标未达成:\n{gaps}",
        )


# 接入层注入: 返回当前生效的规范文本 (AGENTS.md / MEMORY.md / skills / 调用方传入)
RulesProvider = Callable[[], str]


class ComplianceVerifier(Verifier):
    """规范合规检查 (LLMJudge): 执行结果/做法是否违反【已声明规范】。

    规范来源由 rules_provider 注入 (AGENTS.md / MEMORY.md / skills / extra_params)。
    无规范 → 放行; LLM 故障 / 无法解析 → fail-open 放行。
    """
    name = "compliance"

    def __init__(self, llm_call: LLMCall, rules_provider: RulesProvider):
        self.llm_call = llm_call
        self.rules_provider = rules_provider

    def verify(self, *, goal, result, spec, ctx=None) -> VerifyResult:
        try:
            rules = (self.rules_provider() or "").strip()
        except Exception:
            rules = ""
        if not rules:
            return VerifyResult(passed=True, fixable=False, verifier=self.name,
                                feedback="(无已声明规范, 跳过合规检查)")
        answer = TaskVerifier._extract_answer(result)
        prompt = (
            "你是规范审查员。判断下面的执行结果/做法是否违反了【已声明规范】。\n"
            f"【已声明规范】\n{rules[:3000]}\n\n【目标】\n{goal}\n\n【执行结果】\n{answer}\n\n"
            '只输出 JSON, 不要其他文字: '
            '{"compliant": true/false, "violations": "违反的具体条目(合规则空串)"}'
        )
        try:
            raw = self.llm_call(prompt) or ""
        except Exception as e:
            return VerifyResult(passed=True, fixable=False, verifier=self.name,
                                feedback=f"(compliance LLM 异常, 放行: {e})")
        m = re.search(r"\{[\s\S]*\}", raw)
        if not m:
            return VerifyResult(passed=True, fixable=False, verifier=self.name,
                                feedback="(compliance 输出无 JSON, 放行)")
        try:
            data = json.loads(m.group(0))
        except (json.JSONDecodeError, ValueError):
            return VerifyResult(passed=True, fixable=False, verifier=self.name,
                                feedback="(compliance JSON 解析失败, 放行)")
        if bool(data.get("compliant", True)):
            return VerifyResult(passed=True, verifier=self.name)
        return VerifyResult(passed=False, verifier=self.name,
                            feedback=f"违反规范: {data.get('violations', '')}")


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
    """从 extra_params.verify 解析 Execution specs, 追加 goal(opt-in) + task 终态确认。

    extra_params.verify 形如 [{"type":"pytest","target":"tests/"}, "lint", ...];
    extra_params.verify_goals=True (默认关) 追加子目标级验收, 可带 extra_params.goals
        =["子目标1", ...] 显式指定要验收的子目标 (不给则 GoalVerifier 现场拆);
    extra_params.verify_task=False 可关掉终态确认 (默认开)。
    """
    specs: List[VerifySpec] = []
    ep = extra_params or {}
    raw = ep.get("verify") or []
    if isinstance(raw, (list, tuple)):
        for item in raw:
            s = VerifySpec.from_obj(item)
            if s is not None:
                specs.append(s)
    # 方向3 GoalVerifier: 子目标级验收, opt-in (默认关), 不改变既有 verify 行为
    if ep.get("verify_goals"):
        specs.append(VerifySpec(type="goal", args={"goals": ep.get("goals") or []}))
    if ep.get("verify_task", True):
        specs.append(VerifySpec(type="task"))
    return specs


def make_registry(runner: ExecutionRunner, llm_call: LLMCall,
                  rules_provider: Optional[RulesProvider] = None) -> Dict[str, Verifier]:
    """构造默认验证器注册表 (tool_success / execution / task [/ compliance])。
    rules_provider 提供时额外注册 compliance 验证器。"""
    reg: Dict[str, Verifier] = {
        "tool_success": ToolSuccessVerifier(),
        "execution": ExecutionVerifier(runner=runner),
        "goal": GoalVerifier(llm_call=llm_call),
        "task": TaskVerifier(llm_call=llm_call),
    }
    if rules_provider is not None:
        reg["compliance"] = ComplianceVerifier(llm_call=llm_call, rules_provider=rules_provider)
    return reg

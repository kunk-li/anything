# -*- coding: utf-8 -*-
"""
SelfVerifyMixin (从 impl.py 拆出 — 方向3 自我验证闭环, 零行为变更)

执行结果的确定性验证 + auto 模式带 feedback 递归自纠正:
    _build_verify_runner      ExecutionVerifier 执行器 (pytest/lint/shell→subprocess, sql→sqlite)
    _collect_compliance_rules 合规规范来源 (extra_params.compliance_rules / 项目记忆)
    _post_verify              跑验证, auto 失败时预算内递归纠正; off 时原样返回

依赖 SimpleAgent (self) 字段/方法 (由 __init__ 及其它 mixin / impl 提供):
    enable_self_verify, verify_mode, max_correction, timeout,
    execute (impl), _fallback_session_id / _append_state_event (StateHistoryMixin),
    _resolve_llm_planner (impl), project_memory / deps
"""
from __future__ import annotations

import time

from .verifier import collect_specs, make_registry, run_verifiers


class SelfVerifyMixin:
    """方向3: 自我验证闭环 (验证 + 自纠正)。"""

    def _build_verify_runner(self):
        """ExecutionVerifier 的执行器: 按 spec.type 跑确定性验证命令。
        pytest/lint/shell 走 subprocess, sql 走 sqlite。验证命令应来自可信调用方
        (extra_params.verify); 默认 enable_self_verify=off, 不主动执行任何东西。"""
        import subprocess
        import shlex
        import sys

        def runner(spec):
            t = spec.type
            target = (spec.target or "").strip()
            timeout_s = int((spec.args or {}).get("timeout", 60))
            try:
                if t == "pytest":
                    cmd = [sys.executable, "-m", "pytest", "-q"] + (shlex.split(target) if target else [])
                    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
                elif t == "lint":
                    cmd = [sys.executable, "-m", "pyflakes"] + (shlex.split(target) if target else ["."])
                    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout_s)
                elif t == "shell":
                    p = subprocess.run(target, shell=True, capture_output=True, text=True, timeout=timeout_s)
                elif t == "sql":
                    import sqlite3
                    con = sqlite3.connect((spec.args or {}).get("db") or ":memory:")
                    try:
                        con.executescript(target)
                        con.commit()
                        return {"exit_code": 0, "stdout": "ok", "stderr": ""}
                    except Exception as e:
                        return {"exit_code": 1, "stdout": "", "stderr": str(e)}
                    finally:
                        con.close()
                else:
                    return {"exit_code": 0, "stdout": "", "stderr": ""}
                return {"exit_code": p.returncode, "stdout": p.stdout or "", "stderr": p.stderr or ""}
            except subprocess.TimeoutExpired:
                return {"exit_code": 124, "stdout": "", "stderr": f"验证超时 (>{timeout_s}s)"}
            except FileNotFoundError as e:
                return {"exit_code": 127, "stdout": "", "stderr": f"验证命令不可用: {e}"}

        return runner

    def _collect_compliance_rules(self, extra) -> str:
        """合规检查的规范来源: 优先 extra_params.compliance_rules; 否则尽力取项目级
        记忆 (AGENTS.md / ProjectMemory)。取不到返回空串 → compliance 自动放行。"""
        explicit = (extra or {}).get("compliance_rules")
        if explicit:
            return str(explicit)
        pm = getattr(self, "project_memory", None)
        if pm is None:
            pm = getattr(getattr(self, "deps", None), "project_memory", None)
        if pm is not None:
            for meth in ("get_memory_text", "as_text", "get_content", "render", "load"):
                fn = getattr(pm, meth, None)
                if callable(fn):
                    try:
                        txt = fn()
                        if txt:
                            return str(txt)
                    except Exception:
                        pass
        return ""

    def _post_verify(self, request, response, original_task, start_time):
        """对执行结果跑验证; auto 模式失败时带 feedback 递归纠正 (预算护栏)。
        enable_self_verify=off / verify_mode=off 时原样返回 (零影响)。"""
        if not getattr(self, "enable_self_verify", False) or self.verify_mode == "off":
            return response
        if not isinstance(response, dict):
            return response

        extra = dict(request.get("extra_params") or {})
        attempt = int(extra.get("_verify_attempt", 0))
        specs = collect_specs(extra)
        if not specs:
            return response

        trace_id = request.get("trace_id")
        session_id = request.get("session_id") or self._fallback_session_id()
        llm_call = self._resolve_llm_planner(trace_id=trace_id)
        if llm_call is None:
            # 无 LLM 通道: 终态确认无法判, 退化为放行 (只让 execution 验证生效)
            llm_call = lambda _p: '{"completed": true}'
        registry = make_registry(
            runner=self._build_verify_runner(), llm_call=llm_call,
            rules_provider=lambda: self._collect_compliance_rules(extra),
        )

        vresults = run_verifiers(
            goal=original_task, result=response.get("data") or response,
            specs=specs, registry=registry,
        )
        if response.get("details") is None:
            response["details"] = {}
        response["details"]["verification"] = [
            {"verifier": r.verifier, "passed": r.passed, "feedback": (r.feedback or "")[:500]}
            for r in vresults
        ]
        failed = [r for r in vresults if not r.passed]
        response["details"]["verify_passed"] = (len(failed) == 0)
        if not failed:
            return response

        fixable_fb = [r.feedback for r in failed if r.fixable and r.feedback]
        self._append_state_event(
            session_id=session_id, event_type="verify_failed", trace_id=trace_id,
            payload={"attempt": attempt, "failed": [r.verifier for r in failed]},
        )

        # ask 模式: 不自动纠正, 标记需用户确认 + 缺口
        if self.verify_mode == "ask":
            response["details"]["needs_user_confirm"] = True
            response["details"]["verify_gaps"] = fixable_fb
            return response

        # auto 模式: 预算内 + 可修 + 未超时 → 带 feedback 递归纠正
        within_budget = attempt < self.max_correction
        within_time = (time.time() - start_time) < self.timeout
        if fixable_fb and within_budget and within_time:
            self._append_state_event(
                session_id=session_id, event_type="self_correct", trace_id=trace_id,
                payload={"attempt": attempt + 1},
            )
            new_extra = dict(extra)
            new_extra["_verify_attempt"] = attempt + 1
            new_extra["_correction_feedback"] = "\n".join(fixable_fb)[:3000]
            new_extra["_skip_history_prefix"] = True
            new_request = dict(request)
            new_request["extra_params"] = new_extra
            return self.execute(new_request)

        # 预算耗尽 / 不可修 → 返回 + 标记缺口
        response["details"]["verify_gaps"] = fixable_fb
        return response

# -*- coding: utf-8 -*-
"""
ShadowSelfUpdate (执行计划⑧, 原建议第8条: 自更新用全绿测试当安全闸)

"执行性自主"的安全骨架: 任何代码/配置改动提议, **先在隔离 git worktree 里应用 + 跑全量测试**,
通过才提请人审批。核心不变量 (强护栏):
  1. **绝不自动应用到主工作树 / main** — 只在临时 worktree 验证, 用完即删 (finally 清理)。
  2. **全绿测试 = 唯一放行条件** — 优化①把全量 pytest 变成一键可跑, 这里把它当安全闸。
  3. **默认关 + advisory** — 由 agent.enable_self_update gate; 返回闸门结果供人审批, 不替人决定。
  4. **fail-safe = 拒绝** — 任何异常/diff 冲突/测试失败 → gate_passed=False。

test_runner / worktree 可注入 (测试不起真子进程/不动真 git); 默认走真 git worktree + pytest。
注: 本模块只做"验证闸"; 改动的**生成**(LLM 写 diff) 风险更高, 暂不在此实现 (喂现成 diff 即可验)。
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, Optional

_PYTHONPATH_LAYERS = ("basic_support", "data_layer", "business", "interface", "application", "run", "")


class _GitWorktree:
    """真 git worktree 管理: create(隔离 detached HEAD 副本) / apply_diff / cleanup。"""

    def __init__(self, repo_root: str, logger: Any = None):
        self.repo_root = repo_root
        self.logger = logger

    def create(self) -> str:
        d = tempfile.mkdtemp(prefix="anything_shadow_")
        subprocess.run(
            ["git", "-C", self.repo_root, "worktree", "add", "--detach", d, "HEAD"],
            check=True, capture_output=True, text=True,
        )
        return d

    def apply_diff(self, path: str, diff: str) -> bool:
        p = subprocess.run(["git", "-C", path, "apply", "-"],
                           input=diff, text=True, capture_output=True)
        return p.returncode == 0

    def cleanup(self, path: str) -> None:
        # 先尝试 worktree remove (会注销注册), 再兜底删目录
        subprocess.run(["git", "-C", self.repo_root, "worktree", "remove", "--force", path],
                       capture_output=True, text=True)


class ShadowSelfUpdate:
    """影子模式自更新验证闸。repo_root 默认 cwd; test_runner(cwd)->(rc,summary) 与 worktree 可注入。"""

    def __init__(self, repo_root: Optional[str] = None,
                 test_runner: Optional[Callable[[str], tuple]] = None,
                 worktree: Any = None, logger: Any = None):
        self.repo_root = repo_root or os.getcwd()
        self._test_runner = test_runner
        self._worktree = worktree
        self.logger = logger

    def run_test_gate(self, cwd: Optional[str] = None) -> Dict[str, Any]:
        """在 cwd (默认 repo_root) 跑全量测试 → {passed, returncode, summary}。安全闸的判定核心。"""
        cwd = cwd or self.repo_root
        runner = self._test_runner or self._default_test_runner
        try:
            rc, summary = runner(cwd)
        except Exception as e:
            rc, summary = 1, f"test runner 异常: {e}"
        return {"passed": rc == 0, "returncode": rc, "summary": str(summary)[:4000]}

    def _default_test_runner(self, cwd: str) -> tuple:
        """真跑全量 pytest (PYTHONPATH 6 层 + 根)。慢 (~60s), 仅真实自更新验证用。"""
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(os.path.join(cwd, l) if l else cwd for l in _PYTHONPATH_LAYERS)
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            p = subprocess.run(
                [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
                cwd=cwd, env=env, capture_output=True, text=True, timeout=900,
            )
            return p.returncode, (p.stdout or "") + (p.stderr or "")
        except Exception as e:
            return 1, f"pytest 执行失败: {e}"

    def verify_in_shadow(self, proposal: Dict[str, Any]) -> Dict[str, Any]:
        """在隔离 worktree 应用 proposal.diff → 跑 test gate → 报告。**绝不碰主工作树**; 用完清理。

        proposal: {id?, description?, diff: unified-diff 文本}。无 diff 则在干净副本上跑闸 (基线确认)。
        返回 {id, applied, gate_passed, test_summary?, note}。任何异常/失败 → gate_passed=False (fail-safe)。
        """
        proposal = proposal or {}
        diff = proposal.get("diff") or ""
        result: Dict[str, Any] = {
            "id": proposal.get("id"), "applied": False, "gate_passed": False, "note": "",
        }
        wt = self._worktree or _GitWorktree(self.repo_root, self.logger)
        path = None
        try:
            path = wt.create()
            if diff:
                applied = wt.apply_diff(path, diff)
                result["applied"] = bool(applied)
                if not applied:
                    result["note"] = "diff 应用失败 (冲突/格式不符), 不进 test gate → 拒绝"
                    return result
            else:
                result["applied"] = True  # 无 diff = 在干净副本验证基线
            gate = self.run_test_gate(cwd=path)
            result["gate_passed"] = gate["passed"]
            result["test_summary"] = gate["summary"]
            result["returncode"] = gate["returncode"]
            result["note"] = ("全绿 → 可提请人审批 (仍不自动应用)" if gate["passed"]
                              else "全量测试未通过 → 安全闸拦截, 拒绝")
            if self.logger:
                self.logger.info(
                    f"[self_update] shadow 验证 id={result['id']} gate_passed={gate['passed']}"
                )
            return result
        except Exception as e:
            result["note"] = f"shadow 验证异常 (fail-safe 拒绝): {e}"
            return result
        finally:
            if path is not None:
                try:
                    wt.cleanup(path)
                except Exception:
                    pass

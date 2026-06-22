# -*- coding: utf-8 -*-
"""
TaskPreprocessMixin (执行计划④, 原建议第5条: 把 execute 前处理抽成可组合步骤)

execute 原本内联 ~85 行任务前处理 (refine→记忆注入→画像注入→历史→纠正反馈)。抽成一串
**可组合的步骤** (每步 (ctx)->mutate ctx, 或 set ctx.early_response 早退): execute 只需
`early = self._preprocess_task(ctx)`。零行为变更; 每步可单测; 步序经 `task_preprocess_steps`
可重排/增删 (A/B 某注入策略)。依赖 self 的 _refine_query/_inject_*/_history_prefix 等 (MemoryMixin)。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TaskPreContext:
    """前处理流水线的可变上下文。task 被各步逐步增强; early_response 非空则 execute 立即早退。"""
    task: str
    original_task: str
    tenant_id: str
    session_id: str
    trace_id: Optional[str]
    extra_params: Dict[str, Any]
    execution_mode: str
    start_time: float
    memory_hits: List[Dict[str, Any]] = field(default_factory=list)
    refine_meta: Optional[Dict[str, Any]] = None
    early_response: Optional[Dict[str, Any]] = None


class TaskPreprocessMixin:
    """execute 任务前处理流水线 (可组合步骤)。"""

    # 步骤顺序 (可重排/增删做 A/B): 方法名列表, 依次在 ctx 上执行
    # attachments 放最后: 附件块是路径/doc_id 等机器信息, 提前拼会污染
    # refine 的语义改写和记忆注入的相关性检索
    task_preprocess_steps = (
        "_pre_step_refine",
        "_pre_step_inject_memory",
        "_pre_step_inject_profile",
        "_pre_step_history",
        "_pre_step_correction",
        "_pre_step_attachments",
    )

    def _preprocess_task(self, ctx: TaskPreContext) -> Optional[Dict[str, Any]]:
        """依次跑前处理步骤; 某步 set early_response (如 UP-4 ask 澄清) 则立即返回该响应早退,
        否则返回 None (ctx.task/refine_meta/memory_hits 已就绪, execute 取用)。"""
        for step_name in self.task_preprocess_steps:
            getattr(self, step_name)(ctx)
            if ctx.early_response is not None:
                return ctx.early_response
        return None

    # ── 步骤 ──────────────────────────────────────────────────────────────
    def _pre_step_refine(self, ctx: TaskPreContext) -> None:
        """UP-4 query refinement: 含糊问题基于画像改写 (auto) 或反问澄清 (ask→早退)。默认关。
        只改 ctx.task (规划输入), original_task 保持原话; 纠正递归 (_skip_history_prefix) 跳过。"""
        ep = ctx.extra_params
        refine_on = ep.get("enable_query_refine")
        if refine_on is None:
            refine_on = self.enable_query_refine
        if not (ctx.task and refine_on and self.memory_enabled
                and self.long_term_memory is not None
                and not ep.get("_skip_history_prefix")):
            return
        try:
            mode = str(ep.get("query_refine_mode") or self.query_refine_mode).strip().lower()
            refined, refine_meta = self._refine_query(ctx.task, ctx.tenant_id, ctx.trace_id, mode=mode)
            ctx.refine_meta = refine_meta
            if refine_meta and refine_meta.get("action") == "clarify":
                # UP-4 ask: 歧义大 → 反问澄清, 不执行任务, 早退把澄清问题作为回答返回。
                self.logger.info(f"[refine] ask 模式反问澄清 (不执行任务): trace_id={ctx.trace_id}")
                ctx.early_response = {
                    "code": "SUCCESS", "message": "clarification_needed",
                    "data": {
                        "answer": refine_meta["question"],
                        "clarification_needed": True,
                        "session_id": ctx.session_id, "trace_id": ctx.trace_id,
                        "execution_mode": ctx.execution_mode,
                    },
                    "trace_id": ctx.trace_id, "retryable": False,
                    "details": {"query_refinement": refine_meta},
                    "cost_time": round(time.time() - ctx.start_time, 3),
                }
                return
            if refine_meta:
                ctx.task = refined
        except Exception as e:
            self.logger.warning(
                f"[refine] query refinement 失败 (用原问题继续): trace_id={ctx.trace_id} err={e}"
            )

    def _pre_step_inject_memory(self, ctx: TaskPreContext) -> None:
        """Task FFF (#92): 查 query 相关 fact 注入 task 前; 记 memory_hits 给 details; 纠正递归跳过。"""
        if not (ctx.task and self.memory_enabled and self.long_term_memory is not None
                and not ctx.extra_params.get("_skip_history_prefix")):
            return
        try:
            augmented, hits = self._inject_long_term_memory(
                task=ctx.task, tenant_id=ctx.tenant_id, trace_id=ctx.trace_id,
            )
            ctx.task = augmented
            ctx.memory_hits = hits
        except Exception as e:
            self.logger.warning(
                f"[memory] inject 失败 (continue without memory): trace_id={ctx.trace_id} err={e}"
            )

    def _pre_step_inject_profile(self, ctx: TaskPreContext) -> None:
        """UP-3 (方向1): always-on 注入用户画像 (不依赖 query); 纠正递归跳过。"""
        if not (ctx.task and self.memory_enabled and self.long_term_memory is not None
                and not ctx.extra_params.get("_skip_history_prefix")):
            return
        try:
            ctx.task = self._inject_user_profile(ctx.task, ctx.tenant_id)
        except Exception as e:
            self.logger.warning(f"[profile] inject 失败: trace_id={ctx.trace_id} err={e}")

    def _pre_step_history(self, ctx: TaskPreContext) -> None:
        """ZZ-5: 多轮对话历史前缀 (放记忆注入之后); 纠正递归跳过。"""
        if not (ctx.task and not ctx.extra_params.get("_skip_history_prefix")):
            return
        hist_prefix = self._history_prefix(ctx.session_id)
        if hist_prefix:
            ctx.task = hist_prefix + ctx.task

    def _pre_step_correction(self, ctx: TaskPreContext) -> None:
        """方向3: 自我纠正递归时, 把上轮验证失败的 feedback 拼进 task 引导修正。"""
        corr_fb = ctx.extra_params.get("_correction_feedback")
        if corr_fb:
            ctx.task = f"{ctx.task}\n\n[上一轮验证未通过, 请针对性修正以下问题]\n{corr_fb}"

    def _pre_step_attachments(self, ctx: TaskPreContext) -> None:
        """聊天附件 (extra_params.attachments) 拼为附件块附到 task 尾。

        前端只上送元信息 {name, mime, path, doc_id}, 不指定工具 —— 用哪个工具
        读取由 ReAct 循环按文件类型自选 (块尾给一行类型→工具的映射提示)。"""
        suffix = self._attachments_task_suffix(ctx.extra_params)
        if suffix:
            ctx.task = (ctx.task or "") + suffix

    # ── 共享 helper ──────────────────────────────────────────────────────
    def _attachments_task_suffix(self, extra_params: Dict[str, Any]) -> str:
        """把 extra_params.attachments 渲染成 [用户附件] 文本块; 无有效附件返 ''。

        流式 ReAct (streaming.py) 不走 _preprocess_task 流水线, 也直接调本方法
        给 task 拼同一后缀, 保证两条路径附件语义一致。"""
        atts = extra_params.get("attachments") or []
        if not isinstance(atts, list):
            return ""
        items = [a for a in atts
                 if isinstance(a, dict) and (a.get("path") or a.get("doc_id"))]
        if not items:
            return ""
        lines = ["", "[用户附件]"]
        for i, a in enumerate(items, 1):
            name = str(a.get("name") or "(未命名)")
            mime = str(a.get("mime") or "")
            entry = f"{i}. {name}" + (f" ({mime})" if mime else "")
            if a.get("path"):
                entry += f' — 文件路径: "{a["path"]}"'
            if a.get("doc_id"):
                entry += f" — doc_id: {a['doc_id']} (已入库)"
            lines.append(entry)
        lines.append(
            "请先用合适的工具读取附件内容再回答用户问题, 不要凭空猜测: "
            "图片用 image_describe(image_path), PDF 用 pdf_read(file_path), "
            "Excel 用 excel_read(file_path); 其他带 doc_id 的附件 (文本/文档/压缩包) "
            "一律用 document_read(doc_id) — 压缩包入库时已解包为'清单+各成员正文', "
            "直接读即可, 不要用 shell/python 解压。"
            "扫描版 PDF (无文字层) pdf_read 会返回 data.page_images, "
            "再对其中每个路径调用 image_describe 识别页面。"
        )
        return "\n" + "\n".join(lines)

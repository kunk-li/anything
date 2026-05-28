# -*- coding: utf-8 -*-
"""
项目级记忆加载器 (Task U #55)

借鉴 Codex AGENTS.md / Claude Code CLAUDE.md 模式: 项目根放一份 markdown,
里头写项目背景 / 约定 / 偏好, LLM 在每次执行前都注入到 prompt 顶部,
让回答和工具选择风格保持一致。

查找顺序 (按优先级降序, 第一个存在的文件即用):
    1. 显式路径 (env ANYTHING_PROJECT_MEMORY 或调用时传)
    2. CWD/AGENTS.md
    3. CWD/CLAUDE.md
    4. CWD/.anything/memory.md
    5. <project_root>/AGENTS.md  (project_root = CWD 的祖父级 'run/')

读取行为:
    - 不存在 -> 返回空字符串 (不抛错)
    - 文件大于 max_chars (默认 8000) -> 截断后追加 ' [truncated]'
    - 缓存到磁盘 mtime, 文件变了自动重读 (热刷新, 不需要重启服务)

输出 (inject_into_prompt):
    把原 prompt 拼成:
        <ProjectMemory>
        ...memory.md content...
        </ProjectMemory>

        <Task>
        ...原 prompt...
        </Task>
"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import List, Optional, Tuple


# 默认查找路径列表 (按优先级)
_DEFAULT_CANDIDATES = [
    "AGENTS.md",
    "CLAUDE.md",
    ".anything/memory.md",
]


class ProjectMemory:
    """项目级记忆: 读 AGENTS.md / CLAUDE.md, 按 mtime 热刷新."""

    def __init__(
        self,
        explicit_path: Optional[str] = None,
        candidates: Optional[List[str]] = None,
        max_chars: int = 8000,
    ):
        self._explicit_path = explicit_path
        self._candidates = list(candidates or _DEFAULT_CANDIDATES)
        self._max_chars = max(0, int(max_chars))
        self._cache_path: Optional[Path] = None
        self._cache_mtime: float = 0.0
        self._cache_content: str = ""
        self._lock = threading.Lock()

    @property
    def loaded_path(self) -> Optional[str]:
        """当前生效的 memory 文件路径; None 表示没找到任何文件."""
        with self._lock:
            return str(self._cache_path) if self._cache_path else None

    def _resolve_path(self) -> Optional[Path]:
        """按优先级找第一个存在的文件. 找不到返回 None.

        重要: explicit_path 是"独占"模式 — 一旦构造时传了它, 就只看这一个路径,
        即使不存在也不 fallback 到环境变量/默认候选. 这样测试可以传一个
        临时路径强制场景, 不被项目根的真实 AGENTS.md 干扰.
        """
        # 1) 显式路径独占 — 传了就只看它
        if self._explicit_path is not None:
            p = Path(self._explicit_path).expanduser()
            return p if p.is_file() else None
        # 2) 环境变量
        env_path = os.environ.get("ANYTHING_PROJECT_MEMORY")
        if env_path:
            p = Path(env_path).expanduser()
            if p.is_file():
                return p
        # 3) 默认候选 (相对 cwd)
        for cand in self._candidates:
            p = Path(cand).expanduser()
            if p.is_file():
                return p
        # 4) 项目根兜底: 假设 cwd 是 run/, 则上一级也找一次
        try:
            cwd = Path.cwd()
            for cand in self._candidates:
                p = cwd.parent / cand
                if p.is_file():
                    return p
        except Exception:
            pass
        return None

    def load(self) -> str:
        """读 memory 文件内容. 文件不存在 -> 空字符串. mtime 变了自动重读."""
        with self._lock:
            path = self._resolve_path()
            if path is None:
                self._cache_path = None
                self._cache_mtime = 0.0
                self._cache_content = ""
                return ""
            try:
                mtime = path.stat().st_mtime
            except OSError:
                return self._cache_content  # 短暂 IO 错误用上次的
            if path == self._cache_path and mtime == self._cache_mtime:
                return self._cache_content
            # 重读
            try:
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                return self._cache_content
            if self._max_chars > 0 and len(content) > self._max_chars:
                content = content[: self._max_chars] + "\n\n... [truncated]"
            self._cache_path = path
            self._cache_mtime = mtime
            self._cache_content = content
            return content

    def inject_into_prompt(self, prompt: str, header: str = "ProjectMemory") -> str:
        """把记忆内容拼到 prompt 顶部. 没有记忆时原样返回 prompt."""
        content = self.load()
        if not content:
            return prompt
        return (
            f"<{header}>\n"
            f"{content.strip()}\n"
            f"</{header}>\n\n"
            f"<Task>\n"
            f"{prompt}\n"
            f"</Task>"
        )

    def info(self) -> Tuple[Optional[str], int]:
        """返回 (路径, 字符数). 给 /admin/status 调."""
        content = self.load()
        return self.loaded_path, len(content)


# 模块级单例 (大多数场景一份就够), 测试可以新建实例隔离
_default_memory: Optional[ProjectMemory] = None
_default_lock = threading.Lock()


def get_project_memory() -> ProjectMemory:
    """模块级共享 ProjectMemory 实例."""
    global _default_memory
    with _default_lock:
        if _default_memory is None:
            _default_memory = ProjectMemory()
        return _default_memory


def reset_project_memory() -> None:
    """主要给测试用 — 清掉单例, 让下次 get 重建."""
    global _default_memory
    with _default_lock:
        _default_memory = None

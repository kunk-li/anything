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
from typing import Dict, List, Optional, Tuple


# 默认查找路径列表 (按优先级)
_DEFAULT_CANDIDATES = [
    "AGENTS.md",
    "CLAUDE.md",
    ".anything/memory.md",
]


# ---------------------------------------------------------------------------
# 工作区根安全校验 (服务器目录选择限制)
#
# 两道闸, 在所有"把某目录当工作区"的服务器消费端统一调用 (shell_exec 的 CWD /
# 目录浏览 / 项目注册 / 本函数的项目记忆加载) —— 因为 active_project_root 是前端
# 可传的任意字符串, 只限制浏览器 UI 是安全表演, 真正的边界必须落在服务器侧。
#
#   ① 防手滑护栏 (始终生效): 盘符根 / 文件系统根 (C:\ 或 /) 不可作工作区
#      —— 按"父目录 == 自身"能力判定, 不是维护系统目录黑名单。
#   ② 配置式 jail (仅当 env ANYTHING_FS_ROOT 已设): 工作区根必须落在该沙箱根内。
#      不设 = 不开 jail (localhost 全盘自由, 零行为变化); 部署到服务器时一设即生效。
#
# 防逃逸: 先 realpath 解析符号链接/junction/.. 再比对, 否则共享内的恶意软链可绕过。
# ---------------------------------------------------------------------------

def get_fs_root() -> Optional[str]:
    """沙箱根 (env ANYTHING_FS_ROOT) 的规范化绝对路径; 未设/无效 -> None = 不开 jail。"""
    raw = (os.environ.get("ANYTHING_FS_ROOT") or "").strip()
    if not raw:
        return None
    try:
        return os.path.realpath(os.path.abspath(os.path.expanduser(raw)))
    except Exception:
        return None


def _is_fs_root(path: str) -> bool:
    """path 是否是盘符根 / 文件系统根 (C:\\ 、D:\\ 、/)。按"父 == 自身"判定, 不枚举。"""
    return os.path.dirname(path) == path


def validate_workspace_root(
    raw: Optional[str], *, for_browse: bool = False
) -> Tuple[Optional[str], Optional[str]]:
    """校验一个"工作区根 / 浏览目标"路径是否可接受。

    返回 (normalized_abspath, reason):
        - (None, None)  : raw 为空 —— 没指定, 调用方回退默认 (不是错误)。
        - (None, reason): 被拒, reason ∈ {'not_a_dir', 'drive_root', 'outside_jail'}。
        - (norm, None)  : 通过, norm 是 realpath 规范化后的绝对路径。

    for_browse=True: 跳过"盘符根"护栏 (浏览盘符根是正常的目录导航), 仅保留存在性 + jail。
    """
    if not raw or not str(raw).strip():
        return None, None
    try:
        norm = os.path.realpath(os.path.abspath(os.path.expanduser(str(raw).strip())))
    except Exception:
        return None, "not_a_dir"
    if not os.path.isdir(norm):
        return None, "not_a_dir"
    if not for_browse and _is_fs_root(norm):
        return None, "drive_root"
    fs_root = get_fs_root()
    if fs_root:
        nc_root = os.path.normcase(fs_root)
        try:
            if os.path.commonpath([os.path.normcase(norm), nc_root]) != nc_root:
                return None, "outside_jail"
        except ValueError:
            # 不同盘符 (Windows) / 无公共前缀 -> 必在 jail 外
            return None, "outside_jail"
    return norm, None


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


# 多项目支持: 按"项目根"加载 ProjectMemory。全局单例只认 CWD/CWD.parent 的一份 AGENTS.md
# (默认作用域, 通常是本平台自身); 当请求带了 active_project_root (用户选的"当前项目")时, 要读
# 那个根下的 AGENTS.md/CLAUDE.md。按 root 缓存 + mtime 热刷新; root 为空 -> 回退全局单例。
_root_memory_cache: Dict[str, Tuple[Path, float, str]] = {}
_root_memory_lock = threading.Lock()
_ROOT_MEMORY_MAX_CHARS = 8000


def load_project_memory_for_root(root: Optional[str]) -> str:
    """读 <root>/{AGENTS.md|CLAUDE.md|.anything/memory.md} 第一个存在的文件内容; 找不到返回 ''。
    root 为空 -> 回退全局单例 get_project_memory().load() (默认作用域)。按 root 缓存 + mtime 热刷新,
    超 8000 字截断。给 Agent 提示词按"当前项目"注入项目记忆用 (见 prompt_builder._build_react_prompt)。"""
    if not root:
        return get_project_memory().load()
    # 越界/盘符根/不存在 -> 不读它的 AGENTS.md (与 shell_exec/浏览器同一道闸)。
    norm, why = validate_workspace_root(root)
    if why is not None:
        return ""
    base = Path(norm)
    key = str(base)
    with _root_memory_lock:
        path: Optional[Path] = None
        for cand in _DEFAULT_CANDIDATES:
            p = base / cand
            try:
                if p.is_file():
                    path = p
                    break
            except OSError:
                continue
        if path is None:
            _root_memory_cache.pop(key, None)
            return ""
        try:
            mtime = path.stat().st_mtime
        except OSError:
            cached = _root_memory_cache.get(key)
            return cached[2] if cached else ""
        cached = _root_memory_cache.get(key)
        if cached and cached[0] == path and cached[1] == mtime:
            return cached[2]
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            cached = _root_memory_cache.get(key)
            return cached[2] if cached else ""
        if len(content) > _ROOT_MEMORY_MAX_CHARS:
            content = content[:_ROOT_MEMORY_MAX_CHARS] + "\n\n... [truncated]"
        _root_memory_cache[key] = (path, mtime, content)
        return content

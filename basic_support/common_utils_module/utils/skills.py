# -*- coding: utf-8 -*-
"""
Skill 系统 (Task AA #61)

Claude Code Skills 模式: skills/ 目录下每个 .md 文件是一个 skill, 自动被
SkillRegistry 加载. Agent / RAG 在构造 prompt 时按 triggers 关键字命中 skill
并把 body 插到 prompt 顶部, 让 LLM 获得领域知识 / 偏好 / 模板.

Skill 文件格式 (YAML frontmatter + markdown body):

    ---
    name: code_review
    description: 代码审查规范
    triggers:
      - 代码审查
      - code review
      - 审一下这段
    tools:
      - rag_search
    priority: 10
    ---

    # 代码审查规范

    审代码时, 按下面这个顺序看:
    1. 安全 (SQL 注入 / XSS / 命令注入)
    2. 错误处理
    3. ...

frontmatter 可选, 没填则用文件名当 name, 全文当 body, triggers 空 (永远不命中).

匹配规则:
    match(query): 把 query 跟所有 skill 的 triggers 比, triggers 是子串就算命中.
                  返回按 priority 降序的 [skill, ...].
                  priority 默认 0; 大的更优先.

prompt 注入:
    inject_skills_into_prompt(prompt, matched_skills, max_skills=3):
        把命中的前 N 个 skill body 拼到 prompt 顶部:
            <Skills>
            # skill 1 name
            ...body...
            ---
            # skill 2 name
            ...
            </Skills>

            <Task>
            ...原 prompt...
            </Task>
"""

from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class Skill:
    name: str
    description: str = ""
    triggers: List[str] = field(default_factory=list)
    tools: List[str] = field(default_factory=list)
    priority: int = 0
    body: str = ""
    path: Optional[str] = None

    def matches(self, query: str) -> bool:
        """query 含任一 trigger 子串就命中. trigger 不区分大小写."""
        if not query or not self.triggers:
            return False
        q = query.lower()
        return any(t.lower() in q for t in self.triggers if t)


def _parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    """简单 YAML frontmatter 解析: 支持 key: value 和 key: [list] 两种.

    没装 PyYAML, 手写最小子集:
      - key: value          → str
      - key: 123            → int
      - key:                → 多行 list, 每行以 '- ' 开头

    没匹配到 frontmatter (文件不是 --- 开头) → 返回 ({}, 全文).
    """
    if not text.startswith("---"):
        return {}, text
    end = text.find("\n---", 3)
    if end == -1:
        return {}, text
    header = text[3:end].strip()
    body = text[end + 4:].lstrip("\n")  # 跳过 '---\n'
    meta: Dict[str, Any] = {}
    current_key: Optional[str] = None
    for line in header.splitlines():
        line = line.rstrip()
        if not line.strip():
            continue
        # list 续行: '  - xxx' 或 '- xxx'
        list_match = re.match(r"^\s*-\s+(.+)$", line)
        if list_match and current_key is not None and isinstance(meta.get(current_key), list):
            meta[current_key].append(list_match.group(1).strip())
            continue
        # key: value 或 key:
        kv = re.match(r"^([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(.*)$", line)
        if not kv:
            continue
        key = kv.group(1)
        val_raw = kv.group(2).strip()
        if val_raw == "":
            # 空值 → 后续行可能是 list
            meta[key] = []
            current_key = key
            continue
        # 标量
        if re.match(r"^-?\d+$", val_raw):
            meta[key] = int(val_raw)
        elif val_raw.startswith("[") and val_raw.endswith("]"):
            inner = val_raw[1:-1]
            items = [s.strip().strip("'\"") for s in inner.split(",") if s.strip()]
            meta[key] = items
        else:
            meta[key] = val_raw.strip("'\"")
        current_key = key
    return meta, body


def parse_skill_file(path: Path) -> Skill:
    """读单个 .md 文件并解析成 Skill."""
    text = path.read_text(encoding="utf-8", errors="replace")
    meta, body = _parse_frontmatter(text)
    return Skill(
        name=str(meta.get("name") or path.stem),
        description=str(meta.get("description") or ""),
        triggers=[str(t) for t in (meta.get("triggers") or [])],
        tools=[str(t) for t in (meta.get("tools") or [])],
        priority=int(meta.get("priority") or 0),
        body=body.strip(),
        path=str(path),
    )


class SkillRegistry:
    """skills/ 目录扫描 + 匹配."""

    def __init__(self, skills_dir: Optional[str] = None):
        # 解析顺序: 显式参数 > env > 默认 'skills' (相对 cwd) > 项目根 'skills/'
        self._skills_dir = skills_dir
        self._skills: List[Skill] = []
        self._loaded_from: Optional[str] = None
        self._lock = threading.Lock()

    def _resolve_dir(self) -> Optional[Path]:
        # 显式路径独占 (同 ProjectMemory): 传了就只看它, 不存在直接返回 None.
        # 让测试可以传一个临时路径强制空场景, 不被项目根真实 skills/ 干扰.
        if self._skills_dir is not None:
            p = Path(self._skills_dir).expanduser()
            return p if p.is_dir() else None
        candidates: List[Path] = []
        env_dir = os.environ.get("ANYTHING_SKILLS_DIR")
        if env_dir:
            candidates.append(Path(env_dir).expanduser())
        candidates.append(Path("skills"))
        # 项目根兜底
        try:
            candidates.append(Path.cwd().parent / "skills")
        except Exception:
            pass
        for p in candidates:
            if p.is_dir():
                return p
        return None

    def load(self) -> int:
        """扫 skills_dir 下所有 .md, 返回加载数."""
        with self._lock:
            self._skills = []
            self._loaded_from = None
            d = self._resolve_dir()
            if d is None:
                return 0
            self._loaded_from = str(d)
            for path in sorted(d.glob("*.md")):
                try:
                    skill = parse_skill_file(path)
                    self._skills.append(skill)
                except Exception:
                    continue
        return len(self._skills)

    def all_skills(self) -> List[Skill]:
        with self._lock:
            return list(self._skills)

    def match(self, query: str) -> List[Skill]:
        """返回命中的 skill, 按 priority 降序."""
        if not query:
            return []
        with self._lock:
            hits = [s for s in self._skills if s.matches(query)]
        hits.sort(key=lambda s: (-s.priority, s.name))
        return hits

    def info(self) -> Dict[str, Any]:
        """给 /admin/status + /skills 端点用."""
        with self._lock:
            return {
                "loaded_from": self._loaded_from,
                "count": len(self._skills),
                "skills": [
                    {
                        "name": s.name,
                        "description": s.description,
                        "triggers": list(s.triggers),
                        "tools": list(s.tools),
                        "priority": s.priority,
                        "body_chars": len(s.body),
                    }
                    for s in self._skills
                ],
            }


def inject_skills_into_prompt(
    prompt: str,
    skills: List[Skill],
    max_skills: int = 3,
    max_body_chars: int = 2000,
) -> str:
    """把命中的 skill body 拼到 prompt 顶部, 最多 N 个 skill."""
    if not skills:
        return prompt
    chosen = skills[: max(1, int(max_skills))]
    blocks: List[str] = []
    for s in chosen:
        body = s.body or ""
        if len(body) > max_body_chars:
            body = body[:max_body_chars] + "\n... [truncated]"
        blocks.append(f"# {s.name}\n{body}")
    return (
        "<Skills>\n"
        + "\n\n---\n\n".join(blocks)
        + "\n</Skills>\n\n"
        + "<Task>\n"
        + prompt
        + "\n</Task>"
    )


# 模块级单例
_default: Optional[SkillRegistry] = None
_default_lock = threading.Lock()


def get_skill_registry() -> SkillRegistry:
    global _default
    with _default_lock:
        if _default is None:
            _default = SkillRegistry()
            _default.load()
        return _default


def reset_skill_registry() -> None:
    global _default
    with _default_lock:
        _default = None

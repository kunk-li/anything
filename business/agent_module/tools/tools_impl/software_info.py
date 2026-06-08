# -*- coding: utf-8 -*-
"""
Tool: software_info — 只读查本机已安装软件的版本信息与使用说明。

为"问'我装的 X 是什么版本 / 怎么用'就直接答"而加: agent 直接读到真实版本 + 软件
自带 --help, 再整理成易读说明, 而非教用户自己去开命令行。**只读、无副作用 →
不进审批白名单** (区别于 shell_exec / py_sandbox / computer_use 这些有副作用的危险工具)。

动作:
  lookup(默认): 给软件名 → 版本 + 用法。
      ① PATH 上的命令 (git/python/node…): which 解析后跑固定的 `--version` / `--help`
         (Windows 还试 `/?`) 抓版本与帮助;
      ② 非 PATH 的 GUI 软件 (Chrome/VSCode…): 退而在 Windows 卸载注册表按名子串匹配,
         给版本 + 安装位置 (无 --help)。
  list: 列本机已安装软件清单 (Windows 卸载注册表为主, 退化 PATH 上的命令); 支持 filter/limit。

安全姿态 (重要): 只跑 **PATH 上已存在** 的程序、**固定的 version/help 参数**、shell=False +
列表传参 (无注入)、软件名强校验 (执行路径只允许 [A-Za-z0-9._+-])、stdin=DEVNULL + 超时 +
输出截断。注册表/PATH 扫描纯读。backend 可注入 (测试不碰真机/真注册表/不起子进程)。
"""
from __future__ import annotations

import os
import re
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

_IS_WINDOWS = sys.platform.startswith("win")

# 执行路径用 — 严格: 必须字母/数字开头, 仅 [A-Za-z0-9._+-], ≤64。挡空格/路径分隔/shell 元字符,
# 防"跑任意路径的二进制"或注入 (covers git / python3.11 / g++ / dotnet / 7z 等真实命令名)。
_EXEC_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,63}$")
# 注册表子串搜索用 — 宽松 (不执行, 仅匹配 DisplayName): 允许空格/括号, 仍挡控制符与危险标点。
_SEARCH_NAME_RE = re.compile(r"^[\w .+()#-]{1,80}$", re.UNICODE)

_VERSION_FLAGS = ("--version", "-V")
_HELP_FLAGS = ("--help", "-h", "/?") if _IS_WINDOWS else ("--help", "-h")
_RUN_TIMEOUT = 5            # 每次子进程墙钟上限 (秒)
_HELP_CAP = 4000           # 帮助输出截断
_VERSION_CAP = 400         # 版本输出截断
_LIST_LIMIT_DEFAULT = 60


class _RealBackend:
    """默认后端: 真 which / subprocess / winreg / PATH 扫描。测试可注入 fake 顶替。"""

    def which(self, name: str) -> Optional[str]:
        """解析 PATH 上的可执行文件。Windows 上 `shutil.which` 常先命中 Microsoft Store 的
        **App Execution Alias** —— 0 字节 stub, 非交互跑返 9009 空输出 (如 `python`)。这里检出
        0 字节 stub 时, 在 PATH 其余位置找真正可执行的同名程序; 找不到才退回 stub (至少路径可见)。"""
        import shutil
        p = shutil.which(name)
        if p and _IS_WINDOWS and self._is_zero_byte(p):
            alt = self._which_skip(name, skip=p)
            return alt or p
        return p

    @staticmethod
    def _is_zero_byte(path: str) -> bool:
        try:
            return os.path.isfile(path) and os.path.getsize(path) == 0
        except OSError:
            return False

    def _which_skip(self, name: str, skip: str) -> Optional[str]:
        """在 PATH 上找 name 的可执行文件, 跳过 skip 及 0 字节 stub。返回首个真实命中或 None。"""
        exts = [e for e in os.environ.get("PATHEXT", ".EXE;.BAT;.CMD").split(";") if e] or [""]
        skip_nc = os.path.normcase(os.path.abspath(skip))
        has_ext = os.path.splitext(name)[1] != ""
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if not d or not os.path.isdir(d):
                continue
            for ext in ([""] if has_ext else exts):
                cand = os.path.join(d, name + ext)
                if not os.path.isfile(cand):
                    continue
                if os.path.normcase(os.path.abspath(cand)) == skip_nc or self._is_zero_byte(cand):
                    continue
                return cand
        return None

    def run(self, argv: List[str], timeout: int = _RUN_TIMEOUT) -> Tuple[Optional[int], str]:
        """跑 argv (shell=False, 无 stdin, 合并 stdout/stderr)。返回 (returncode, text);
        超时/异常 returncode=None。文本按 utf-8 宽松解码 (版本号 ASCII; 本地化帮助可能花)。"""
        import subprocess
        try:
            p = subprocess.run(
                argv, stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                timeout=timeout, shell=False,
            )
            return p.returncode, (p.stdout or b"").decode("utf-8", errors="replace")
        except subprocess.TimeoutExpired:
            return None, "(超时)"
        except Exception as e:
            return None, f"(执行失败: {str(e)[:120]})"

    def uninstall_entries(self) -> List[Dict[str, Any]]:
        """Windows 卸载注册表 → 已安装软件列表 (DisplayName/Version/Publisher/Location)。
        非 Windows / 读失败 → []。跳过无 DisplayName 及 SystemComponent=1 的系统组件。"""
        if not _IS_WINDOWS:
            return []
        import winreg
        roots = [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        ]
        out: List[Dict[str, Any]] = []
        seen = set()
        for hive, path in roots:
            try:
                key = winreg.OpenKey(hive, path)
            except OSError:
                continue
            try:
                count = winreg.QueryInfoKey(key)[0]
            except OSError:
                count = 0
            for i in range(count):
                try:
                    sk = winreg.OpenKey(key, winreg.EnumKey(key, i))
                except OSError:
                    continue

                def _get(vname):
                    try:
                        return winreg.QueryValueEx(sk, vname)[0]
                    except OSError:
                        return None

                dn = _get("DisplayName")
                if not dn or _get("SystemComponent") == 1:
                    continue
                ver = _get("DisplayVersion")
                dedup = (str(dn), str(ver))
                if dedup in seen:
                    continue
                seen.add(dedup)
                out.append({
                    "name": str(dn),
                    "version": str(ver) if ver not in (None, "") else None,
                    "publisher": (str(_get("Publisher")) or None) or None,
                    "location": (str(_get("InstallLocation")) or None) or None,
                })
        return out

    def path_commands(self, limit: int = 500) -> List[Dict[str, Any]]:
        """扫 PATH 目录下的可执行命令 (Windows 按 PATHEXT, POSIX 按可执行位)。按名去重, 限量。"""
        exts = None
        if _IS_WINDOWS:
            exts = {e.lower() for e in os.environ.get("PATHEXT", ".EXE;.BAT;.CMD").split(";") if e}
        out: List[Dict[str, Any]] = []
        seen = set()
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if not d or not os.path.isdir(d):
                continue
            try:
                names = os.listdir(d)
            except OSError:
                continue
            for fn in names:
                full = os.path.join(d, fn)
                if not os.path.isfile(full):
                    continue
                if _IS_WINDOWS:
                    stem, ext = os.path.splitext(fn)
                    if ext.lower() not in exts:
                        continue
                    name = stem
                else:
                    if not os.access(full, os.X_OK):
                        continue
                    name = fn
                low = name.lower()
                if low in seen:
                    continue
                seen.add(low)
                out.append({"name": name, "path": full})
                if len(out) >= limit:
                    return out
        return out


def _ok(data: Dict[str, Any], trace_id) -> Dict[str, Any]:
    return {"code": "SUCCESS", "message": "ok", "data": data,
            "trace_id": trace_id, "retryable": False, "details": None}


def _err(code: str, message: str, trace_id, retryable: bool = False) -> Dict[str, Any]:
    return {"code": code, "message": message, "data": None,
            "trace_id": trace_id, "retryable": retryable, "details": None}


def _first_useful(be, argv_base: List[str], flags) -> Optional[Dict[str, Any]]:
    """依次试 flags, 取第一个 returncode==0 且有输出的; 全不成则取第一个有任意输出的。
    返回 {flag, output, returncode} 或 None (全空)。"""
    fallback = None
    for flag in flags:
        rc, text = be.run(argv_base + [flag])
        text = (text or "").strip()
        if not text:
            continue
        if rc == 0:
            return {"flag": flag, "output": text, "returncode": rc}
        if fallback is None:
            fallback = {"flag": flag, "output": text, "returncode": rc}
    return fallback


def make_software_info_tool(backend: Optional[Any] = None) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """构造 software_info 工具。backend 缺省真后端; 测试可注入 fake (which/run/uninstall/path)。"""

    def software_info(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = payload or {}
        trace_id = payload.get("trace_id")
        action = str(payload.get("action") or "lookup").strip().lower()
        be = backend if backend is not None else _RealBackend()

        if action == "list":
            return _list(be, payload, trace_id)
        if action == "lookup":
            return _lookup(be, payload, trace_id)
        return _err("PARAM_INVALID", f"action 必须是 lookup/list; 收到 {action!r}", trace_id)

    def _lookup(be, payload, trace_id) -> Dict[str, Any]:
        name = str(payload.get("name") or payload.get("software") or "").strip()
        if not name:
            return _err("PARAM_MISSING", "lookup 需要 name (软件名, 如 'git' / 'Chrome')", trace_id)

        data: Dict[str, Any] = {"query": name, "found": False, "source": None}
        weak_path: Optional[Dict[str, Any]] = None  # 在 PATH 但读不到版本/用法 (如 Store stub) 的弱命中

        # ① PATH 命令: 严格校验名 → which 解析 → 跑 --version / --help (固定参数)
        if _EXEC_NAME_RE.match(name):
            try:
                resolved = be.which(name)
            except Exception:
                resolved = None
            if resolved:
                argv = [resolved]
                ver = _first_useful(be, argv, _VERSION_FLAGS)
                helpinfo = _first_useful(be, argv, _HELP_FLAGS)
                path_data = {
                    "found": True, "source": "path_command",
                    "name": name, "path": resolved,
                    "version": (ver["output"][:_VERSION_CAP] if ver else None),
                    "version_flag": (ver["flag"] if ver else None),
                    "usage": (helpinfo["output"][:_HELP_CAP] if helpinfo else None),
                    "usage_flag": (helpinfo["flag"] if helpinfo else None),
                    "usage_truncated": bool(helpinfo and len(helpinfo["output"]) > _HELP_CAP),
                }
                if ver or helpinfo:
                    data.update(path_data)
                    return _ok(data, trace_id)
                # 读不到任何版本/用法 (Store stub / 怪程序): 先存为弱命中, 试注册表能否给更好的
                path_data["note"] = "在 PATH 找到该命令, 但 --version/--help 无输出 (可能是壳/Store 别名)。"
                weak_path = path_data

        # ② 非 PATH 或上面读不到版本 (多为 GUI 软件): Windows 卸载注册表按名子串匹配 → 版本 + 安装位置
        if _SEARCH_NAME_RE.match(name):
            try:
                entries = be.uninstall_entries()
            except Exception:
                entries = []
            low = name.lower()
            matches = [e for e in entries if low in str(e.get("name", "")).lower()]
            if matches:
                matches.sort(key=lambda e: len(str(e.get("name", ""))))  # 名更短的更像精确命中
                data.update({
                    "found": True, "source": "registry",
                    "matches": matches[:10],
                    "note": "来自已安装软件注册表 (版本 + 安装位置; 无命令行 --help)。",
                })
                if weak_path:  # 它也在 PATH, 附带路径供参考
                    data["path"] = weak_path["path"]
                return _ok(data, trace_id)

        if weak_path:  # 注册表也没更好的 → 至少返回"在 PATH、版本未知"
            return _ok({**data, **weak_path}, trace_id)

        data["note"] = (f"未找到 {name!r}: 不在 PATH 命令中"
                        + ("" if _IS_WINDOWS else " (非 Windows 无注册表回退)")
                        + "; 也未在已安装软件注册表里按名匹配到。可试 action=list 看清单, 或换个名字。")
        return _ok(data, trace_id)

    def _list(be, payload, trace_id) -> Dict[str, Any]:
        try:
            limit = max(1, min(int(payload.get("limit", _LIST_LIMIT_DEFAULT)), 500))
        except (TypeError, ValueError):
            limit = _LIST_LIMIT_DEFAULT
        filt = str(payload.get("filter") or "").strip().lower()

        try:
            entries = be.uninstall_entries()
        except Exception:
            entries = []
        source = "registry"
        if not entries:
            # 非 Windows / 注册表空 → 退化列 PATH 上的命令
            try:
                entries = be.path_commands(limit=500)
            except Exception:
                entries = []
            source = "path_commands"

        if filt:
            entries = [e for e in entries if filt in str(e.get("name", "")).lower()]
        total = len(entries)
        entries = sorted(entries, key=lambda e: str(e.get("name", "")).lower())[:limit]
        return _ok({
            "source": source, "total": total, "returned": len(entries),
            "truncated": total > len(entries), "filter": filt or None,
            "software": entries,
        }, trace_id)

    return software_info


SOFTWARE_INFO_DESCRIPTION = (
    '只读查本机已安装软件的版本与使用说明 (用于直接答"我装的 X 是什么版本 / 怎么用", '
    '而非教用户自己开命令行)。'
    'input: {"action": "lookup"(默认)|"list", '
    '"name": str(lookup 必填, 软件名如 "git"/"python"/"Chrome"), '
    '"filter": str?(list 按名子串过滤), "limit": int?(list 上限, 默认 60)}。'
    'lookup: PATH 命令跑固定 --version/--help 抓版本与帮助; GUI 软件退到卸载注册表给版本+安装位置。'
    'list: 列已安装软件清单 (Windows 注册表为主, 退化 PATH 命令)。'
    '只跑 PATH 已存在程序+固定参数, 只读无副作用、无需审批。'
)

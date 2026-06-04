# -*- coding: utf-8 -*-
"""
Tool: system_info — 只读查看本机运行状态 (CPU/内存/磁盘/网络/进程)。

为"你来直接查看电脑使用情况然后分析"而加: agent 直接读到真实指标再分析, 而不是
教用户去开任务管理器。**只读、无副作用 → 不进审批白名单** (区别于 computer_use /
shell_exec / py_sandbox 这些有副作用的危险工具)。

可选依赖 psutil (lazy import; 未装 → MISSING_DEPS 优雅降级, 不拖垮启动)。
backend 可注入 (测试不碰真机)。

动作: overview(默认, 综合快照) / cpu / memory / disk / network / processes。
"""
from __future__ import annotations

import platform
import time
from typing import Any, Callable, Dict, List, Optional

_VALID_ACTIONS = ("overview", "cpu", "memory", "disk", "network", "processes")


class _PsutilBackend:
    """默认后端: lazy psutil。首次用时 import; 未装 → ImportError (被工具捕获降级)。"""

    def __init__(self):
        self._ps = None

    def _ps_(self):
        if self._ps is None:
            import psutil  # lazy; 未装抛 ImportError
            self._ps = psutil
        return self._ps

    def cpu(self) -> Dict[str, Any]:
        ps = self._ps_()
        return {
            "percent": ps.cpu_percent(interval=0.3),
            "per_cpu_percent": ps.cpu_percent(interval=0.0, percpu=True),
            "logical_cores": ps.cpu_count(logical=True),
            "physical_cores": ps.cpu_count(logical=False),
        }

    def memory(self) -> Dict[str, Any]:
        ps = self._ps_()
        vm = ps.virtual_memory()
        sw = ps.swap_memory()
        return {
            "total_gb": round(vm.total / 1024 ** 3, 2),
            "used_gb": round(vm.used / 1024 ** 3, 2),
            "available_gb": round(vm.available / 1024 ** 3, 2),
            "percent": vm.percent,
            "swap_total_gb": round(sw.total / 1024 ** 3, 2),
            "swap_percent": sw.percent,
        }

    def disk(self) -> List[Dict[str, Any]]:
        ps = self._ps_()
        out: List[Dict[str, Any]] = []
        for p in ps.disk_partitions(all=False):
            try:
                u = ps.disk_usage(p.mountpoint)
            except Exception:
                continue
            out.append({
                "device": p.device, "mountpoint": p.mountpoint, "fstype": p.fstype,
                "total_gb": round(u.total / 1024 ** 3, 2),
                "used_gb": round(u.used / 1024 ** 3, 2),
                "percent": u.percent,
            })
        return out

    def network(self) -> Dict[str, Any]:
        ps = self._ps_()
        io = ps.net_io_counters()
        return {
            "bytes_sent_mb": round(io.bytes_sent / 1024 ** 2, 2),
            "bytes_recv_mb": round(io.bytes_recv / 1024 ** 2, 2),
            "packets_sent": io.packets_sent,
            "packets_recv": io.packets_recv,
        }

    def processes(self, top: int = 8) -> List[Dict[str, Any]]:
        ps = self._ps_()
        procs: List[Dict[str, Any]] = []
        for p in ps.process_iter(["pid", "name", "cpu_percent", "memory_percent"]):
            try:
                info = p.info
                procs.append({
                    "pid": info.get("pid"),
                    "name": info.get("name"),
                    "cpu_percent": info.get("cpu_percent"),
                    "memory_percent": round(info.get("memory_percent") or 0.0, 2),
                })
            except Exception:
                continue
        # cpu_percent 首次读多为 0 (psutil 需两次采样), 按 memory_percent 排序更可靠
        procs.sort(key=lambda x: (x.get("memory_percent") or 0.0), reverse=True)
        return procs[: max(1, int(top))]

    def boot_time(self) -> float:
        return self._ps_().boot_time()


def _ok(data: Dict[str, Any], trace_id) -> Dict[str, Any]:
    return {"code": "SUCCESS", "message": "ok", "data": data,
            "trace_id": trace_id, "retryable": False, "details": None}


def _err(code: str, message: str, trace_id, retryable: bool = False) -> Dict[str, Any]:
    return {"code": code, "message": message, "data": None,
            "trace_id": trace_id, "retryable": retryable, "details": None}


def make_system_info_tool(backend: Optional[Any] = None) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """构造 system_info 工具。backend 缺省 psutil (lazy); 测试可注入 fake backend。"""

    def system_info(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = payload or {}
        trace_id = payload.get("trace_id")
        action = str(payload.get("action") or "overview").strip().lower()
        if action not in _VALID_ACTIONS:
            return _err("PARAM_INVALID",
                        f"action 必须是 {'/'.join(_VALID_ACTIONS)}; 收到 {action!r}", trace_id)
        be = backend if backend is not None else _PsutilBackend()
        try:
            if action == "cpu":
                return _ok(be.cpu(), trace_id)
            if action == "memory":
                return _ok(be.memory(), trace_id)
            if action == "disk":
                return _ok({"partitions": be.disk()}, trace_id)
            if action == "network":
                return _ok(be.network(), trace_id)
            if action == "processes":
                return _ok({"top_processes": be.processes(top=int(payload.get("top", 8)))}, trace_id)
            # overview: 综合快照 (一次拿全)
            data: Dict[str, Any] = {
                "platform": f"{platform.system()} {platform.release()}",
                "cpu": be.cpu(),
                "memory": be.memory(),
                "disk": be.disk(),
                "network": be.network(),
                "top_processes": be.processes(top=5),
            }
            try:
                data["uptime_hours"] = round((time.time() - be.boot_time()) / 3600, 1)
            except Exception:
                pass
            return _ok(data, trace_id)
        except ImportError:
            return _err("MISSING_DEPS", "psutil 未装. 跑: pip install psutil", trace_id)
        except Exception as e:
            return _err("TOOL_CALL_FAILED", f"读系统状态失败: {str(e)[:200]}", trace_id, retryable=True)

    return system_info


SYSTEM_INFO_DESCRIPTION = (
    '只读查看本机运行状态 (用于"直接看电脑使用情况并分析", 而非教用户开任务管理器): '
    'CPU/内存/磁盘/网络/进程占用。'
    'input: {"action": "overview"(默认,综合)|"cpu"|"memory"|"disk"|"network"|"processes", '
    '"top": int?(processes 取占用前 N)}。'
    'overview 一次返回 平台/CPU%/内存/磁盘/网络/占用最高的进程。只读无副作用、无需审批。'
    '需 pip install psutil。'
)

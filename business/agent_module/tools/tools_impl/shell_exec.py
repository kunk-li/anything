# -*- coding: utf-8 -*-
"""
Tool: shell_exec — 通用终端执行, 逐条命令风险分级 (通用执行内核)。

理念 (用户提出): 与其每缺一个能力就造一个专用工具 (mongo_query / mysql_query / …一万个),
不如给 agent 一个真终端, 让它自己写命令查/做任何事。安全不靠"禁止执行", 而靠
**对每条命令逐条风险分级** —— 只读自动放行, 危险/看不透的才要人确认一下。

风险分级 (完备: 每条命令必落一档):
  - safe   : 形态明确的只读/查询 → 直接执行
             (dir/ls/cat/type/findstr/grep/find[无 -exec]/--version/ps/df/whoami/echo/
              mongosh 查询/SELECT/show/pip list/git status…)
  - danger : 明确的破坏/高危 → 默认拦, 需确认
             (rm -rf / del /s / Remove-Item -Recurse / drop / truncate / format / mkfs /
              dd / shutdown / kill -9 / chmod -R / 覆盖写系统目录 / fork bomb / mongo drop·delete…)
  - opaque : 会开通用解释器或能藏任意逻辑 → 看不透, 默认拦, 需确认
             (python -c / bash -c / eval / base64 -d | sh / 管道接 sh / 反引号 / $() / iex…)

danger / opaque 默认返回 TOOL_APPROVAL_REQUIRED; extra_params.approve_tools 含 'shell_exec'
或 '*' 时放行该次执行。

⚠️ 诚实边界: 命令文本判断挡住绝大多数, 但单条命令内部是图灵完备的, 不可能 100% 防绕过 ——
   所以"看不透的"一律归 opaque 让人确认 (这是判断的*结论*, 不是漏判)。要数学意义的强隔离,
   得靠 OS 层 (只读挂载 / 受限账户 / 容器), 本地个人用通常不必走到那步。

安全姿态: subprocess + 超时 + 输出截断 + 审计日志 (每条命令留痕, 无论放行/拦截)。
backend 可注入 (测试不起子进程)。
"""
from __future__ import annotations

import re
import shlex
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple

_IS_WINDOWS = sys.platform.startswith("win")
_RUN_TIMEOUT_DEFAULT = 30
_RUN_TIMEOUT_MAX = 120
_OUTPUT_CAP = 20000

# ---- danger: 明确破坏性 (优先级最高, 最该拦) ----
_DANGER_PATTERNS: List[Tuple[str, str]] = [
    (r"\brm\s+(?:-\w+\s+)*-\w*[rf]", "递归/强制删除 (rm -rf)"),
    (r"\bdel\s+.*[/\\][sfqa]", "Windows del 批量删除"),
    (r"\brmdir\s+.*[/\\]s", "rmdir 递归删除"),
    (r"Remove-Item\b.*-Recurse", "PowerShell 递归删除"),
    (r"\bdrop\s+(database|table|collection|index|schema)\b", "DROP 删库/表/集合"),
    (r"\bdropDatabase\b|\.drop\s*\(|\.deleteMany\s*\(|\.deleteOne\s*\(|\.remove\s*\(", "数据库删除操作"),
    (r"\btruncate\b", "TRUNCATE 清空"),
    (r"\b(mkfs|format)\b", "格式化磁盘"),
    (r"\bdd\s+if=", "dd 块写入"),
    (r"\b(shutdown|reboot|poweroff|halt|Restart-Computer|Stop-Computer)\b", "关机/重启"),
    (r"\bkill\s+-9\b|\bkillall\b|Stop-Process\b.*-Force", "强杀进程"),
    (r"\bchmod\s+-R\b|\bchown\s+-R\b", "递归改权限/属主"),
    (r">\s*/(etc|usr|bin|boot|sys|dev|lib|sbin)\b", "覆盖写系统目录"),
    (r":\s*\(\s*\)\s*\{.*\}\s*;", "fork bomb"),
    (r"\bgit\s+.*(reset\s+--hard|clean\s+-\w*[fdx]|push\b.*--force)", "git 破坏性操作"),
    (r"\b(mv|move)\b.*\s+/(etc|usr|bin|boot|sys|sbin)\b", "移动到/覆盖系统目录"),
]

# ---- opaque: 会开通用解释器或能藏任意逻辑 (看不透 → 归确认) ----
# 注意: 不把 DB 客户端的 --eval/-e (mongosh/mysql) 归 opaque, 它们的破坏性内容由 danger 兜;
#       只读 DB 查询 (find/show/aggregate) 应自动放行。
_OPAQUE_PATTERNS: List[Tuple[str, str]] = [
    (r"\b(?:python|python3|py)\s+-[ce]\b", "python 内联代码 (-c/-e)"),
    (r"\b(?:node|deno)\s+-e\b", "node 内联代码"),
    (r"\b(?:perl|ruby|php)\s+-[eE]\b", "脚本语言内联代码"),
    (r"\b(?:bash|sh|zsh|dash)\s+-c\b", "shell -c 内联"),
    (r"(?:^|[\s;&|])eval\s", "eval 动态执行"),
    (r"\b(?:iex|Invoke-Expression)\b", "PowerShell 动态执行"),
    (r"base64\s+(?:-\w+\s+)*-d", "base64 解码 (常用于绕过检测)"),
    (r"\|\s*(?:ba)?sh\b|\|\s*python\b|\|\s*powershell\b", "管道接解释器执行"),
    (r"`[^`]+`", "反引号命令替换"),
    (r"\$\([^)]+\)", "$() 命令替换"),
    (r"\b(?:curl|wget|Invoke-WebRequest|iwr)\b.*\|", "下载后管道执行"),
]


def classify_command(cmd: str) -> Tuple[str, str]:
    """对一条命令分级: 返回 (level, reason), level ∈ {safe, danger, opaque}.

    danger 优先于 opaque (破坏性最该先拦)。判不出危险/不透明 → safe。
    """
    s = (cmd or "").strip()
    if not s:
        return "safe", "空命令"
    for pat, why in _DANGER_PATTERNS:
        if re.search(pat, s, re.IGNORECASE):
            return "danger", why
    for pat, why in _OPAQUE_PATTERNS:
        if re.search(pat, s, re.IGNORECASE):
            return "opaque", why
    return "safe", "未匹配破坏性/不透明模式 (按只读放行)"


# 真正需要 shell 的"独立操作符 token" (shlex 拆分后作为单独元素出现才算;
# 引号内的 > < | 会被 shlex 保留在某个 token 内部, 不会单独成 token, 故不误判)。
_SHELL_OP_TOKENS = {"|", "||", "&", "&&", ";", ">", ">>", "<", "<<", "2>", "2>>", "&>", "|&"}


def _plan_exec(command: str):
    """决定怎么执行一条命令字符串, 返回 (argv, use_shell)。

    先 shlex 拆分 (正确处理引号), 再判断是否真需要 shell:
    - argv 里出现*独立的* shell 操作符 token, 或有 `…`/$(…) 命令替换 → (None, True) 经 shell;
    - 否则 → (argv, False): shell=False 直接喂程序。

    关键: mongosh --eval "…d => printjson(d)…" 里的 => / > / < / | 都在引号内, shlex 会把整段
    保留在单个 token 内, 不会单独成为操作符 token → 走 argv, 不经 shell。
    (旧版在原始串上正则匹配 '>', 会把箭头函数 => 误判成重定向 → 既跑坏命令、又在 cwd 创建垃圾文件。)
    shlex 拆不动 (引号不配对) → 回退经 shell。
    """
    try:
        argv = shlex.split(command, posix=True)
    except ValueError:
        return None, True
    if not argv:
        return None, True
    if any(tok in _SHELL_OP_TOKENS for tok in argv):
        return None, True
    if "`" in command or "$(" in command:
        return None, True
    return argv, False


class _RealShell:
    """默认后端: 真 subprocess 跑命令。无 shell 元字符 → argv+shell=False (引号可靠,
    绕开 Windows shell 嵌套引号坑); 含管道/重定向才 shell=True。测试可注入 fake 顶替。"""

    def run(self, command: str, timeout: int) -> Tuple[Optional[int], str]:
        import subprocess
        kw = dict(stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                  stderr=subprocess.STDOUT, timeout=timeout, text=True, errors="replace")
        try:
            if _IS_WINDOWS:
                # Windows 一律经 cmd.exe (shell=True): cmd 原生处理反斜杠路径 (D:\a\b 不被吞)、
                # dir/type/findstr/where 等内建+自带命令, 以及 "..." 引号传参 (实测 mongosh
                # --eval "d => printjson(d)" 的箭头与引号也正确)。**不能**走下面 shlex argv:
                # posix shlex 会把路径反斜杠当转义吃掉 (D:\projects→D:projects), 且若 PATH 含
                # Git/usr/bin 还会把 dir/find 解析到 MinGW coreutils (跑 GNU 版 → Windows
                # 参数 /s /b 全被当路径报错)。命令级风险分级在工具外层已做, 此处只管执行。
                p = subprocess.run(command, shell=True, **kw)
                return p.returncode, (p.stdout or "")
            argv, use_shell = _plan_exec(command)
            if use_shell:
                p = subprocess.run(command, shell=True, **kw)
            else:
                try:
                    p = subprocess.run(argv, shell=False, **kw)
                except FileNotFoundError:
                    # argv[0] 不在 PATH (shell 内建 / .cmd 包装等) → 回退经 shell 找
                    p = subprocess.run(command, shell=True, **kw)
            return p.returncode, (p.stdout or "")
        except subprocess.TimeoutExpired:
            return None, f"(超时 >{timeout}s, 已中止)"
        except Exception as e:
            return None, f"(执行失败: {str(e)[:200]})"


def _ok(data: Dict[str, Any], trace_id) -> Dict[str, Any]:
    return {"code": "SUCCESS", "message": "ok", "data": data,
            "trace_id": trace_id, "retryable": False, "details": None}


def _err(code: str, message: str, trace_id, retryable: bool = False) -> Dict[str, Any]:
    return {"code": code, "message": message, "data": None,
            "trace_id": trace_id, "retryable": retryable, "details": None}


def make_shell_exec_tool(
    backend: Optional[Any] = None, logger: Optional[Any] = None,
) -> Callable[[Dict[str, Any]], Dict[str, Any]]:
    """构造 shell_exec 工具。backend 缺省真 subprocess; logger 用于审计 (每条命令留痕)。"""

    def _audit(cmd: str, level: str, reason: str, executed: bool, trace_id) -> None:
        if logger is None:
            return
        try:
            logger.info(
                f"[shell_exec][audit] executed={executed} risk={level} ({reason}) "
                f"trace={trace_id} cmd={cmd[:300]}",
                logger_name="agent_module",
            )
        except Exception:
            pass

    def shell_exec(payload: Dict[str, Any]) -> Dict[str, Any]:
        payload = payload or {}
        trace_id = payload.get("trace_id")
        cmd = str(payload.get("command") or payload.get("cmd") or "").strip()
        if not cmd:
            return _err("PARAM_MISSING", "command 不能为空 (要执行的终端命令)", trace_id)

        try:
            timeout = int(payload.get("timeout_seconds", _RUN_TIMEOUT_DEFAULT) or _RUN_TIMEOUT_DEFAULT)
        except (TypeError, ValueError):
            timeout = _RUN_TIMEOUT_DEFAULT
        timeout = max(1, min(timeout, _RUN_TIMEOUT_MAX))

        level, reason = classify_command(cmd)

        extra = payload.get("extra_params") or {}
        approved = extra.get("approve_tools") or []
        if isinstance(approved, str):
            approved = [approved]
        approved_ok = "*" in approved or "shell_exec" in approved

        if level in ("danger", "opaque") and not approved_ok:
            _audit(cmd, level, reason, executed=False, trace_id=trace_id)
            return {
                "code": "TOOL_APPROVAL_REQUIRED",
                "message": (
                    f"命令判为「{level}」({reason}), 需你确认后才执行。"
                    f"确认请带 extra_params.approve_tools=['shell_exec'] 重发。"
                ),
                "data": {"command": cmd, "risk": level, "reason": reason},
                "trace_id": trace_id, "retryable": False, "details": None,
            }

        be = backend if backend is not None else _RealShell()
        rc, out = be.run(cmd, timeout)
        _audit(cmd, level, reason, executed=True, trace_id=trace_id)
        truncated = len(out) > _OUTPUT_CAP
        return _ok({
            "command": cmd, "risk": level, "risk_reason": reason,
            "returncode": rc,
            "output": out[:_OUTPUT_CAP],
            "output_truncated": truncated,
        }, trace_id)

    return shell_exec


# 运行期 OS 提示拼进描述: agent 不会自己知道在哪个系统, 不提示就会乱发命令 (实测在 Windows 上发
# unix 的 `find -name -exec`/`ls`/`grep`/`cat` → cmd 下不存在或语义不同 → 空转好几轮)。
_OS_HINT = (
    '⚠️ 本机是 **Windows**, 命令经 cmd.exe 执行 —— 必须用 Windows 命令: '
    '递归列/找文件 `dir /s /b <目录或通配>`、看文件内容 `type <文件>`、'
    '全文搜索 `findstr /s /n /c:"关键字" <目录>\\*.py`、定位程序 `where`。'
    '**不要**用 ls/cat/find/grep/`find -name -exec` 等 Unix 命令 (cmd 下不存在或行为不同)。 '
) if _IS_WINDOWS else (
    '本机是类 Unix 系统, 用 ls/cat/grep/find 等命令。 '
)

SHELL_EXEC_DESCRIPTION = (
    _OS_HINT +
    '通用终端执行: 直接在本机跑 shell 命令, 用来查询/操作系统、数据库、文件、进程等 —— '
    '需要什么信息就自己写命令拿, 不要因为"没有专门的工具"就说做不了。'
    'input: {"command": str (完整命令行), "timeout_seconds": int? (默认 30, 上限 120)}。'
    '逐条命令自动风险分级: 只读/查询类 (dir/ls/cat/--version/mongosh 查询/SELECT/show) 直接执行; '
    '删除/drop/格式化等破坏性, 或 python -c/eval/管道接 sh 等"能藏任意逻辑"的命令会要求确认 '
    "(带 extra_params.approve_tools=['shell_exec'] 放行)。"
    '例: 列 MongoDB 数据库 → command=`mongosh --quiet --eval "db.adminCommand({listDatabases:1})"`; '
    '看已装软件 → command=`wmic product get name,version` (Windows) 或 `dpkg -l` (Linux)。'
    '读/审查本项目自身的源码也用我 (只读, 自动放行): 看某个文件内容 → command=`type <文件绝对路径>` '
    '(Windows; Linux 用 `cat`); 在整个项目里找某函数/类的定义或用法 → '
    'command=`findstr /s /n "def 函数名" <项目根>\\*.py` (Windows) 或 `grep -rn "def 函数名" <项目根>`。'
    '注意服务进程工作目录在 run\\ 下, 读项目根的文件要用绝对路径或 ..\\ 上溯。'
    ' ⚠️ 查某个库的集合/数据时**必须指定目标库**: mongosh 不指定库默认连 test, 会查到空! '
    '用连接串 `mongosh "mongodb://localhost:27017/<库名>" --quiet --eval "..."` 或在 eval 里 '
    '`db.getSiblingDB("<库名>").<集合>.find().toArray()`。'
    '例: 查 inspection_system 的 template_infos → '
    'command=`mongosh "mongodb://localhost:27017/inspection_system" --quiet --eval "db.template_infos.find().toArray()"`。'
)

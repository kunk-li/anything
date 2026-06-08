# -*- coding: utf-8 -*-
"""Agent 评测台 (eval harness) — 跑一组代表性任务, 量成功率 + 抓回归 / 非确定性。

走真实 WS 流式路径 (/invoke/stream, 用户实际路径) 打真实 LLM, 故 慢 + 依赖运行中的服务 /
网络 / LLM。这不是单元测试: 单测验证代码逻辑, 本台验证"agent 在真实 LLM 下还能不能把代表性
任务做对"——专门抓那些只在真模型/真流式下才暴露、且非确定性的回归 (空答案 / 报错 / 拒绝 /
算错 / 并行工具空转 等本轮踩过的坑)。

用法 (服务需在跑):
    python scripts/eval_agent.py                  # 每任务 1 次
    python scripts/eval_agent.py --runs 3         # 每任务 3 次 (量非确定性, 看成功率)
    python scripts/eval_agent.py --url ws://127.0.0.1:18877 --key dev_api_key_1_change_in_prod

退出码: 全过 0, 有失败 1 (便于接 CI / 发版前 gate)。
任务集在下面 TASKS, 直接加条目即可扩。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import re
import time

try:
    import websockets
except ImportError:  # pragma: no cover
    raise SystemExit("评测台需要 websockets: pip install websockets")


# ── 任务集: 代表性 + 覆盖本轮踩过的坑 ───────────────────────────────
# assert 字段 (全部满足才算这次通过):
#   no_error      : end 必须是 done:SUCCESS (非 error / AGENT_RUN_FAILED / 超时)
#   nonempty      : 答案非空白 (抓"显示不全/空白答案")
#   min_len       : 答案至少这么多字 (抓截断 / 敷衍)
#   contains      : 答案须含 list 中任一子串 (抓算错 / 工具没真跑)
#   not_refused   : 答案不能出现拒绝措辞 (抓"不能操作 / 我做不到")
TASKS = [
    {"name": "算数-完整", "task": "1234 乘以 5678 等于多少",
     "assert": {"no_error": True, "contains": ["7006652", "7,006,652"]}},
    {"name": "多工具-并行", "task": "现在几点了? 顺便算一下 100 乘以 200 等于几",
     "assert": {"no_error": True, "contains": ["20000", "20,000"]}},
    {"name": "本机状态", "task": "看看我这台电脑的内存大概用了多少",
     "assert": {"no_error": True, "nonempty": True, "not_refused": True}},
    {"name": "规划-直接答", "task": "帮我规划一下个人项目的文档目录该怎么分类",
     "assert": {"no_error": True, "min_len": 80, "not_refused": True}},
    {"name": "实时-时间", "task": "现在北京时间几点了",
     "assert": {"no_error": True, "contains": ["点", ":", "时"]}},
    {"name": "调试-方法论", "task": "我的程序老报一个错, 给我一套系统的排查思路",
     "assert": {"no_error": True, "min_len": 80, "not_refused": True}},
    {"name": "问答-直接答", "task": "用一句话解释什么是 RAG",
     "assert": {"no_error": True, "nonempty": True}},
    {"name": "软件版本", "task": "我电脑上装的 git 是什么版本",
     "assert": {"no_error": True, "not_refused": True, "contains": ["版本", "version", "git"]}},
]

# 注: 拒绝词须带"指向用户/本机"的语境, 否则会误判正常排查建议为拒绝 (over-broad):
#   - "无法直接" 须接 访问/操作/帮/为你/替你 (否则误判 "无法直接复现/定位/获取")
#   - "无法访问" 须接 你/您/本机/本地 (否则误判 "程序无法访问数据库/网络/资源" 这类技术建议)
# 真拒绝 "我无法访问你的电脑" 仍被 "我无法" / "无法访问你" 覆盖。
_REFUSE_PAT = re.compile(r"我无法|无法访问(你|您|本机|本地)|无法直接(访问|操作|帮|为你|替你)|我做不到|做不了|我只是.{0,6}(语言模型|AI|人工智能|模型)|请你?自己")


async def run_one(url: str, key: str, task: str, run_idx: int = 0) -> dict:
    full = f"{url}/invoke/stream?api_key={key}"
    # 每次运行用独立 session_id (带 run_idx): 否则同任务多跑会复用 session、带上一跑的历史,
    # 运行不独立 → 污染非确定性测量 (会看到雷同答案)。每跑干净起 session 才能真量稳定性。
    body = {"type": "agent", "task": task, "tenant_id": "default",
            "session_id": f"eval_{abs(hash(task)) % 100000}_{run_idx}", "trace_id": "eval"}
    actions, chunks, end = [], [], None
    t0 = time.time()
    try:
        async with websockets.connect(full, max_size=None, open_timeout=10) as ws:
            await ws.send(json.dumps(body, ensure_ascii=False))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=180)
                m = json.loads(raw)
                t = m.get("type")
                if t == "action":
                    actions.append(m.get("tool_name"))
                elif t == "chunk":
                    chunks.append(m.get("text", ""))
                elif t in ("done", "error"):
                    end = f"{t}:{m.get('code')}"
                    break
    except Exception as e:
        end = f"EXC:{type(e).__name__}:{str(e)[:40]}"
    return {"end": end, "answer": "".join(chunks),
            "actions": [a for a in actions if a], "latency": round(time.time() - t0, 1)}


def check(result: dict, spec: dict) -> list:
    ans = result["answer"]
    fails = []
    if spec.get("no_error") and result["end"] != "done:SUCCESS":
        fails.append(f"end={result['end']}")
    if spec.get("nonempty") and not ans.strip():
        fails.append("空答案")
    if "min_len" in spec and len(ans.strip()) < spec["min_len"]:
        fails.append(f"答案过短({len(ans.strip())}<{spec['min_len']})")
    if "contains" in spec and not any(c in ans for c in spec["contains"]):
        fails.append(f"未含{spec['contains']}")
    if spec.get("not_refused") and _REFUSE_PAT.search(ans):
        fails.append("出现拒绝措辞")
    return fails


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="ws://127.0.0.1:18877")
    ap.add_argument("--key", default="dev_api_key_1_change_in_prod")
    ap.add_argument("--runs", type=int, default=1)
    args = ap.parse_args()
    url = args.url.replace("https://", "wss://").replace("http://", "ws://").rstrip("/")

    total = passed = 0
    all_lat = []
    print(f"== Agent 评测台 ==  {url}  每任务 {args.runs} 次\n")
    for spec in TASKS:
        ok_n, lat, last_fail = 0, [], ""
        for ri in range(args.runs):
            r = await run_one(url, args.key, spec["task"], ri)
            lat.append(r["latency"]); all_lat.append(r["latency"])
            fails = check(r, spec["assert"])
            total += 1
            if not fails:
                ok_n += 1; passed += 1
            else:
                last_fail = "; ".join(fails) + f"  [tools={r['actions']} ans={r['answer'][:50]!r}]"
        avg = round(sum(lat) / len(lat), 1) if lat else 0
        mark = "PASS" if ok_n == args.runs else ("PART" if ok_n else "FAIL")
        print(f"[{mark}] {spec['name']:<12} {ok_n}/{args.runs}  ~{avg:>5}s  {last_fail}")

    rate = round(100 * passed / total, 1) if total else 0.0
    avg_all = round(sum(all_lat) / len(all_lat), 1) if all_lat else 0
    print(f"\n总成功率: {passed}/{total} = {rate}%   平均延迟 ~{avg_all}s")
    raise SystemExit(0 if passed == total else 1)


if __name__ == "__main__":
    asyncio.run(main())

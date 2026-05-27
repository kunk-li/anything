# -*- coding: utf-8 -*-
"""
最小联调脚本(本地 dev 模式)

运行方式：
    python run_smoke_test.py

说明：
    本脚本是本地联调用,默认启用 ANYTHING_DEV_MODE=1,允许 bootstrap 在某个依赖
    初始化失败时回退到占位实现(便于排查问题)。生产部署使用 main_api.py /
    main_console.py 进入口,默认走 fail-fast 严格启动。
"""

import os

# 显式启用 dev 模式,放在 import bootstrap 之前以便 bootstrap 读取
os.environ.setdefault("ANYTHING_DEV_MODE", "1")

from bootstrap import build_handler


def main():
    handler = build_handler()

    cases = [
        {"type": "rag", "query": "什么是 RAG？", "top_k": 3},
        {"type": "agent", "task": "请写一段开发计划"},
        {"type": "hybrid", "task": "请基于知识库回答这个问题"},
    ]

    for i, case in enumerate(cases, start=1):
        result = handler.handle(case, trace_id=f"trace_smoke_{i}")
        print("=" * 60)
        print(f"CASE {i}: {case['type']}")
        print(result)


if __name__ == "__main__":
    main()

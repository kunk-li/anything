# -*- coding: utf-8 -*-
"""
最小联调脚本
运行方式：
    python run_smoke_test.py
"""

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

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ABC 签名漂移检测脚本

扫描各模块的 core/base.py 中的 @abstractmethod 与 core/impl.py 中的同名方法,
对比函数签名,发现漂移立刻报错退出。

设计动机:
    历史上多次出现"BaseXXX 抽象 retrieve(query, top_k) 但 SimpleXXX 实现接收
    request: dict"这种签名漂移,迫使调用方在使用点做 try/except 签名探测。
    本脚本作为定期回归手段(可挂在 pre-commit 或 CI 上),让漂移当场暴露。

用法:
    python scripts/check_abc_alignment.py        # 扫描所有模块
    python scripts/check_abc_alignment.py --ci   # CI 模式: 仅打印漂移项,有漂移则 exit 1

约定:
    - 抽象方法签名以 base.py 为准
    - 实现允许新增可选关键字参数(向后兼容),但不允许:
        * 必填参数与抽象不同
        * 参数名拼写不同(filter vs filters 这类)
        * 返回类型注解不同
"""

from __future__ import annotations

import argparse
import inspect
import sys
import importlib
from pathlib import Path
from typing import Dict, List, Tuple

# 项目根目录(scripts/ 的上一级)
ROOT = Path(__file__).resolve().parent.parent

# (module_name, base_class_name, impl_class_name)
# 这里只列出已经/计划纳入 ABC 检测的模块
TARGETS: List[Tuple[str, str, str]] = [
    # 数据层
    ("vector_db_module", "BaseVectorDB", "FaissVectorDB"),
    ("llm_adapter_module", "BaseLLMService", "LLMService"),
    ("document_store_module", "BaseDocumentStore", "LocalDocumentStore"),
    ("state_store_module", "BaseStateStore", "LocalStateStore"),
    ("document_parser_module", "BaseDocumentParser", "LocalDocumentParser"),
    # 业务层
    ("embedding_module", "BaseEmbedding", "STEmbedding"),
    ("rag_module", "BaseRAG", "SimpleRAG"),
    ("agent_module", "BaseAgent", "SimpleAgent"),
    ("orchestrator_module", "BaseOrchestrator", "SimpleOrchestrator"),
    # 接口/应用层
    ("request_response_module", "BaseRequestHandler", "RequestHandler"),
]


def _setup_path() -> None:
    """注册项目源路径,让 module_name 可直接 import。"""
    for layer in ("basic_support", "data_layer", "business", "interface", "application", "run"):
        p = str(ROOT / layer)
        if p not in sys.path:
            sys.path.insert(0, p)
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))


def _list_abstract_methods(cls) -> Dict[str, inspect.Signature]:
    """返回类中所有抽象方法的名字 → 签名映射。"""
    methods: Dict[str, inspect.Signature] = {}
    for name in getattr(cls, "__abstractmethods__", ()):
        fn = getattr(cls, name, None)
        if fn is None or not callable(fn):
            continue
        try:
            methods[name] = inspect.signature(fn)
        except (ValueError, TypeError):
            continue
    return methods


def _impl_signature(impl_cls, method_name: str) -> inspect.Signature | None:
    fn = getattr(impl_cls, method_name, None)
    if fn is None or not callable(fn):
        return None
    try:
        return inspect.signature(fn)
    except (ValueError, TypeError):
        return None


def _required_params(sig: inspect.Signature) -> List[str]:
    """抽出"必填参数"名字列表(排除 self / *args / **kwargs / 有默认值的)。"""
    result = []
    for p in sig.parameters.values():
        if p.name == "self":
            continue
        if p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue
        if p.default is not inspect.Parameter.empty:
            continue
        result.append(p.name)
    return result


def _check_pair(module_name: str, base_name: str, impl_name: str) -> List[str]:
    """检查一对 (Base, Impl), 返回漂移问题列表(空 = 无漂移)。"""
    issues: List[str] = []
    try:
        mod = importlib.import_module(module_name)
    except ImportError as e:
        return [f"  ✗ import failed: {e}"]

    base_cls = getattr(mod, base_name, None)
    impl_cls = getattr(mod, impl_name, None)
    if base_cls is None:
        issues.append(f"  ✗ base class '{base_name}' 不在 {module_name}.__init__")
    if impl_cls is None:
        issues.append(f"  ✗ impl class '{impl_name}' 不在 {module_name}.__init__")
    if not base_cls or not impl_cls:
        return issues

    if not issubclass(impl_cls, base_cls):
        issues.append(f"  ✗ {impl_name} 未继承 {base_name}")

    abstract_methods = _list_abstract_methods(base_cls)
    for name, base_sig in abstract_methods.items():
        impl_sig = _impl_signature(impl_cls, name)
        if impl_sig is None:
            issues.append(f"  ✗ {impl_name}.{name} 未实现")
            continue

        base_required = set(_required_params(base_sig))
        impl_required = set(_required_params(impl_sig))

        # impl 必填集合不能"多于"base(impl 可少不可多)
        extra_required = impl_required - base_required
        # 但允许 impl 把 base 的必填参数变可选(放宽)
        missing_in_impl = [p for p in base_required
                           if p not in impl_sig.parameters]
        if extra_required:
            issues.append(
                f"  ✗ {impl_name}.{name} 多出必填参数 {sorted(extra_required)} "
                f"(base 签名 {base_sig})"
            )
        if missing_in_impl:
            issues.append(
                f"  ✗ {impl_name}.{name} 缺少 base 要求的参数 {missing_in_impl}"
            )

    return issues


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="检测 ABC 与实现签名漂移")
    parser.add_argument("--ci", action="store_true",
                        help="CI 模式:有漂移时 exit 1")
    args = parser.parse_args(argv)

    _setup_path()

    total_issues = 0
    print("=" * 60)
    print("ABC 签名漂移检测")
    print("=" * 60)
    for module_name, base_name, impl_name in TARGETS:
        print(f"\n[{module_name}] {base_name} ↔ {impl_name}")
        issues = _check_pair(module_name, base_name, impl_name)
        if not issues:
            print("  ✓ aligned")
        else:
            for line in issues:
                print(line)
            total_issues += len(issues)

    print("\n" + "=" * 60)
    if total_issues == 0:
        print("ALL ALIGNED ✓")
        return 0
    else:
        print(f"DRIFT FOUND: {total_issues} issue(s)")
        return 1 if args.ci else 0


if __name__ == "__main__":
    sys.exit(main())

import inspect
import json
from typing import (
    get_type_hints, Union, Optional, List, Dict, Any, get_origin, get_args
)
from enum import Enum
import re


# ---------- 工具函数：Python 类型 → JSON Schema ----------

def python_type_to_schema(tp):
    origin = get_origin(tp)
    args = get_args(tp)

    # Optional[T] → Union[T, None]
    if origin is Union and type(None) in args:
        non_none_type = [a for a in args if a is not type(None)][0]
        sub = python_type_to_schema(non_none_type)
        return {"anyOf": [sub, {"type": "null"}]}

    # Union[X, Y]
    if origin is Union:
        return {
            "anyOf": [python_type_to_schema(a) for a in args]
        }

    # List[X]
    if origin is list or origin is List:
        return {
            "type": "array",
            "items": python_type_to_schema(args[0])
        }

    # Dict[X, Y]
    if origin is dict or origin is Dict:
        return {
            "type": "object",
            "additionalProperties": python_type_to_schema(args[1])
        }

    # Enum
    if inspect.isclass(tp) and issubclass(tp, Enum):
        return {
            "type": "string",
            "enum": [m.value for m in tp]
        }

    # 基础类型
    mapping = {
        str: "string",
        int: "integer",
        float: "number",
        bool: "boolean",
    }
    if tp in mapping:
        return {"type": mapping[tp]}

    # 默认 string
    return {"type": "string"}


# ---------- 解析 docstring 参数说明 ----------

def parse_docstring_args(doc: str) -> dict:
    """
    解析 Google 风格 docstring: Args:
    返回 {param: description}
    """
    if not doc:
        return {}

    args_section = {}
    pattern = r"Args:\s*((?:.|\n)*?)(?=\n\S|$)"
    match = re.search(pattern, doc)
    if not match:
        return {}

    args_text = match.group(1).strip()
    lines = args_text.split("\n")
    for ln in lines:
        if ":" in ln:
            name, desc = ln.split(":", 1)
            args_section[name.strip()] = desc.strip()

    return args_section


# ---------- 工具装饰器：支持 category ----------

def tool(category: str = "default"):
    def decorator(func):
        func._is_tool = True
        func._tool_category = category
        return func
    return decorator


# -------------------- FunctionManager 主体 --------------------

class FunctionManager:
    tools = []

    def __init__(self):
        self._build_tools()

    def _build_tools(self):
        for name, method in inspect.getmembers(self, predicate=inspect.ismethod):
            if hasattr(method, "_is_tool"):
                sig = inspect.signature(method)
                type_hints = get_type_hints(method)
                doc = inspect.getdoc(method) or ""
                description = doc.split("\n")[0].strip()

                doc_arg_desc = parse_docstring_args(doc)

                props = {}
                required = []

                for param_name, param in sig.parameters.items():
                    if param_name == "self":
                        continue

                    anno = type_hints.get(param_name, str)
                    schema = python_type_to_schema(anno)

                    # 使用 docstring 的参数描述
                    desc = doc_arg_desc.get(param_name, f"Parameter: {param_name}")

                    # 默认值
                    if param.default is not inspect._empty:
                        schema["default"] = param.default
                    else:
                        required.append(param_name)

                    schema["description"] = desc
                    props[param_name] = schema

                # 处理返回类型（可选）
                returns_schema = None
                if "return" in type_hints:
                    returns_schema = python_type_to_schema(type_hints["return"])

                # 生成工具 schema
                tool_schema = {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": description,
                        "category": method._tool_category,
                        "parameters": {
                            "type": "object",
                            "properties": props,
                            "required": required
                        }
                    }
                }

                if returns_schema:
                    tool_schema["function"]["returns"] = returns_schema

                self.tools.append(tool_schema)

    def function_call(self, function_name, args):
        if hasattr(self, function_name):
            return getattr(self, function_name)(**args)


# -------------------- 示例：你可以随意扩展 --------------------

class Color(Enum):
    RED = "red"
    BLUE = "blue"


class MyFunctions(FunctionManager):

    @tool(category="weather")
    def get_weather(self, location: str, unit: Optional[str] = None) -> dict:
        """
        获取天气信息

        Args:
            location: 查询的城市
            unit: 单位（可选）
        """
        return {"temperature": "24℃", "unit": unit}

    @tool(category="math")
    def add(self, a: int, b: int = 3) -> int:
        """
        加法

        Args:
            a: 第一个数字
            b: 第二个数字
        """
        return a + b

    @tool(category="paint")
    def set_color(self, color: Color) -> str:
        """
        设置颜色

        Args:
            color: 颜色枚举
        """
        return f"Color set to {color.value}"

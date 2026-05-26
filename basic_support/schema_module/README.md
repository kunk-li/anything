# schema_module

系统统一数据 Schema(基于 Pydantic v2)。

## 职责

定义系统跨模块流转的统一数据契约,在 **系统边界**(API 入口 / 接口层)做强校验,
内部仍可用 dict 传递,避免每次调用都付出 model 序列化开销。

## 包含

| Schema | 对应文档 | 用途 |
|---|---|---|
| `RequestEnvelope` | 第 8 章 统一请求格式 | API/Console 入口请求解析 + 校验 |
| `ResponseEnvelope` | 第 10 章 统一响应信封 | 对外响应结构契约(可选用于序列化) |
| `validate_request_dict()` | - | 把 dict 请求按 RequestEnvelope 校验,返回 (ok, msg, error_code) |

## 设计偏离说明

本模块**有意偏离**架构规范中 "core/base.py + impl.py" 的统一目录结构。原因:

> Schema 是**声明式数据契约**,不存在"抽象接口/具体实现"二分关系 —
> 强行套 ABC + impl 等于把数据类硬塞进面向行为的二分模板,反而降低可读性。

实际目录:

```
schema_module/
├── __init__.py        # 导出 schema 类
├── schema.py          # Pydantic models(无 base/impl 二分)
├── tests/
│   └── test_schema.py
└── README.md          # 本文件
```

## 使用示例

```python
from schema_module import RequestEnvelope, validate_request_dict

# 1. 严格校验(在 RequestHandler / ApiService 边界)
ok, msg, code = validate_request_dict(request_dict)
if not ok:
    return {"code": code, "message": msg, ...}

# 2. 拿到强类型对象(可选)
env = RequestEnvelope.model_validate(request_dict)
print(env.type, env.query, env.top_k)

# 3. 序列化回 dict(传给下游)
downstream_dict = env.model_dump()
```

## 不应该做什么

- ❌ 不要在每个内部函数都做 model_validate(会让性能不可控)
- ❌ 不要把 schema 当成"运行时配置"(那是 config_module 的职责)
- ❌ 不要在 schema 里写业务逻辑(只做数据契约与最小约束)

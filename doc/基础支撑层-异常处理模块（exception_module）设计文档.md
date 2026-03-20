# 基础支撑层-异常处理模块（exception_module）设计文档

| 文档版本 | v1.1 |
| :--- | :--- |
| 最后更新 | 2026-03-19 |
| 维护责任人 | 异常处理模块开发负责人 |
| 状态 | 修订版 |

> 本修订版对齐《RAG与Agent系统架构设计说明书》v1.1 及统一错误码表，重点修正标准异常层级、业务错误码映射、trace_id 贯穿、结构化 details 生成与异常兜底策略。

# 1. 文档概述

## 1.1 文档目的

本文档为 RAG 与 Agent 系统基础支撑层-异常处理模块（`exception_module`）的独立设计说明书。

本模块负责系统中的统一异常定义、捕获、封装与标准化输出能力，是“异常分类 -> 错误码映射 -> 结构化 details -> 日志记录 -> 统一兜底”的核心基础支撑模块。

# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于**基础支撑层**，负责：

- 定义系统标准异常基类
- 定义模块级异常类型
- 统一错误码与 message
- 生成结构化 details
- 提供异常转统一响应的辅助能力
- 保证 trace_id 可贯穿异常日志与返回结果

本模块不负责：

- 不直接决定 HTTP 状态码
- 不负责任务规划与业务执行
- 不替代日志模块，只与之协同

## 2.2 标准异常层级（推荐）

- `SystemBaseException`
- `ConfigException`
- `DocumentException`
- `VectorDBException`
- `RAGException`
- `AgentException`
- `APIException`
- `StateStoreException`

约束：

- 所有自定义异常应至少携带：
  - `code`
  - `message`
  - `details`
  - `retryable`（可选）

## 2.3 错误码映射原则

- 错误码以系统总设计第 10 章为准
- 子模块不得随意新造冲突错误码
- 未知异常统一映射为 `UNKNOWN_ERROR`
- details 尽量结构化，不返回一大段非结构化字符串

# 3. 统一项目结构规范

```text
exception_module/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── base.py
│   └── impl.py
├── utils/
│   ├── __init__.py
│   └── tool_functions.py
├── config/
│   ├── __init__.py
│   └── config.py
├── tests/
│   ├── __init__.py
│   └── test_impl.py
├── README.md
└── requirements.txt
```

# 4. 核心接口设计（抽象基类）

```python
from abc import ABC, abstractmethod
from typing import Any, Dict

class BaseExceptionHandler(ABC):
    @abstractmethod
    def handle(self, exc: Exception, trace_id: str | None = None) -> Dict[str, Any]:
        pass
```

# 5. 核心实现设计（ExceptionHandler）

## 5.1 核心职责

- 将异常转换为统一错误结构
- 根据异常类型映射错误码
- 生成结构化 details
- 记录异常日志
- 为接口层、应用层、业务层提供统一异常出口

## 5.2 标准输出结构

建议输出：

```json
{
  "code": "RAG_RUN_FAILED",
  "message": "RAG执行失败",
  "data": null,
  "trace_id": "trace_demo_001",
  "retryable": true,
  "details": {
    "stage": "generate"
  }
}
```

## 5.3 trace_id 规则

- `trace_id` 由上游传入
- 异常处理模块不得重新生成新的业务 `trace_id`
- 所有异常日志都应尽量携带 `trace_id`

## 5.4 未知异常兜底

规则：

- 未知异常统一映射 `UNKNOWN_ERROR`
- 记录异常类型、简要堆栈与上下文
- 不把完整敏感堆栈直接暴露给最终用户

# 6. 测试规范

必须覆盖：

- 标准异常映射
- 自定义异常 details 透传
- 未知异常兜底
- trace_id 贯穿
- retryable 规则
- 敏感信息不直接暴露

# 7. 交付物清单

- `core/base.py`
- `core/impl.py`
- `utils/tool_functions.py`
- `config/config.py`
- `tests/test_impl.py`
- `README.md`
- `requirements.txt`

# 8. 可替换性约束

- 上游模块只能依赖统一异常处理接口
- 错误码规则不得破坏系统总设计
- details 结构化要求不得移除

# 9. FAQ

| 问题 | 说明 |
| :--- | :--- |
| 为什么不能直接把 Python 原始异常返回给前端？ | 因为会泄露实现细节且不利于统一处理。 |
| 为什么要保留 retryable？ | 因为调用方需要据此决定是否重试。 |

# 10. 附录：系统错误码关联

本模块直接围绕系统总设计第 10 章错误码表工作，不额外定义与总设计冲突的新主错误码。

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
# 基础支撑层-通用工具模块（common_utils_module）设计文档

| 文档版本 | v1.1 |
| :--- | :--- |
| 最后更新 | 2026-03-19 |
| 维护责任人 | 通用工具模块开发负责人 |
| 状态 | 修订版 |

> 本修订版对齐《RAG与Agent系统架构设计说明书》v1.1 及各层修订版子设计，重点修正工具边界、文本/数据/参数校验/辅助能力分组、避免工具模块承载业务逻辑以及统一可复用规范。

# 1. 文档概述

## 1.1 文档目的

本文档为 RAG 与 Agent 系统基础支撑层-通用工具模块（`common_utils_module`）的独立设计说明书。

本模块负责系统中的通用辅助能力，是“文本处理 -> 数据转换 -> 参数校验 -> 时间/哈希/编码等辅助函数”的核心工具集合模块。

# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于**基础支撑层**，负责提供跨模块复用的通用辅助能力，主要包括：

- 文本处理工具
- 数据转换工具
- 参数校验工具
- 时间/哈希/编码等辅助工具

本模块不负责：

- 不承载具体业务逻辑
- 不负责请求路由、调度、存储、模型调用
- 不将模块专属逻辑错误地沉到通用工具层

## 2.2 工具分组规范（强制）

建议至少拆分为：

- `text_tool.py`
  - 文本清洗
  - 脱敏
  - 格式检查

- `data_tool.py`
  - JSON 转换
  - 类型转换
  - 列表/字典辅助处理

- `param_validate.py`
  - 必填校验
  - 范围校验
  - 类型校验

- `assist_tool.py`
  - 哈希
  - base64
  - 时间格式化
  - 时间范围辅助
  - 简单标识生成

## 2.3 工具边界约束

- 工具函数必须保持无副作用或副作用可预期
- 工具函数不得依赖业务模块实现细节
- 不得把 RAG、Agent、API 等业务逻辑沉到 common_utils
- 若某工具明显只服务某模块，应迁回对应模块 `utils/`

# 3. 统一项目结构规范

```text
common_utils_module/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── base.py
│   └── impl.py
├── utils/
│   ├── __init__.py
│   ├── text_tool.py
│   ├── data_tool.py
│   ├── param_validate.py
│   └── assist_tool.py
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

可定义统一聚合入口：

```python
from abc import ABC, abstractmethod

class BaseCommonUtils(ABC):
    @abstractmethod
    def get_text_tool(self):
        pass

    @abstractmethod
    def get_data_tool(self):
        pass

    @abstractmethod
    def get_param_validate(self):
        pass

    @abstractmethod
    def get_assist_tool(self):
        pass
```

# 5. 核心实现设计（CommonUtils）

## 5.1 核心职责

- 聚合通用工具实例
- 统一对外暴露工具入口
- 保证工具能力边界清晰
- 避免重复实现

## 5.2 文本工具规范

应支持：

- `clean_text`
- `mask_sensitive_info`
- `is_valid_text`

约束：

- 不做业务语义重写
- 不做摘要生成
- 不静默篡改原意

## 5.3 数据工具规范

应支持：

- `safe_json_loads`
- `safe_json_dumps`
- `to_int / to_float / to_bool`
- `deduplicate_list`

约束：

- 转换失败要么返回默认值，要么显式抛异常
- 行为必须可预测

## 5.4 参数校验工具规范

应支持：

- 必填参数校验
- 类型校验
- 数值范围校验
- 枚举值校验

约束：

- 只做通用校验，不硬编码模块专属规则
- 复杂业务校验应留给对应业务模块

## 5.5 辅助工具规范

应支持：

- `md5 / sha256`
- `base64_encode / decode`
- `format_time`
- `parse_time`
- `is_in_range`
- 常见时间起止辅助

约束：

- 不使用不必要的重依赖
- 对时区、格式歧义要写清楚

# 6. 测试规范

必须覆盖：

- 文本清洗
- JSON 转换
- 参数校验
- 时间工具
- 哈希工具
- 边界值与异常输入

# 7. 交付物清单

- `core/base.py`
- `core/impl.py`
- `utils/text_tool.py`
- `utils/data_tool.py`
- `utils/param_validate.py`
- `utils/assist_tool.py`
- `tests/test_impl.py`
- `README.md`
- `requirements.txt`

# 8. 可替换性约束

- 上游模块只能依赖通用工具公开接口
- 不得把业务逻辑固化到工具层
- 工具命名与行为需稳定

# 9. FAQ

| 问题 | 说明 |
| :--- | :--- |
| 什么工具应该放到 common_utils？ | 跨多个模块复用、且不带明显业务语义的工具。 |
| 什么工具不该放到 common_utils？ | 只服务某个模块、带明显业务规则的逻辑。 |

# 10. 附录：系统错误码关联

本模块一般不直接定义主业务错误码，参数校验失败可配合调用方使用 `PARAM_INVALID`、`PARAM_MISSING` 等统一错误码。

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
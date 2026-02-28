# exception_module（基础支撑层 - 异常处理模块）

本模块为 RAG（检索增强生成）与 Agent（智能代理）系统基础支撑层中的 **统一异常处理模块**，用于：
- 定义系统统一异常基类与各业务异常类型（CONFIG / VECTOR / RAG / AGENT）
- 捕获并封装异常为统一输出格式：`{"code": "...", "message": "..."}`（与系统错误码表严格对应）
- 记录异常日志（依赖 `log_module`；若本地没有该模块，会自动使用兜底 logger 以便单测/演示）

## 目录结构

```
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
│   ├── test_base.py
│   └── test_impl.py
└── requirements.txt
```

## 快速开始

```python
from exception_module.core.impl import ExceptionHandler, ConfigException

handler = ExceptionHandler()

try:
    raise ConfigException("CONFIG_NOT_FOUND", "配置文件不存在")
except Exception as e:
    print(handler.handle_exception(e))
    # {"code": "CONFIG_NOT_FOUND", "message": "配置文件不存在"}
```

## 自定义异常类型

- 所有系统自定义异常应继承 `SystemBaseException`，并传入 `code`（错误码表中的业务码）与 `message`
- 业务模块新增异常类型：新增一个继承 `SystemBaseException` 的类即可，无需修改处理器核心逻辑

## 与错误码表的关系

`utils/tool_functions.py` 中维护了 `EXCEPTION_CODE_MAP`（错误码到默认 message 的映射）。
当你们的“系统全局错误码表”更新时，需要同步更新此映射表。

## 依赖

- Python 3.10+
- `log_module`（基础支撑层日志模块）：生产环境应提供 `log_module.core.impl.SystemLogger`

> 说明：为了保证本模块“可独立开发/测试”，当 `log_module` 不存在时会自动启用兜底 logger（标准库 logging）。


## 与 log_module 的对齐说明

- 本模块按《日志模块（log_module）设计文档》使用 `from log_module import SystemLogger` 导入日志器；
  记录日志时调用 `SystemLogger().info/warning/error/critical(message, logger_name=...)`。fileciteturn1file0
- 异常日志采用 **单条 message + JSON 字段** 的方式输出，便于多进程场景下快速 grep/解析。
- 若本地未集成 log_module（仅本模块独立开发/单测），会自动启用标准库 logging 的兜底实现，不影响测试运行。

## 运行测试

在模块同级目录执行：

```bash
python -m unittest -v
```

或仅运行本模块测试：

```bash
python -m unittest -v exception_module.tests.test_impl
```

## 常见问题

- Q：为什么我本地没有 `log_module` 也能跑？
  - A：本模块内置了兜底 logger，方便开发与单测；集成到系统时请确保 `log_module` 可用。

## 与 config_module 的对齐说明

本模块不强依赖 `config_module`，但在系统集成时建议注入 `ConfigManager` 以读取全局配置：
- `global.log_level`：用于本模块 fallback logger 的日志级别（当 log_module 未就绪/本地单测时生效）fileciteturn2file0
- `exception_module.logger_name`：可选，用于覆盖本模块写日志时的 logger_name（便于集中检索）

示例：

```python
from config_module.core.impl import ConfigManager
from exception_module.core.impl import ExceptionHandler

cm = ConfigManager()
cm.load_config()  # 默认读取 config_module/config/config.yaml

handler = ExceptionHandler(config_manager=cm)
```


# 基础支撑层-日志模块（log_module）设计文档

| 文档版本 | v1.1 |
| :--- | :--- |
| 最后更新 | 2026-03-19 |
| 维护责任人 | 日志模块开发负责人 |
| 状态 | 修订版 |

> 本修订版对齐《RAG与Agent系统架构设计说明书》v1.1 及各层修订版子设计，重点修正 trace_id 日志贯穿、多进程日志边界、审计日志区分、JSON/文本格式支持与统一日志配置。

# 1. 文档概述

## 1.1 文档目的

本文档为 RAG 与 Agent 系统基础支撑层-日志模块（`log_module`）的独立设计说明书。

本模块负责系统中的统一日志能力，是“logger 初始化 -> 日志格式化 -> 控制台/文件输出 -> 审计日志 -> 多进程安全”的核心基础支撑模块。

## 1.2 核心需求回顾

| 需求类型 | 具体要求 |
| :--- | :--- |
| 模块功能 | 提供统一 logger、文本/JSON 输出、trace_id 贯穿、审计日志与多进程安全能力。 |
| 模块约束 | 不承载业务逻辑；日志句柄初始化统一；不泄露敏感信息。 |

# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于**基础支撑层**，负责：

- 统一 logger 创建与缓存；
- 控制台与文件双输出；
- 文本与 JSON 两种日志格式；
- 普通日志与审计日志区分；
- 多进程场景的句柄、锁与写入安全；
- `trace_id / session_id` 等上下文字段的贯穿。

本模块不负责：

- 不负责业务逻辑；
- 不负责 HTTP 状态码；
- 不负责配置生成（仅读取配置）；
- 不负责链路追踪系统本身，只提供日志落点。

## 2.2 日志级别与分类

标准日志级别：

- DEBUG
- INFO
- WARNING
- ERROR
- CRITICAL

推荐日志分类：

- `app`：普通业务日志
- `audit`：审计日志
- `access`：应用访问日志（可选）
- `error`：错误聚合日志（可选）

## 2.3 trace_id / session_id 规则（强制）

- 所有与请求相关日志都应尽量带 `trace_id`
- Agent / Hybrid 相关日志应带 `session_id`
- 本模块不生成新的业务 `trace_id`
- 若上游未提供，可为空，但不得伪造新的业务链路 ID

# 3. 统一项目结构规范

```text
log_module/
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

class BaseLogger(ABC):
    @abstractmethod
    def get_logger(self, name: str):
        pass

    @abstractmethod
    def get_audit_logger(self, name: str):
        pass
```

# 5. 核心实现设计（SystemLogger）

## 5.1 核心职责

- 初始化 logger
- 维护 logger 缓存
- 支持控制台与文件输出
- 支持 JSON / 文本格式
- 多进程安全写入
- 审计日志独立文件

## 5.2 多进程规范（强制）

- 锁必须成对获取/释放
- 每个进程应独立初始化句柄或确保安全共享
- 日志写入异常不得导致业务主流程崩溃
- 多进程测试必须存在

## 5.3 日志格式规范

推荐文本日志字段：

- timestamp
- level
- logger_name
- trace_id
- session_id
- message

推荐 JSON 日志字段：

```json
{
  "timestamp": "2026-03-19T12:00:00Z",
  "level": "INFO",
  "logger": "rag_module",
  "trace_id": "trace_demo_001",
  "session_id": "session_001",
  "message": "retrieve started"
}
```

## 5.4 审计日志规则

审计日志建议记录：

- 文档上传
- 文档删除
- Agent 执行开始/完成
- 敏感操作
- 认证失败（可选）

约束：

- 审计日志与普通日志应分开存储
- 审计日志格式应更适合检索与合规审查

## 5.5 敏感信息保护

日志中不得直接输出：

- API Key
- token
- password
- secret
- 原始凭证内容

必要时必须脱敏。

# 6. 配置示例

```yaml
log:
  level: "INFO"
  log_dir: "./logs"
  json_format: false
  audit_enabled: true
  audit_file: "audit.log"
```

# 7. 测试规范

必须覆盖：

- logger 初始化
- 控制台/文件输出
- JSON 格式输出
- 审计日志输出
- trace_id / session_id 字段保留
- 多进程写入测试
- 敏感信息不泄露

# 8. 交付物清单

- `core/base.py`
- `core/impl.py`
- `utils/tool_functions.py`
- `config/config.py`
- `tests/test_impl.py`
- `tests/test_multiprocess.py`
- `README.md`
- `requirements.txt`

# 9. 可替换性约束

- 上游模块只能依赖统一 logger 获取接口
- 输出后端（本地文件、stdout、外部 sink）可替换
- trace_id / session_id 字段约束不变
- 审计日志能力不得移除

# 10. FAQ

| 问题 | 说明 |
| :--- | :--- |
| 为什么要区分普通日志和审计日志？ | 因为两者用途不同，审计日志更强调可追溯与合规。 |
| 多进程为什么需要单独测试？ | 因为句柄竞争和锁问题容易导致日志丢失或损坏。 |

# 11. 附录：系统错误码关联

本模块一般不主动定义业务错误码，内部异常建议映射为：

- `UNKNOWN_ERROR`
- 或在调用方上下文中记录日志后继续抛出
- 
返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
# 基础支撑层-配置管理模块（config_module）设计文档

| 文档版本 | v1.1 |
| :--- | :--- |
| 最后更新 | 2026-03-19 |
| 维护责任人 | 配置管理模块开发负责人 |
| 状态 | 修订版 |

> 本修订版对齐《RAG与Agent系统架构设计说明书》v1.1 及各层修订版子设计，重点修正全局/模块配置分层、环境变量覆盖规范、默认值策略、热更新边界、敏感配置保护与统一错误码。

# 1. 文档概述

## 1.1 文档目的

本文档为 RAG 与 Agent 系统基础支撑层-配置管理模块（`config_module`）的独立设计说明书。

本模块负责系统中的统一配置管理能力，是“配置加载 -> 环境变量覆盖 -> 默认值合并 -> 读取访问 -> 可选热更新”的核心基础支撑模块。模块在系统中的职责包括：

- 加载系统级与模块级配置；
- 支持环境变量覆盖与默认值解析；
- 提供统一配置读取接口；
- 支持模块配置分层与命名空间隔离；
- 对敏感配置做最小暴露与保护；
- 为全系统所有模块提供统一配置访问能力。

本文档作为本模块开发、测试、联调与后续替换实现的唯一标准依据。

## 1.2 适用人群

适用于配置管理模块开发人员、所有模块开发人员、测试人员、架构设计人员及后续维护人员。

## 1.3 核心需求回顾

| 需求类型 | 具体要求 |
| :--- | :--- |
| 模块功能 | 提供统一配置加载、读取、覆盖、校验与可选热更新能力。 |
| 开发语言 | Python 3.10+，最低 3.10，推荐 3.12。 |
| 开发模式 | 独立开发、可替换实现、通过抽象接口集成。 |
| 文档要求 | 与系统总设计 v1.1、部署规范、各模块配置章节保持一致。 |
| 模块约束 | 本模块不负责 HTTP 协议处理；不负责业务逻辑；应作为全系统统一配置入口。 |

# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于系统**基础支撑层**，是全系统统一配置管理能力的基础模块。

本模块职责如下：

- 加载 YAML / JSON / Python 配置；
- 解析环境变量占位符；
- 提供统一的 `get_config` / `set_config` / `reload` 访问方式；
- 维护全局配置与模块配置的命名空间隔离；
- 对敏感配置做最小暴露；
- 为其他模块提供统一配置依赖能力。

本模块不负责：

- 不负责业务逻辑；
- 不负责路由、请求处理与调度；
- 不负责日志输出本身（仅提供日志相关配置）；
- 不直接决定应用层行为，只提供配置数据。

## 2.2 配置分层规范（强制）

系统配置应按以下层次组织：

1. **系统全局配置**
   - 运行环境
   - 通用超时
   - 安全与认证
   - 通用目录路径

2. **模块级配置**
   - `request_response`
   - `api_service`
   - `orchestrator`
   - `agent`
   - `rag`
   - `embedding`
   - `vector_db`
   - `document_store`
   - `document_parser`
   - `state_store`
   - `llm_adapter`
   - `log`
   - `exception`

3. **环境变量覆盖**
   - 优先级高于配置文件
   - 支持 `${ENV_VAR}` 与 `${ENV_VAR:default}`

优先级规则：

- 环境变量覆盖 > 运行时显式注入 > 模块默认配置 > 配置文件默认值

## 2.3 依赖关系

### 2.3.1 上游依赖
无业务上游依赖，本模块为基础支撑模块。

### 2.3.2 下游依赖
所有模块均可依赖本模块获取配置。

### 2.3.3 基础依赖

| 依赖模块 | 用途 |
| :--- | :--- |
| `common_utils_module` | 可选的解析辅助能力 |
| `exception_module` | 配置异常封装 |
| `log_module` | 可选配置加载日志 |

# 3. 统一项目结构规范

```text
config_module/
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
│   └── config.yaml
├── tests/
│   ├── __init__.py
│   └── test_impl.py
├── README.md
└── requirements.txt
```

可选扩展目录：

- `schemas/`：配置 schema
- `examples/`：配置示例
- `docs/`：补充说明材料

# 4. 核心数据模型设计

## 4.1 ConfigNamespace（推荐）

```python
from dataclasses import dataclass, field
from typing import Any, Dict

@dataclass
class ConfigNamespace:
    name: str
    values: Dict[str, Any] = field(default_factory=dict)
```

## 4.2 配置结构约束（强制）

- 顶层键必须为模块名或系统公共配置域
- 配置键统一使用 `snake_case`
- 敏感字段（如 `api_key`、`jwt_secret`、`password`）必须支持环境变量覆盖
- 模块必须优先在自己的命名空间下读取配置

# 5. 核心接口设计（抽象基类）

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

class BaseConfigManager(ABC):
    @abstractmethod
    def load(self) -> Dict[str, Any]:
        pass

    @abstractmethod
    def get_config(self, key: Optional[str] = None, default: Any = None) -> Any:
        pass

    @abstractmethod
    def set_config(self, key: str, value: Any) -> None:
        pass

    @abstractmethod
    def reload(self) -> Dict[str, Any]:
        pass
```

约束：

- `get_config()` 支持读取全局、模块级与嵌套键；
- `reload()` 可选支持热更新；
- 所有实现都必须保证读取行为稳定。

# 6. 核心实现设计（ConfigManager）

## 6.1 核心职责

- 加载配置文件
- 解析环境变量占位符
- 合并默认值
- 提供嵌套键访问
- 支持可选热更新

## 6.2 环境变量覆盖规则（强制）

支持两种写法：

```yaml
api_key: "${OPENAI_API_KEY}"
base_url: "${OPENAI_BASE_URL:https://api.openai.com/v1}"
```

规则：

- 若环境变量存在，则使用其值；
- 若不存在且提供 default，则使用 default；
- 若不存在且无 default，则保留为空并在严格模式下报错。

## 6.3 嵌套键读取规则

推荐支持：

```python
config.get_config("llm_adapter.default_models.chat")
config.get_config("rag.top_k_retrieve", 50)
```

约束：

- 嵌套键读取失败可返回默认值；
- 严格模式下对关键键缺失返回 `CONFIG_KEY_MISSING`。

## 6.4 热更新边界

允许支持：

- 重新读取配置文件
- 刷新环境变量覆盖结果

不要求支持：

- 自动推送配置到所有已初始化对象
- 无锁热替换复杂状态对象

说明：

- `reload()` 仅刷新配置缓存；
- 下游模块是否重新获取配置由其自身控制。

## 6.5 敏感配置保护

敏感字段包括但不限于：

- `api_key`
- `jwt_secret`
- `password`
- `secret`
- `token`

约束：

- 日志中不得直接打印敏感值；
- `to_dict()` 或 debug 输出时必须脱敏；
- README 中不得硬编码真实凭证。

## 6.6 错误处理与统一返回

推荐错误码：

| 错误码 | 说明 |
| :--- | :--- |
| `SUCCESS` | 成功 |
| `CONFIG_NOT_FOUND` | 配置文件不存在 |
| `CONFIG_KEY_MISSING` | 关键配置项缺失 |
| `PARAM_INVALID` | 输入参数不合法 |
| `UNKNOWN_ERROR` | 未知异常 |

# 7. 配置示例

```yaml
system:
  env: "dev"

security:
  auth_enabled: true
  auth_type: "apikey"
  api_keys:
    - "${API_KEY_1}"

llm_adapter:
  provider: "openai"
  default_models:
    chat: "gpt-4.1-mini"
    embedding: "text-embedding-3-large"

rag:
  top_k_retrieve: 50
  top_k_rerank: 8
```

# 8. 测试规范

必须覆盖：

- 配置文件加载
- 环境变量覆盖
- 默认值回退
- 嵌套键读取
- 敏感配置脱敏
- reload 行为
- 缺失配置与严格模式报错

# 9. 交付物清单

- `core/base.py`
- `core/impl.py`
- `utils/tool_functions.py`
- `config/config.yaml`
- `tests/test_impl.py`
- `README.md`
- `requirements.txt`

# 10. 可替换性约束

- 上游模块只能依赖 `BaseConfigManager`
- 具体配置文件格式可替换，但接口不变
- 环境变量覆盖规则必须保持一致
- 敏感配置保护规则必须保持一致

# 11. 常见问题（FAQ）

| 问题 | 说明 |
| :--- | :--- |
| 为什么所有模块都不直接读 yaml？ | 因为应通过统一配置模块屏蔽配置来源与覆盖逻辑。 |
| reload 后已初始化对象会自动更新吗？ | 默认不会，reload 只刷新配置缓存。 |
| 为什么敏感字段必须脱敏？ | 防止日志与调试输出泄露凭证。 |

# 12. 附录：系统错误码关联

| 错误码 | 适用场景 |
| :--- | :--- |
| `SUCCESS` | 配置读取成功 |
| `CONFIG_NOT_FOUND` | 配置文件不存在 |
| `CONFIG_KEY_MISSING` | 关键配置缺失 |
| `PARAM_INVALID` | 输入键非法 |
| `UNKNOWN_ERROR` | 未知异常 |

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
# 数据层-状态存储模块（state_store_module）设计说明书

# 1. 文档概述

## 1.1 文档目的

本文档为RAG与Agent系统数据层状态存储模块（state_store_module）的专项设计说明书，用于指导开发人员（含初学者）进行该模块的独立开发、测试与集成。文档明确了模块功能、架构定位、接口定义、项目结构、配置规范及集成要求，确保模块开发符合系统整体架构标准，可与其他模块无缝对接，同时保障模块的可替换性与可维护性。

## 1.2 适用人群

本团队所有开发人员（含资深开发者与初学者）、测试人员，作为该模块开发、测试、部署及维护的唯一标准依据；项目管理人员可参考本文档进行模块开发进度管控。

## 1.3 模块定位

本模块属于系统**数据层**核心模块之一，依赖基础支撑层的通用工具模块、配置管理模块、日志模块、异常处理模块，主要负责存储Agent运行过程中的各类状态数据（会话记忆、任务步骤、工具调用记录等），为Agent模块提供状态持久化与读取能力，是Agent实现复杂任务执行、会话延续的核心支撑。

## 1.4 核心需求

- 功能需求：支持Agent状态的保存、读取、事件追加与清理，提供稳定的状态持久化能力。

- 开发规范：遵循系统统一的项目结构、编码规范，包含抽象基类（ABC），确保模块可替换。

- 兼容性：与系统其他模块（Agent模块、基础支撑层模块）通过统一接口通信，适配系统整体交互流程。

- 易用性：提供清晰的接口定义与使用说明，适配初学者开发，降低集成难度。

# 2. 术语定义

|术语|定义|
|---|---|
|状态存储|用于持久化Agent运行状态的模块，支持状态的保存、读取、追加与清理，保障Agent任务执行的连续性。|
|ABC|抽象基类，定义模块的核心接口与方法，强制子类实现，保障模块一致性与可替换性。|
|session_id|会话唯一标识，用于区分不同Agent会话的状态，确保状态数据的隔离性。|
|状态事件|Agent运行过程中产生的关键操作记录（如任务解析结果、工具调用结果），需追加到对应会话的状态中。|
# 3. 模块功能设计

## 3.1 核心功能

本模块核心用于存储Agent运行状态，支撑Agent任务的持续执行与会话记忆，具体功能如下：

- save_state：保存指定会话的Agent状态，支持完整状态的覆盖保存。

- get_state：根据会话ID，读取该会话的Agent状态数据，若会话不存在则返回None。

- append_event：向指定会话的状态中追加事件记录，用于跟踪Agent任务执行过程。

- clear_state：清理指定会话的状态数据，释放存储资源。

## 3.2 功能约束

- 状态数据需保证隔离性，不同session_id对应的状态互不干扰。

- 支持状态数据的持久化，服务重启后可恢复已保存的状态。

- 操作失败时需抛出统一格式的异常，由基础支撑层异常处理模块统一处理。

- 需记录关键操作日志（如状态保存失败、读取失败），便于问题排查。

# 4. 项目结构规范

本模块严格遵循系统统一的项目结构规范，开发者需严格遵循，不得随意修改目录名称与层级，初学者可直接复制该结构搭建项目。

```plain text
# 状态存储模块目录结构（模块名称：state_store_module）
state_store_module/                  # 模块根目录（全小写，多单词用下划线连接）
├── __init__.py                     # 模块初始化文件，暴露模块核心类/方法（必须包含，不能为空）
├── core/                           # 核心逻辑目录（存放模块核心实现，含ABC抽象类）
│   ├── __init__.py
│   ├── base.py                     # 抽象基类（ABC）文件，定义模块核心接口（必须包含）
│   └── impl.py                     # 具体实现类文件，继承base.py中的抽象类（必须包含）
├── utils/                          # 模块工具目录（存放模块专属工具函数，无则空目录）
│   ├── __init__.py
│   └── tool_functions.py           # 工具函数文件（如状态数据格式校验、路径处理等）
├── config/                         # 模块配置目录（存放模块专属配置，无则空目录）
│   ├── __init__.py
│   └── config.py                   # 配置文件（读取基础配置，可添加模块专属配置）
├── tests/                          # 测试目录（存放模块单元测试、集成测试用例，必须包含）
│   ├── __init__.py
│   ├── test_base.py                # 抽象类测试用例（可选，初学者可简化）
│   └── test_impl.py                # 具体实现类测试用例（必须包含，覆盖核心功能）
└── README.md                       # 模块说明文档（必须包含，说明模块功能、接口、使用方法，适配初学者）
```

## 4.1 目录结构说明

- state_store_module：模块根目录，名称严格遵循“全小写、多单词下划线连接”规则，与模块功能对应。

- __init__.py：每个目录必须包含，根目录的__init__.py需暴露模块核心类/方法（如from .core.impl import LocalStateStore），方便其他模块调用。

- core目录：核心逻辑存放处，base.py定义抽象基类（ABC），明确模块必须实现的接口方法；impl.py是具体实现类，继承base.py的抽象类，实现所有抽象方法。

- utils目录：模块专属工具函数，如状态数据格式校验、存储路径处理等，不包含核心业务逻辑，仅为模块提供辅助。

- config目录：模块专属配置，如状态存储目录、过期时间等，可读取基础支撑层的全局配置，补充模块专属配置。

- tests目录：测试用例存放处，必须包含test_impl.py，覆盖模块核心功能（save_state、get_state等）的单元测试，初学者可参考示例编写简单测试场景。

- README.md：模块说明文档，需详细说明模块功能、核心接口、使用方法、依赖项、常见问题，语言简洁易懂，适配初学者。

# 5. 核心接口设计（抽象基类）

抽象基类（core/base.py）定义模块核心接口，强制所有具体实现类必须实现以下方法，保障模块的一致性与可替换性。

```python
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional, List


class BaseStateStore(ABC):
    """状态存储抽象基类，定义Agent状态存储核心接口，所有具体实现类需继承此类并实现所有抽象方法"""

    @abstractmethod
    def save_state(self, session_id: str, state: Dict[str, Any]) -> bool:
        """
        保存指定会话的Agent状态，若会话已存在则覆盖原有状态
        :param session_id: 会话唯一标识（str类型，不可为空）
        :param state: 会话状态数据（Dict类型，存储Agent运行相关的所有状态信息）
        :return: 保存成功返回True，失败返回False
        """
        pass

    @abstractmethod
    def get_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        """
        读取指定会话的Agent状态
        :param session_id: 会话唯一标识（str类型）
        :return: 会话状态数据（Dict类型），若会话不存在或读取失败则返回None
        """
        pass

    @abstractmethod
    def append_event(self, session_id: str, event: Dict[str, Any]) -> bool:
        """
        向指定会话的状态中追加事件记录，用于跟踪Agent任务执行过程
        :param session_id: 会话唯一标识（str类型）
        :param event: 事件记录（Dict类型，包含事件类型、事件数据、时间戳等信息）
        :return: 追加成功返回True，失败返回False
        """
        pass

    @abstractmethod
    def clear_state(self, session_id: str) -> bool:
        """
        清理指定会话的状态数据，释放存储资源
        :param session_id: 会话唯一标识（str类型）
        :return: 清理成功返回True，失败返回False
        """
        pass
```

# 6. 具体实现类基础构建

具体实现类（core/impl.py）继承抽象基类BaseStateStore，实现所有抽象方法，以下为基础构建代码（不包含具体方法实现逻辑），开发者可根据实际存储方式（本地文件、数据库等）完善实现。

```python
import os
import json
from typing import Dict, Any, Optional
from .base import BaseStateStore
from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from exception_module.core.impl import VectorDBException


class LocalStateStore(BaseStateStore):
    """本地状态存储实现（示例），基于JSON文件存储会话状态，适用于开发/测试环境"""

    def __init__(self):
        """初始化方法：加载配置、初始化日志、创建存储目录"""
        self.config = ConfigManager()
        self.config.load_config()
        self.logger = SystemLogger()

        # 从配置中读取状态存储目录，默认值为"state_store"
        self.store_dir = self.config.get_config("state_store.dir", "state_store")
        
        # 若存储目录不存在，则创建
        if not os.path.exists(self.store_dir):
            os.makedirs(self.store_dir)

    def _get_state_path(self, session_id: str) -> str:
        """
        私有方法：获取指定会话的状态文件路径
        :param session_id: 会话唯一标识
        :return: 状态文件路径（str类型）
        """
        return os.path.join(self.store_dir, f"{session_id}.json")

    def save_state(self, session_id: str, state: Dict[str, Any]) -> bool:
        """实现抽象方法：保存会话状态（具体实现逻辑由开发者完善）"""
        pass

    def get_state(self, session_id: str) -> Optional[Dict[str, Any]]:
        """实现抽象方法：读取会话状态（具体实现逻辑由开发者完善）"""
        pass

    def append_event(self, session_id: str, event: Dict[str, Any]) -> bool:
        """实现抽象方法：追加会话事件（具体实现逻辑由开发者完善）"""
        pass

    def clear_state(self, session_id: str) -> bool:
        """实现抽象方法：清理会话状态（具体实现逻辑由开发者完善）"""
        pass
```

# 7. 配置规范

## 7.1 配置内容

本模块配置需包含存储目录、状态过期时间（可选）等核心参数，配置文件（config/config.yaml）示例如下，可根据实际需求扩展。

```yaml
state_store:
  dir: "state_store"          # 状态存储目录（默认值，可通过环境变量覆盖）
  expire_hours: 24            # 会话状态过期时间（可选，单位：小时，过期后自动清理）
  max_size: 1073741824        # 存储目录最大容量（可选，单位：字节，默认1GB）
```

## 7.2 配置加载

配置加载需依赖基础支撑层的配置管理模块（ConfigManager），通过调用ConfigManager的get_config方法读取配置参数，支持通过环境变量覆盖配置文件中的值（遵循系统配置分离规范）。

# 8. 测试规范

## 8.1 测试用例要求

测试用例（tests/test_impl.py）需覆盖模块核心功能，至少包含以下场景，确保模块功能正常：

- save_state：正常保存状态、重复保存（覆盖）、异常会话ID（空值、特殊字符）场景。

- get_state：正常读取状态、读取不存在的会话、读取异常状态文件场景。

- append_event：正常追加事件、向不存在的会话追加事件、追加异常事件数据场景。

- clear_state：正常清理状态、清理不存在的会话、清理正在使用的会话场景。

## 8.2 测试用例基础代码

```python
import unittest
from state_store_module.core.impl import LocalStateStore


class TestLocalStateStore(unittest.TestCase):
    """状态存储模块具体实现类的单元测试用例，覆盖核心功能场景"""

    def setUp(self):
        """测试前置：初始化LocalStateStore实例"""
        self.state_store = LocalStateStore()
        self.test_session_id = "test_session_001"
        self.test_state = {"task": "测试任务", "steps": [], "events": []}
        self.test_event = {"type": "tool_call", "data": {"tool": "rag_search", "result": "success"}, "timestamp": "2026-02-27"}

    def test_save_and_get_state(self):
        """测试：正常保存并读取状态"""
        # 保存状态
        save_result = self.state_store.save_state(self.test_session_id, self.test_state)
        self.assertTrue(save_result)
        
        # 读取状态
        get_result = self.state_store.get_state(self.test_session_id)
        self.assertIsNotNone(get_result)
        self.assertEqual(get_result["task"], self.test_state["task"])

    def test_append_event(self):
        """测试：追加事件并验证"""
        # 先保存基础状态
        self.state_store.save_state(self.test_session_id, self.test_state)
        # 追加事件
        append_result = self.state_store.append_event(self.test_session_id, self.test_event)
        self.assertTrue(append_result)
        # 验证事件已追加
        state = self.state_store.get_state(self.test_session_id)
        self.assertEqual(len(state["events"]), 1)
        self.assertEqual(state["events"][0]["type"], self.test_event["type"])

    def test_clear_state(self):
        """测试：清理状态并验证"""
        # 先保存状态
        self.state_store.save_state(self.test_session_id, self.test_state)
        # 清理状态
        clear_result = self.state_store.clear_state(self.test_session_id)
        self.assertTrue(clear_result)
        # 验证状态已清理
        get_result = self.state_store.get_state(self.test_session_id)
        self.assertIsNone(get_result)


if __name__ == "__main__":
    unittest.main()
```

# 9. 集成规范

## 9.1 依赖模块

本模块依赖基础支撑层的以下模块，开发与集成时需确保这些模块已完成开发并正常引入：

- 配置管理模块（config_module）：用于加载模块配置。

- 日志模块（log_module）：用于记录模块操作日志与错误信息。

- 异常处理模块（exception_module）：用于统一处理模块运行过程中的异常。

## 9.2 对外接口

本模块对外提供的核心接口为抽象基类BaseStateStore定义的4个方法，其他模块（主要是Agent模块）通过调用这些接口实现状态的操作，调用时需注入具体实现类实例（如LocalStateStore），确保模块可替换。

## 9.3 集成示例

```python
# 模块集成示例（Agent模块中调用状态存储模块）
from state_store_module.core.impl import LocalStateStore

# 初始化状态存储实例
state_store = LocalStateStore()

# 保存Agent状态
session_id = "agent_session_001"
agent_state = {"task": "整理知识库要点", "steps": ["解析任务", "调用RAG检索"], "events": []}
state_store.save_state(session_id, agent_state)

# 追加事件
event = {"type": "task_parse", "data": {"plan": [{"tool": "rag_search"}]}, "timestamp": "2026-02-27"}
state_store.append_event(session_id, event)

# 读取状态
current_state = state_store.get_state(session_id)
print("当前Agent状态：", current_state)

# 清理状态
state_store.clear_state(session_id)
```

# 10. 开发与交付规范

## 10.1 交付物清单

本模块开发完成后，需提交以下交付物，确保符合系统整体交付标准：

- core/base.py：抽象基类（ABC）文件，定义核心接口。

- core/impl.py：具体实现类文件，继承抽象基类并实现所有方法。

- tests/test_impl.py：单元测试用例文件，覆盖核心功能。

- config/config.yaml：模块配置文件，包含必要配置参数。

- README.md：模块说明文档，详细说明模块功能、接口、使用方法。

- requirements.txt：模块依赖包清单，明确依赖包名称与版本号。

## 10.2 可替换性约束

- 其他模块只能依赖本模块的抽象基类（BaseStateStore），禁止直接引用具体实现类（如LocalStateStore）的内部私有方法。

- 替换状态存储实现（如从本地文件存储改为数据库存储）时，只需实现BaseStateStore抽象类，无需修改其他模块代码，确保模块可替换。

# 11. 常见问题与注意事项

- 会话ID需保证唯一，避免不同会话的状态数据相互覆盖。

- 状态数据存储时需注意数据格式校验，避免非法数据导致存储或读取失败。

- 若使用本地文件存储，需注意存储目录的权限，确保模块有读写权限。

- 模块运行过程中若出现异常，需抛出系统统一格式的异常，便于异常处理模块统一捕获与处理。

- 测试时需模拟各种异常场景（如存储目录不可写、会话ID为空），确保模块稳定性。

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
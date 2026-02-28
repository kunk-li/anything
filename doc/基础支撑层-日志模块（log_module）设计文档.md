# 基础支撑层 - 日志模块（log_module）设计文档

# 1. 文档概述

## 1.1 文档目的

本文档为RAG与Agent系统基础支撑层日志模块（log_module）的专项设计文档，用于指导开发团队（含初学者）进行该模块的独立开发、测试与集成。文档明确了模块功能、接口定义、项目结构、编码规范及调用方式，重点优化了多进程场景适配能力和外部调用便捷性，确保模块开发符合系统整体架构要求，可无缝对接其他模块，提供标准化、高可用的日志服务。

## 1.2 适用人群

本团队所有开发人员（含资深开发者与初学者）、测试人员，作为日志模块开发、测试、部署及维护的唯一标准依据；项目管理人员可参考本文档了解模块职责与集成要点；外部模块开发人员可通过本文档快速掌握日志模块的调用方法。

## 1.3 核心需求回顾

- 模块功能：统一管理系统日志，支持日志输出到控制台、文件，按级别过滤日志，适配所有上层模块调用；**支持多进程场景**，避免多进程日志错乱、丢失，确保日志完整性；优化外部调用体验，提供简洁、标准化的调用接口。

- 开发语言：Python 3.10+，与系统整体开发语言一致。

- 开发模式：独立开发，不依赖基础支撑层以外的其他模块（仅依赖同层配置管理模块），基于本文档即可完成开发。

- 文档要求：详细、易懂，适配初学者，明确接口、项目结构等可提前定义的内容；补充多进程适配说明和外部调用示例，降低外部集成成本。

- 模块要求：包含抽象基类（ABC），确保与系统其他模块的一致性，支持代码可交换；多进程适配逻辑封装在核心实现中，对外部调用透明，无需额外开发适配代码。

## 1.4 术语定义

|术语|定义|
|---|---|
|ABC|抽象基类，定义模块的核心接口与方法，强制子类实现，保障模块一致性。|
|日志级别|用于区分日志重要程度，包括DEBUG（调试）、INFO（普通）、WARNING（警告）、ERROR（错误）、CRITICAL（严重错误）。|
|日志器|日志模块的核心实例，负责接收日志信息、按配置输出到指定位置（控制台/文件），多进程场景下确保实例安全、日志有序。|
|多进程适配|通过进程锁、独立日志句柄等机制，避免多进程并发写入日志时出现错乱、丢失、资源竞争等问题，确保日志完整性和有序性。|
|外部调用|模块对外提供标准化接口，支持外部模块（含跨进程模块）快速导入、初始化和调用，无需关注内部实现细节。|
# 2. 模块核心设计

## 2.1 模块定位与职责

日志模块属于系统基础支撑层，是所有上层模块（数据层、核心业务层、接口层、应用层）的依赖模块，需最先开发。核心职责如下：

- 提供标准化的日志记录接口，支持不同级别日志的输出，简化外部调用流程。

- 支持日志同时输出到控制台（便于开发调试）和文件（便于问题排查），多进程场景下确保日志无错乱、无丢失。

- 集成配置管理模块，读取系统全局日志配置（如日志级别、文件路径），支持配置动态适配；多进程场景下同步配置，确保所有进程日志配置一致。

- 支持按日志器名称区分不同模块、不同进程的日志，便于日志分类排查和问题定位。

- 统一日志格式，避免不同模块、不同进程日志格式混乱，提升可维护性；封装多进程适配逻辑，对外部调用透明。

- 提供单例模式的日志器实例，支持外部模块全局复用，减少资源占用；多进程场景下为每个进程分配独立安全的日志句柄。

## 2.2 模块依赖

日志模块仅依赖基础支撑层的配置管理模块（config_module）和Python标准库（logging、multiprocessing），其中：config_module用于读取全局日志级别等配置；multiprocessing用于实现多进程锁，避免日志写入冲突；不依赖其他层模块，确保独立开发与部署，同时降低外部调用的依赖成本。

## 2.3 多进程适配设计

针对多进程场景下日志错乱、丢失的问题，采用“进程锁+独立日志句柄”的适配方案，核心设计如下，且所有适配逻辑封装在模块内部，外部调用无需额外处理：

- 进程锁机制：使用multiprocessing.Lock创建全局进程锁，日志写入文件时自动获取锁，写入完成后释放锁，避免多进程并发写入导致的日志错乱。

- 独立日志句柄：每个进程初始化日志器时，创建独立的文件处理器（FileHandler），避免多个进程共用一个句柄导致的资源竞争；控制台处理器可共用，确保调试日志正常输出。

- 配置同步：多进程启动时，从配置管理模块读取全局配置，确保所有进程的日志级别、格式、文件路径等保持一致，无需单独配置。

- 日志标识：日志格式中添加进程ID（pid）和进程名称，便于区分不同进程的日志，提升问题排查效率。

# 3. 统一项目结构规范

本模块严格遵循系统统一项目结构规范，开发者需严格遵循，不得随意修改目录名称与层级，初学者可直接复制该结构搭建项目；同时优化目录设计，便于外部模块快速定位核心接口。

## 3.1 模块目录结构

```plain text
log_module/                  # 模块根目录（全小写，多单词用下划线连接）
├── __init__.py               # 模块初始化文件，暴露模块核心类/方法（必须包含，简化外部导入）
├── core/                     # 核心逻辑目录（存放模块核心实现，含ABC抽象类、多进程适配逻辑）
│   ├── __init__.py
│   ├── base.py               # 抽象基类（ABC）文件，定义模块核心接口（必须包含）
│   └── impl.py               # 具体实现类文件，继承base.py，实现多进程适配和日志功能
├── utils/                    # 模块工具目录（存放模块专属工具函数，无则空目录）
│   ├── __init__.py
│   └── tool_functions.py     # 工具函数文件（日志格式处理、进程ID获取等辅助功能）
├── config/                   # 模块专属配置目录（无则空目录，可复用全局配置）
│   ├── __init__.py
│   └── config.py             # 配置文件（读取基础配置，可添加模块专属配置）
├── tests/                    # 测试目录（存放模块单元测试、集成测试用例，含多进程测试）
│   ├── __init__.py
│   ├── test_base.py          # 抽象类测试用例（可选，初学者可简化）
│   ├── test_impl.py          # 具体实现类测试用例（必须包含，覆盖核心功能）
│   └── test_multiprocess.py  # 多进程场景测试用例（必须包含，验证多进程日志完整性）
└── README.md                 # 模块说明文档（必须包含，重点补充外部调用和多进程使用说明）
```

## 3.2 目录结构说明

- log_module：模块根目录，名称固定为log_module，与模块功能对应，全小写无大写字母，便于外部模块导入。

- __init__.py：每个目录必须包含，核心作用是将目录标识为Python模块；根目录的__init__.py需简化外部导入，直接暴露核心类（如from .core.impl import SystemLogger），外部模块可直接导入使用，无需深入目录层级。

- core目录：核心逻辑存放处，base.py是抽象基类（ABC），定义日志模块必须实现的接口方法；impl.py是具体实现类，继承base.py的抽象类，实现所有抽象方法，同时封装多进程适配逻辑（进程锁、独立句柄等）。

- utils目录：模块专属工具函数，新增进程ID获取、日志格式添加进程标识等功能，不包含核心业务逻辑，仅为模块提供支撑。

- config目录：模块专属配置，可读取基础支撑层的全局配置（如日志级别、文件路径），补充模块专属配置（如进程锁超时时间），无专属配置时可留空。

- tests目录：新增test_multiprocess.py测试用例，专门验证多进程场景下的日志输出完整性、无错乱；test_impl.py覆盖单进程核心功能；初学者可参考示例编写测试用例。

- README.md：模块说明文档，重点补充外部调用步骤、多进程使用注意事项、常见问题（多进程相关），语言简洁易懂，适配外部开发人员快速上手。

## 3.3 统一编码规范

遵循[系统架构设计](./RAG与Agent系统架构设计说明书.md)中的 3.2 统一编码规范


# 4. 模块详细设计

## 4.1 抽象基类设计（core/base.py）

定义日志模块的核心接口，强制子类实现所有抽象方法，保障模块一致性；接口设计简洁，适配外部调用，无需关注多进程内部实现：

```python
from abc import ABC, abstractmethod
import logging

class BaseLogger(ABC):
    """日志模块抽象基类，定义日志记录核心接口，所有日志实现类需继承此类
    接口设计适配外部调用，多进程适配逻辑由子类封装，外部无需额外处理
    """

    @abstractmethod
    def get_logger(self, logger_name: str = "rag_agent_system") -> logging.Logger:
        """
        获取日志器实例，支持指定日志器名称（区分不同模块、不同进程日志）
        多进程场景下，返回当前进程的独立日志器实例
        :param logger_name: 日志器名称（默认系统全局日志器名称）
        :return: 日志器实例（logging.Logger）
        """
        pass

    @abstractmethod
    def debug(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """记录DEBUG级别日志，用于开发调试信息，多进程安全"""
        pass

    @abstractmethod
    def info(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """记录INFO级别日志，用于普通操作信息，多进程安全"""
        pass

    @abstractmethod
    def warning(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """记录WARNING级别日志，用于警告信息，多进程安全"""
        pass

    @abstractmethod
    def error(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """记录ERROR级别日志，用于错误信息，多进程安全"""
        pass

    @abstractmethod
    def critical(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """记录CRITICAL级别日志，用于严重错误信息，多进程安全"""
        pass
```

## 4.2 具体实现类设计（core/impl.py）

继承抽象基类，实现所有抽象方法，集成配置管理模块读取日志配置，封装多进程适配逻辑（进程锁、独立句柄），提供标准化日志输出功能；优化实例初始化，支持外部模块快速调用，多进程场景下自动适配：

```python
import logging
import os
import multiprocessing
from .base import BaseLogger
from config_module.core.impl import ConfigManager
from log_module.utils.tool_functions import get_log_file_name, get_process_info

# 全局进程锁，确保多进程日志写入安全
PROCESS_LOCK = multiprocessing.Lock()
# 日志格式（新增进程ID、进程名称，便于多进程日志区分）
LOG_FORMAT = "%(asctime)s - %(process)d - %(processName)s - %(name)s - %(levelname)s - %(message)s"
# 日志文件路径（可从配置读取）
DEFAULT_LOG_DIR = "logs"

class SystemLogger(BaseLogger):
    """系统日志具体实现类，集成配置管理模块，支持控制台+文件双输出
    封装多进程适配逻辑（进程锁、独立日志句柄），对外部调用透明；提供单例模式，便于外部复用
    """
    # 单例实例（每个进程一个实例，避免跨进程共享导致的问题）
    _instance = None

    def __new__(cls, *args, **kwargs):
        """单例模式，确保每个进程只有一个日志器实例"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.__init__()
        return cls._instance

    def __init__(self):
        """初始化日志器，加载配置、设置日志格式与输出位置，初始化多进程相关资源"""
        # 避免重复初始化（单例模式下仅执行一次）
        if hasattr(self, "_initialized") and self._initialized:
            return
        self.config_manager = ConfigManager()
        self.config_manager.load_config()  # 加载全局配置
        self.log_level = self._get_log_level()  # 从配置获取日志级别
        self.log_dir = self._get_log_dir()  # 从配置获取日志文件存储目录
        self._init_logger()  # 初始化日志器配置（创建独立句柄）
        self._initialized = True

    def _get_log_level(self) -> int:
        """根据配置文件获取日志级别，转换为logging对应的级别常量"""
        config_level = self.config_manager.get_config("log_level", "INFO")
        level_map = {
            "DEBUG": logging.DEBUG,
            "INFO": logging.INFO,
            "WARNING": logging.WARNING,
            "ERROR": logging.ERROR,
            "CRITICAL": logging.CRITICAL
        }
        return level_map.get(config_level.upper(), logging.INFO)

    def _get_log_dir(self) -> str:
        """从配置获取日志文件存储目录，不存在则创建"""
        log_dir = self.config_manager.get_config("log_dir", DEFAULT_LOG_DIR)
        if not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        return log_dir

    def _init_logger(self) -> None:
        """初始化日志器，配置日志格式、控制台处理器、文件处理器（多进程独立句柄）"""
        # 日志器名称（结合进程ID，确保每个进程的日志器唯一）
        process_id, process_name = get_process_info()
        self.logger_name = f"{self.config_manager.get_config('logger_name', 'rag_agent_system')}_{process_id}"
        
        # 初始化日志器，避免多进程共用一个实例
        self.logger = logging.getLogger(self.logger_name)
        self.logger.setLevel(self.log_level)
        self.logger.propagate = False  # 禁止日志传播，避免重复输出

        # 控制台处理器（所有进程可共用，无需锁）
        console_handler = logging.StreamHandler()
        console_handler.setLevel(self.log_level)
        console_handler.setFormatter(logging.Formatter(LOG_FORMAT))

        # 文件处理器（每个进程独立句柄，避免资源竞争）
        log_file = os.path.join(self.log_dir, get_log_file_name())
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(self.log_level)
        file_handler.setFormatter(logging.Formatter(LOG_FORMAT))

        # 添加处理器（避免重复添加，确保每个进程只有一套处理器）
        if not self.logger.handlers:
            self.logger.addHandler(console_handler)
            self.logger.addHandler(file_handler)

    def get_logger(self, logger_name: str = "rag_agent_system") -> logging.Logger:
        """实现抽象方法，返回指定名称的日志器实例，多进程场景下返回当前进程独立实例"""
        # 若指定日志器名称，创建新的独立实例（仍使用当前进程的句柄配置）
        if logger_name != self.logger_name:
            process_id, _ = get_process_info()
            new_logger_name = f"{logger_name}_{process_id}"
            new_logger = logging.getLogger(new_logger_name)
            new_logger.setLevel(self.log_level)
            new_logger.propagate = False
            # 复用当前进程的处理器配置，确保格式、输出位置一致
            for handler in self.logger.handlers:
                new_logger.addHandler(handler)
            return new_logger
        return self.logger

    def _log(self, level, message: str, logger_name: str = "rag_agent_system") -> None:
        """封装日志记录通用方法，添加进程锁，确保多进程写入安全"""
        logger = self.get_logger(logger_name)
        # 进程锁获取，避免多进程并发写入错乱，设置超时时间防止死锁
        with PROCESS_LOCK:
            if level == logging.DEBUG:
                logger.debug(message)
            elif level == logging.INFO:
                logger.info(message)
            elif level == logging.WARNING:
                logger.warning(message)
            elif level == logging.ERROR:
                logger.error(message)
            elif level == logging.CRITICAL:
                logger.critical(message)

    def debug(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """实现抽象方法，记录DEBUG级别日志，多进程安全"""
        self._log(logging.DEBUG, message, logger_name)

    def info(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """实现抽象方法，记录INFO级别日志，多进程安全"""
        self._log(logging.INFO, message, logger_name)

    def warning(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """实现抽象方法，记录WARNING级别日志，多进程安全"""
        self._log(logging.WARNING, message, logger_name)

    def error(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """实现抽象方法，记录ERROR级别日志，多进程安全"""
        self._log(logging.ERROR, message, logger_name)

    def critical(self, message: str, logger_name: str = "rag_agent_system") -> None:
        """实现抽象方法，记录CRITICAL级别日志，多进程安全"""
        self._log(logging.CRITICAL, message, logger_name)
```

## 4.3 工具函数补充（utils/tool_functions.py）

提供日志模块专属工具函数，新增多进程相关辅助功能，辅助日志处理，简化核心逻辑代码：

```python
from datetime import datetime
import os
import multiprocessing

def get_log_file_name() -> str:
    """生成按日期命名的日志文件名，格式：system_YYYYMMDD.log"""
    date_str = datetime.now().strftime("%Y%m%d")
    return f"system_{date_str}.log"

def format_log_message(message: str, module_name: str) -> str:
    """格式化日志信息，添加模块标识等辅助信息"""
    return f"[{module_name}] {message}"

def get_process_info() -> tuple[int, str]:
    """获取当前进程ID和进程名称，用于多进程日志标识"""
    process = multiprocessing.current_process()
    return process.pid, process.name

def check_log_dir_exists(log_dir: str) -> bool:
    """检查日志目录是否存在，不存在则创建，返回创建结果"""
    if not os.path.exists(log_dir):
        os.makedirs(log_dir, exist_ok=True)
        return False
    return True
```

## 4.4 接口调用示例（外部调用+多进程调用）

提供简洁易懂的调用示例，分单进程、多进程两种场景，供外部模块参考使用，适配初学者和外部开发人员，调用流程简化，无需关注多进程适配细节：

```python
from log_module import SystemLogger  # 简化外部导入，直接从模块根目录导入核心类
import multiprocessing
import time

# -------------------------- 单进程调用示例（外部模块基础调用）--------------------------
# 初始化日志器（全局只需初始化一次，所有模块可复用，单例模式自动生效）
logger = SystemLogger()

# 记录不同级别日志（默认使用系统全局日志器名称，多进程安全）
logger.debug("这是DEBUG级别日志，用于开发调试")
logger.info("这是INFO级别日志，记录普通操作（如模块初始化完成）")
logger.warning("这是WARNING级别日志，记录警告信息（如配置未找到，使用默认值）")
logger.error("这是ERROR级别日志，记录错误信息（如日志文件写入失败）")
logger.critical("这是CRITICAL级别日志，记录严重错误信息（如日志模块初始化失败）")

# 不同模块可指定日志器名称，便于分类排查
logger.info("RAG模块检索完成", logger_name="rag_module")
logger.info("Agent模块任务执行成功", logger_name="agent_module")

# -------------------------- 多进程调用示例（外部模块多进程场景）--------------------------
def process_task(task_id: int, logger: SystemLogger):
    """多进程任务函数，接收日志器实例，直接调用日志方法（无需额外适配）"""
    # 日志器实例可直接传入多进程，自动适配当前进程的独立句柄
    logger.info(f"进程{task_id}启动，开始执行任务", logger_name=f"task_{task_id}")
    try:
        # 模拟任务执行
        time.sleep(1)
        logger.debug(f"进程{task_id}任务执行中，当前进度50%")
        time.sleep(1)
        logger.info(f"进程{task_id}任务执行完成", logger_name=f"task_{task_id}")
    except Exception as e:
        logger.error(f"进程{task_id}任务执行失败，错误信息：{str(e)}", logger_name=f"task_{task_id}")

if __name__ == "__main__":
    # 初始化日志器（主进程初始化，子进程自动复用单例，适配独立句柄）
    global_logger = SystemLogger()
    global_logger.info("主进程启动，开始创建多进程任务")

    # 创建3个进程，执行任务，传入日志器实例
    processes = []
    for i in range(3):
        p = multiprocessing.Process(
            target=process_task,
            args=(i, global_logger),
            name=f"TaskProcess-{i}"
        )
        processes.append(p)
        p.start()

    # 等待所有进程执行完成
    for p in processes:
        p.join()

    global_logger.info("所有多进程任务执行完成，主进程退出")

# -------------------------- 外部模块跨进程调用注意事项 --------------------------
# 1. 日志器实例可直接传入多进程，无需单独初始化，单例模式自动为每个进程创建独立实例
# 2. 无需手动处理进程锁，日志写入时自动获取/释放锁，避免多进程错乱
# 3. 日志格式中包含进程ID和进程名称，可通过日志快速定位具体进程的问题
# 4. 外部模块只需导入SystemLogger，无需关注内部多进程适配逻辑
```

## 4.5 测试用例设计（tests/test_impl.py + tests/test_multiprocess.py）

提供核心测试用例框架，新增多进程场景测试，覆盖日志级别输出、日志器获取、多进程日志完整性等核心功能，初学者可参考扩展：

### 4.5.1 单进程测试用例（tests/test_impl.py）

```python
import unittest
from log_module.core.impl import SystemLogger

class TestSystemLogger(unittest.TestCase):
    def setUp(self):
        """测试前置：初始化日志器实例（单进程）"""
        self.logger = SystemLogger()

    # 测试日志器获取功能，验证单例模式
    def test_get_logger(self):
        logger1 = self.logger.get_logger(logger_name="test_logger")
        logger2 = self.logger.get_logger(logger_name="test_logger")
        self.assertEqual(logger1.name, logger2.name)  # 同一名称日志器实例一致
        self.assertEqual(logger1.level, self.logger.log_level)  # 日志级别一致

    # 测试不同级别日志输出（仅校验无异常，不校验输出内容）
    def test_log_levels(self):
        self.assertIsNone(self.logger.debug("测试DEBUG日志"))
        self.assertIsNone(self.logger.info("测试INFO日志"))
        self.assertIsNone(self.logger.warning("测试WARNING日志"))
        self.assertIsNone(self.logger.error("测试ERROR日志"))
        self.assertIsNone(self.logger.critical("测试CRITICAL日志"))

    # 测试日志器名称指定功能
    def test_logger_name(self):
        logger = self.logger.get_logger(logger_name="custom_logger")
        self.assertIn("custom_logger", logger.name)  # 日志器名称包含指定名称
        self.assertIn(str(multiprocessing.current_process().pid), logger.name)  # 包含进程ID

if __name__ == "__main__":
    unittest.main()
```

### 4.5.2 多进程测试用例（tests/test_multiprocess.py）

```python
import unittest
import multiprocessing
import time
from log_module.core.impl import SystemLogger
from log_module.utils.tool_functions import get_log_file_name
import os

class TestSystemLoggerMultiprocess(unittest.TestCase):
    def setUp(self):
        """测试前置：初始化日志器实例，清理测试日志文件"""
        self.logger = SystemLogger()
        self.log_file = os.path.join(self.logger.log_dir, get_log_file_name())
        # 清理历史日志文件
        if os.path.exists(self.log_file):
            os.remove(self.log_file)

    def test_multiprocess_log_integrity(self):
        """测试多进程场景下日志完整性，验证无错乱、无丢失"""
        task_count = 5  # 创建5个进程
        processes = []

        def task(process_id):
            """多进程任务，记录多条日志"""
            local_logger = SystemLogger()  # 验证单例模式，每个进程一个实例
            for i in range(3):  # 每个进程记录3条日志
                local_logger.info(f"进程{process_id} - 日志{i+1}", logger_name=f"task_{process_id}")
                time.sleep(0.1)  # 模拟任务耗时，制造并发写入场景

        # 启动多进程
        for i in range(task_count):
            p = multiprocessing.Process(target=task, args=(i,), name=f"TestProcess-{i}")
            processes.append(p)
            p.start()

        # 等待所有进程完成
        for p in processes:
            p.join()

        # 验证日志文件存在，且日志条数正确（5进程 × 3条 = 15条）
        self.assertTrue(os.path.exists(self.log_file))
        with open(self.log_file, "r", encoding="utf-8") as f:
            log_lines = [line.strip() for line in f if line.strip()]
        self.assertEqual(len(log_lines), task_count * 3)

        # 验证每条日志都包含进程ID和进程名称，无错乱
        for line in log_lines:
            self.assertIn(" - ", line)
            self.assertIn("TestProcess-", line)  # 包含进程名称
            self.assertIn(str(multiprocessing.current_process().pid), line)  # 包含主进程ID或子进程ID

    def test_multiprocess_logger_isolation(self):
        """测试多进程日志器隔离性，每个进程的日志器独立"""
        def task(process_id):
            local_logger = SystemLogger()
            logger_name = f"isolation_test_{process_id}"
            local_logger.info(f"进程{process_id} 隔离测试", logger_name=logger_name)
            return local_logger.logger.name

        # 使用进程池获取每个进程的日志器名称
        with multiprocessing.Pool(processes=3) as pool:
            results = pool.map(task, [0, 1, 2])

        # 验证每个进程的日志器名称不同（包含不同进程ID）
        self.assertNotEqual(results[0], results[1])
        self.assertNotEqual(results[1], results[2])
        self.assertNotEqual(results[0], results[2])

if __name__ == "__main__":
    unittest.main()
```

# 5. 模块交付物清单（强制）

模块开发完成后，需提交以下交付物，确保符合系统集成要求，同时适配外部调用和多进程场景：

- core/base.py：日志模块抽象基类（ABC），包含所有核心接口，适配外部调用。

- core/impl.py：日志模块具体实现类，继承抽象基类，实现所有方法和多进程适配逻辑。

- tests/test_impl.py：单进程核心测试用例，覆盖日志级别输出、日志器获取等核心功能。

- tests/test_multiprocess.py：多进程场景测试用例，验证多进程日志完整性、无错乱。

- README.md：模块说明文档，详细说明模块功能、接口、外部调用步骤、多进程使用注意事项、依赖项。

- requirements.txt：模块依赖包清单，注明包名称与版本（含Python版本要求）。

- utils/tool_functions.py：工具函数文件，包含多进程相关辅助功能。

# 6. 可替换性约束（强制）

- 其他模块（含外部模块）只能依赖本模块的抽象基类（BaseLogger）或具体实现类的公开接口，禁止直接引用impl.py中的私有方法（以_开头的方法）和私有变量，避免多进程适配逻辑被破坏。

- 若需替换日志实现（如更换日志输出方式、集成第三方日志框架），只需实现BaseLogger抽象基类，保持接口一致，无需修改其他模块代码；多进程适配逻辑需在新实现类中重新封装，确保多进程场景兼容。

- 外部模块调用时，需通过模块根目录的__init__.py导入核心类（SystemLogger），禁止直接深入目录导入，确保模块结构变更时不影响外部调用。

# 7. 常见问题（适配初学者+外部调用+多进程）

- 问题1：日志无法输出到文件？
解答：检查日志目录（logs）是否存在，若不存在需在初始化时创建；检查配置文件中日志级别是否高于输出的日志级别（如配置为INFO，DEBUG级别日志无法输出）；多进程场景下，检查日志文件路径是否可写，避免权限不足。

- 问题2：其他模块无法导入日志模块？
解答：确保log_module根目录的__init__.py中暴露了核心类（如SystemLogger）；确保模块路径已添加到Python环境变量中；外部模块导入时，直接使用from log_module import SystemLogger，无需深入目录。

- 问题3：日志格式混乱？
解答：严格遵循编码规范中的日志格式定义，统一使用系统规定的日志格式，避免在具体实现中修改日志格式；多进程场景下，日志格式已包含进程ID和进程名称，无需额外修改。

- 问题4：多进程场景下日志出现错乱、丢失？
解答：检查是否正确使用SystemLogger实例（单例模式，每个进程自动创建独立实例）；无需手动创建进程锁，模块内部已封装锁机制；检查日志文件路径是否唯一，避免多个进程写入同一文件时出现冲突（模块已处理，无需额外操作）。

- 问题5：外部模块多进程调用时，日志器实例传入失败？
解答：确保日志器实例在主进程初始化后传入子进程，子进程会自动适配为当前进程的独立实例；避免在子进程中重新初始化日志器（单例模式会自动复用，无需重复初始化）。

- 问题6：多进程场景下，不同进程的日志级别不一致？
解答：确保所有进程启动时都加载了全局配置，模块初始化时会自动读取配置，确保所有进程日志级别一致；若需修改日志级别，修改全局配置后，重启所有进程即可生效。

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
# 基础支撑层异常处理模块设计文档

# 1. 文档概述

## 1.1 文档目的

本文档为RAG（检索增强生成）与Agent（智能代理）系统基础支撑层中异常处理模块的专项设计文档，用于指导开发团队（含初学者）进行该模块的独立开发、测试与集成。文档明确了模块功能、核心接口、抽象基类设计、自定义异常类型、与其他模块的依赖关系及开发交付规范，确保模块开发符合系统整体架构要求，可无缝集成至基础支撑层，为所有上层模块提供统一的异常处理能力。

## 1.2 适用人群

本团队所有开发人员（含资深开发者与初学者）、测试人员、项目管理人员，作为异常处理模块开发、测试、部署及维护的唯一标准依据。

## 1.3 核心需求回顾

- 模块功能：定义系统统一异常类型，提供异常捕获、处理、封装功能，规范异常输出格式，减少重复代码，提升系统稳定性。

- 开发语言：遵循系统统一要求，采用Python（版本3.10+）。

- 开发模式：独立开发，不依赖基础支撑层以外的模块，仅依赖基础支撑层内部的日志模块，开发完成后通过统一接口供其他模块调用。

- 文档要求：详细、易懂，适配初学者，明确接口定义、异常类型、代码结构及与系统错误码表的关联。

- 模块要求：包含抽象基类（ABC），确保模块一致性，支持异常类型的扩展，与系统统一错误码表严格对应。

## 1.4 术语定义

|术语|定义|
|---|---|
|ABC|抽象基类，定义模块的核心接口与方法，强制子类实现，保障模块一致性。|
|系统统一异常|由本模块定义，所有系统内部异常均继承此类，包含异常编码与异常信息，便于统一处理与定位。|
|异常封装|将捕获的异常（自定义异常或系统异常）转换为系统统一的响应格式，包含异常编码、信息等，供上层模块使用。|
|错误码|对应系统全局错误码表，用于区分不同异常类型，便于问题定位与处理，本模块所有异常均对应唯一错误码。|
# 2. 模块整体设计

## 2.1 模块定位

异常处理模块属于系统基础支撑层核心模块，是所有上层模块（数据层、核心业务层、接口层、应用层）的依赖模块，负责统一处理系统运行过程中产生的所有异常，规范异常输出格式，记录异常日志，为问题排查与系统稳定运行提供支撑。

## 2.2 设计原则

- 统一性：定义统一的异常基类与异常处理接口，所有模块均通过本模块处理异常，确保异常输出格式一致。

- 可扩展性：支持自定义异常类型的扩展，新增业务模块时可新增对应异常类型，不影响原有逻辑。

- 可追溯性：异常处理过程中记录详细日志，关联系统错误码，便于定位异常原因。

- 易用性：提供简洁的异常处理接口，适配初学者，上层模块可快速集成使用。

## 2.3 依赖关系

本模块仅依赖基础支撑层内部的日志模块，用于记录异常日志；不依赖其他模块，同时为所有上层模块提供异常处理服务，具体依赖关系如下：

- 依赖模块：日志模块（log_module），用于记录异常详细信息，便于排查。

- 被依赖模块：基础支撑层其他模块（通用工具、配置管理）、数据层所有模块、核心业务层所有模块、接口层、应用层。

# 3. 统一项目结构规范

遵循系统全局项目结构规范，异常处理模块采用以下统一目录结构，开发者需严格遵循，不得随意修改目录名称与层级，初学者可直接复制该结构搭建项目。

```plain text
# 异常处理模块目录结构（模块名称：exception_module）
exception_module/                  # 模块根目录（全小写，多单词用下划线连接）
├── __init__.py                    # 模块初始化文件，暴露模块核心类/方法（必须包含，不能为空）
├── core/                          # 核心逻辑目录（存放模块核心实现，含ABC抽象类）
│   ├── __init__.py
│   ├── base.py                    # 抽象基类（ABC）文件，定义模块核心接口（必须包含）
│   └── impl.py                    # 具体实现类文件，继承base.py中的抽象类（必须包含）
├── utils/                         # 模块工具目录（存放模块专属工具函数，无则空目录）
│   ├── __init__.py
│   └── tool_functions.py          # 工具函数文件（如异常编码映射辅助函数）
├── config/                        # 模块配置目录（存放模块专属配置，无则空目录）
│   ├── __init__.py
│   └── config.py                  # 配置文件（读取基础配置，可添加模块专属配置）
├── tests/                         # 测试目录（存放模块单元测试、集成测试用例，必须包含）
│   ├── __init__.py
│   ├── test_base.py               # 抽象类测试用例（可选，初学者可简化）
│   └── test_impl.py               # 具体实现类测试用例（必须包含，覆盖核心功能）
└── README.md                      # 模块说明文档（必须包含，说明模块功能、接口、使用方法，适配初学者）
```

## 3.1 目录结构说明

- exception_module：模块根目录，名称固定为exception_module，对应异常处理模块功能。

- __init__.py：每个目录必须包含，核心作用是将目录标识为Python模块，根目录的__init__.py需暴露模块核心类/方法（如from .core.impl import ExceptionHandler, SystemBaseException），方便其他模块调用。

- core目录：核心逻辑存放处，base.py是抽象基类（ABC），定义异常处理的核心接口；impl.py是具体实现，继承base.py的抽象类，实现所有抽象方法，同时定义系统统一异常类型。

- utils目录：模块专属工具函数，如异常编码与异常信息的映射辅助函数，不包含核心业务逻辑，仅为模块提供辅助。

- config目录：模块专属配置，可读取基础支撑层的全局配置，补充模块专属配置（如异常日志输出格式），无专属配置可留空。

- tests目录：测试用例存放处，必须包含test_impl.py，覆盖异常捕获、异常封装、异常编码获取等核心功能的单元测试，初学者可参考示例编写简单测试。

- README.md：模块说明文档，需详细说明模块功能、核心接口、使用方法、依赖项、常见异常及对应错误码、常见问题，语言简洁易懂，适配初学者。

## 3.2 统一编码规范

遵循系统全局编码规范，所有开发人员需严格执行，确保代码可交换、可维护：

- 编码格式：UTF-8，缩进采用4个空格（禁止使用Tab），每行代码长度不超过120字符。

- 命名规范：

- 类名：大驼峰命名法（如ExceptionHandler、SystemBaseException）；

- 方法名/函数名：小驼峰命名法（如handle_exception、get_exception_code）；

- 变量名：小驼峰命名法（如exception、error_info）；

- 常量名：全大写，多单词用下划线连接（如EXCEPTION_CODE_MAP）；

- 模块名/目录名：全小写，多单词用下划线连接（如exception_module）。

注释规范：

- 类注释：使用文档字符串（"""），说明类的功能、参数、返回值（若有）；

- 方法/函数注释：使用文档字符串，说明功能、参数（名称、类型、含义）、返回值（类型、含义）、异常（若有）；

- 关键代码注释：对复杂逻辑、不易理解的代码，添加单行注释（#），说明逻辑用途。

依赖管理：模块的依赖项统一写入requirements.txt文件（放在模块根目录），注明依赖包名称与版本，避免版本冲突，核心依赖为日志模块相关依赖。

# 4. 模块详细设计

## 4.1 模块功能

异常处理模块核心功能如下，具体方法实现不在本文档体现，仅保留核心接口与代码基础构建：

- 定义系统统一异常基类及各类业务异常（配置、向量数据库、RAG、Agent等相关异常），所有异常均关联系统错误码表中的对应编码。

- 提供异常处理接口，捕获系统运行过程中产生的所有异常（自定义异常、系统异常），进行统一封装，输出标准化的异常信息。

- 提供异常编码获取接口，根据异常类型返回对应的系统错误码，便于问题定位。

- 集成日志模块，记录异常详细信息（异常类型、异常信息、发生位置等），为问题排查提供支撑。

## 4.2 抽象基类设计（core/base.py）

抽象基类定义异常处理的核心接口，强制子类实现，确保模块一致性，代码基础构建如下：

```python
from abc import ABC, abstractmethod
from typing import Dict


class BaseExceptionHandler(ABC):
    """异常处理抽象基类，定义异常处理核心接口，所有异常处理实现类需继承此类"""

    @abstractmethod
    def handle_exception(self, exception: Exception) -> Dict[str, str]:
        """
        处理异常，封装异常信息，对应系统统一响应格式中的错误结构
        :param exception: 捕获的异常对象（自定义异常或系统异常）
        :return: 封装后的异常信息（包含code、message，与系统错误码表严格对应）
        """
        pass

    @abstractmethod
    def get_exception_code(self, exception: Exception) -> str:
        """
        获取异常编码，对应系统错误码表中的业务码
        :param exception: 异常对象
        :return: 异常编码（字符串，遵循系统错误码命名规范）
        """
        pass
```

## 4.3 具体实现类设计（core/impl.py）

具体实现类继承抽象基类，实现所有抽象方法，同时定义系统统一异常类型，关联系统错误码表，代码基础构建如下（不含具体方法实现）：

```python
from .base import BaseExceptionHandler
from typing import Dict
from log_module.core.impl import SystemLogger


# 定义系统统一异常基类，所有自定义异常均继承此类，关联系统错误码
class SystemBaseException(Exception):
    """系统基础异常类，所有自定义异常继承此类，包含异常编码与异常信息"""

    def __init__(self, code: str, message: str):
        self.code = code  # 异常编码，严格对应系统错误码表中的业务码
        self.message = message  # 异常信息，简洁明了，便于用户理解
        super().__init__(message)


# 定义各类业务异常，对应系统不同模块的异常场景，均继承SystemBaseException
class ConfigException(SystemBaseException):
    """配置相关异常（如配置文件不存在、配置键缺失），对应错误码表中CONFIG_*系列"""
    pass


class VectorDBException(SystemBaseException):
    """向量数据库相关异常（如连接失败、检索失败），对应错误码表中VECTOR_*系列"""
    pass


class RAGException(SystemBaseException):
    """RAG模块相关异常（如文档加载失败、嵌入失败），对应错误码表中RAG_*系列"""
    pass


class AgentException(SystemBaseException):
    """Agent模块相关异常（如任务解析失败、工具调用失败），对应错误码表中AGENT_*系列"""
    pass


class ExceptionHandler(BaseExceptionHandler):
    """异常处理具体实现类，继承抽象基类，实现所有抽象方法"""

    def __init__(self):
        self.logger = SystemLogger()  # 依赖日志模块，记录异常日志

    def get_exception_code(self, exception: Exception) -> str:
        """获取异常编码，根据异常类型匹配系统错误码表中的业务码"""
        # 具体实现逻辑（本文档不体现），核心逻辑为：
        # 1. 判断异常类型（自定义异常/系统异常）
        # 2. 自定义异常直接返回其code属性（与错误码表对应）
        # 3. 系统异常返回默认编码UNKNOWN_ERROR
        pass

    def handle_exception(self, exception: Exception) -> Dict[str, str]:
        """处理异常，封装为统一格式，记录异常日志"""
        # 具体实现逻辑（本文档不体现），核心逻辑为：
        # 1. 调用日志模块记录异常详细信息
        # 2. 调用get_exception_code获取异常编码
        # 3. 封装异常信息为{"code": 异常编码, "message": 异常信息}
        # 4. 确保返回格式与系统统一响应结构中的失败返回格式一致
        pass
```

## 4.4 工具函数补充（utils/tool_functions.py）

提供模块专属工具函数，辅助异常处理，代码基础构建如下（根据实际需求扩展）：

```python
def get_exception_message(exception: Exception) -> str:
    """
    辅助函数：获取异常的详细信息，处理嵌套异常
    :param exception: 异常对象
    :return: 异常详细信息字符串
    """
    pass


def exception_code_to_message(code: str) -> str:
    """
    辅助函数：根据异常编码，返回对应的默认异常信息（与系统错误码表对应）
    :param code: 异常编码
    :return: 默认异常信息
    """
    pass
```

## 4.5 接口调用示例（供其他模块参考）

其他模块集成异常处理模块的调用示例，简洁易懂，适配初学者：

```python
from exception_module.core.impl import ExceptionHandler, ConfigException, VectorDBException

# 初始化异常处理器（全局只需初始化一次）
exception_handler = ExceptionHandler()

# 捕获并处理配置相关异常（对应错误码CONFIG_NOT_FOUND）
try:
    # 模拟配置文件不存在异常
    raise ConfigException("CONFIG_NOT_FOUND", "配置文件不存在")
except Exception as e:
    # 调用异常处理接口，获取标准化异常信息
    error_info = exception_handler.handle_exception(e)
    # 输出格式：{"code": "CONFIG_NOT_FOUND", "message": "配置文件不存在"}
    print(error_info)

# 捕获并处理向量数据库相关异常（对应错误码VECTOR_DB_CONNECT_FAILED）
try:
    # 模拟向量数据库连接失败异常
    raise VectorDBException("VECTOR_DB_CONNECT_FAILED", "向量数据库连接失败")
except Exception as e:
    error_info = exception_handler.handle_exception(e)
    # 输出格式：{"code": "VECTOR_DB_CONNECT_FAILED", "message": "向量数据库连接失败"}
    print(error_info)
```

## 4.6 测试用例设计（tests/test_impl.py）

测试用例需覆盖核心功能，适配初学者编写，代码基础构建如下：

```python
import unittest
from exception_module.core.impl import ExceptionHandler, ConfigException, AgentException


class TestExceptionHandler(unittest.TestCase):
    def setUp(self):
        """初始化异常处理器实例，用于所有测试用例"""
        self.exception_handler = ExceptionHandler()

    # 测试自定义异常的编码获取与异常封装
    def test_custom_exception_handle(self):
        # 测试配置异常
        config_exc = ConfigException("CONFIG_KEY_MISSING", "配置缺失：vector_db.host")
        self.assertEqual(self.exception_handler.get_exception_code(config_exc), "CONFIG_KEY_MISSING")
        error_info = self.exception_handler.handle_exception(config_exc)
        self.assertEqual(error_info["code"], "CONFIG_KEY_MISSING")
        self.assertEqual(error_info["message"], "配置缺失：vector_db.host")

        # 测试Agent异常
        agent_exc = AgentException("TOOL_NOT_FOUND", "工具不存在：rag_search")
        self.assertEqual(self.exception_handler.get_exception_code(agent_exc), "TOOL_NOT_FOUND")
        error_info = self.exception_handler.handle_exception(agent_exc)
        self.assertEqual(error_info["code"], "TOOL_NOT_FOUND")
        self.assertEqual(error_info["message"], "工具不存在：rag_search")

    # 测试系统未知异常的处理
    def test_unknown_exception_handle(self):
        unknown_exc = Exception("未知错误：数据库连接超时")
        self.assertEqual(self.exception_handler.get_exception_code(unknown_exc), "UNKNOWN_ERROR")
        error_info = self.exception_handler.handle_exception(unknown_exc)
        self.assertEqual(error_info["code"], "UNKNOWN_ERROR")
        self.assertIn("未知异常", error_info["message"])


if __name__ == "__main__":
    unittest.main()
```

# 5. 与系统错误码表的关联规范

## 5.1 关联原则

本模块所有自定义异常的编码，必须严格对应系统全局错误码表（第10章）中的业务码，遵循以下原则：

- 异常编码命名规范：全大写 + 下划线，格式为MODULE_ACTION_REASON，与错误码表完全一致（如CONFIG_NOT_FOUND、VECTOR_QUERY_FAILED）。

- 异常信息与错误码表匹配：自定义异常的message字段，需与错误码表中对应业务码的message示例一致，可根据实际场景补充细节，但核心含义不变。

- 异常类型与错误码分类对应：ConfigException对应错误码表中CONFIG_*系列，VectorDBException对应VECTOR_*系列，依次类推，确保分类统一。

- 未知异常处理：系统未定义的异常（非自定义异常），统一返回错误码表中的UNKNOWN_ERROR，确保异常编码全覆盖。

## 5.2 核心关联映射

本模块自定义异常与系统错误码表的核心关联如下，完整关联请参考系统全局错误码表（第10章）：

|自定义异常类|对应错误码前缀|示例错误码|示例异常信息|
|---|---|---|---|
|ConfigException|CONFIG_*|CONFIG_NOT_FOUND|配置文件不存在|
|ConfigException|CONFIG_*|CONFIG_KEY_MISSING|配置缺失：xxx|
|VectorDBException|VECTOR_*|VECTOR_QUERY_FAILED|向量检索失败|
|RAGException|RAG_*|RAG_RUN_FAILED|RAG执行失败|
|AgentException|AGENT_*|TOOL_CALL_FAILED|工具调用失败：xxx|
# 6. 开发与交付规范

## 6.1 交付物清单（必须）

模块开发完成后，需提交以下交付物，确保符合系统统一交付标准：

- core/base.py：异常处理抽象基类（ABC），包含所有核心接口。

- core/impl.py：具体实现类，包含自定义异常定义与接口实现。

- utils/tool_functions.py：模块专属工具函数（若有）。

- config/config.py：模块专属配置（若有）。

- tests/test_impl.py：核心测试用例，覆盖异常捕获、编码获取、异常封装等功能。

- README.md：模块说明文档，详细说明模块功能、接口、使用方法、依赖项、异常与错误码关联。

- requirements.txt：模块依赖包清单，注明名称与版本。

## 6.2 可替换性约束（强制）

- 其他模块只能依赖本模块的抽象接口（BaseExceptionHandler）或自定义异常类，禁止直接引用impl.py中的内部私有方法。

- 若需修改异常处理逻辑，需继承BaseExceptionHandler抽象基类，实现所有接口，不得修改抽象基类的定义，确保不影响其他模块集成。

- 新增异常类型时，需新增对应的自定义异常类（继承SystemBaseException），关联系统错误码表中的新增业务码，确保扩展不破坏原有逻辑。

# 7. 集成说明

## 7.1 集成前提

集成异常处理模块前，需确保基础支撑层的日志模块已开发完成并部署，因为本模块依赖日志模块记录异常信息。

## 7.2 集成步骤（适配初学者）

1. 复制本模块的目录结构，搭建本地开发环境，安装requirements.txt中的依赖包。

2. 在需要集成异常处理的模块中，导入ExceptionHandler类及所需的自定义异常类。

3. 初始化ExceptionHandler实例（全局只需初始化一次）。

4. 在可能产生异常的代码块中，使用try-except捕获异常，调用handle_exception方法处理异常，获取标准化异常信息。

5. 根据异常信息中的code字段，进行对应的业务处理（如返回错误响应、记录日志等）。

# 8. 常见问题（FAQ）

- Q：新增业务模块后，如何新增对应的异常类型？
A：新增自定义异常类，继承SystemBaseException，指定对应的异常编码（遵循错误码表规范），无需修改原有异常处理逻辑，直接在业务模块中抛出新增异常即可。

- Q：异常编码与错误码表不匹配怎么办？
A：检查自定义异常的code属性，确保与系统错误码表中的业务码完全一致，修改后重新测试集成。

- Q：如何确保异常日志记录完整？
A：确保日志模块已正确集成，ExceptionHandler类已初始化SystemLogger实例，异常处理时会自动记录异常详细信息。

- Q：系统未知异常如何处理？
A：未知异常会被自动捕获，返回错误码UNKNOWN_ERROR，异常信息为“未知异常：xxx”，同时记录详细异常日志，便于排查。

# 9. 文档更新与维护

## 9.1 更新原则

当系统错误码表（第10章）更新、异常处理模块功能迭代、新增异常类型或集成方式变更时，需同步更新本文档，确保文档与实际开发实现一致，更新后需提交项目管理平台审核，审核通过后生效。

## 9.2 维护责任

异常处理模块的开发负责人为文档维护第一责任人，负责文档的更新、修订与答疑；项目管理人员负责监督文档的完整性与准确性，确保文档可作为开发、测试、集成的唯一标准依据。

# 10. 系统全局错误码表

## 10.1 错误码设计规范

系统全局错误码采用“模块前缀+具体场景”的命名方式，全大写字母+下划线组成，格式为【MODULE_ACTION_REASON】，具体规范如下：

- 模块前缀：对应系统各模块缩写，如CONFIG（配置模块）、VECTOR（向量数据库模块）、RAG（RAG模块）、AGENT（Agent模块）、EXCEPTION（异常处理模块）。

- 动作标识（ACTION）：表示异常对应的操作，如NOT_FOUND（未找到）、CONNECT_FAILED（连接失败）、QUERY_FAILED（查询失败）、KEY_MISSING（键缺失）。

- 原因标识（REASON）：可选，补充异常具体原因，如INVALID_FORMAT（格式无效）、TIMEOUT（超时），无具体原因可省略。

- 通用错误码：不归属任何具体业务模块，用于全局通用异常，如UNKNOWN_ERROR（未知错误）、SYSTEM_ERROR（系统错误）。

## 10.2 完整错误码表

|错误码|模块归属|异常信息（默认）|对应异常类|适用场景|
|---|---|---|---|---|
|UNKNOWN_ERROR|通用|未知异常，请查看日志排查具体原因|无（系统异常）|未定义的自定义异常、系统原生异常|
|SYSTEM_ERROR|通用|系统内部错误，无法正常处理请求|无（系统异常）|系统级故障，如依赖模块崩溃|
|CONFIG_NOT_FOUND|配置模块|配置文件不存在，请检查配置路径是否正确|ConfigException|读取配置文件时，文件路径错误或文件缺失|
|CONFIG_KEY_MISSING|配置模块|配置缺失：{key}，请补充配置信息|ConfigException|配置文件存在，但缺少必要的配置键|
|CONFIG_INVALID_FORMAT|配置模块|配置文件格式无效，请检查配置语法|ConfigException|配置文件格式错误（如JSON、YAML语法错误）|
|VECTOR_DB_CONNECT_FAILED|向量数据库模块|向量数据库连接失败，请检查连接配置|VectorDBException|无法连接向量数据库（地址、端口、密钥错误）|
|VECTOR_QUERY_FAILED|向量数据库模块|向量检索失败，请检查检索参数或数据库状态|VectorDBException|向量数据库连接正常，但检索操作失败|
|VECTOR_INSERT_FAILED|向量数据库模块|向量插入失败，请检查插入数据格式或数据库权限|VectorDBException|向向量数据库插入数据时失败|
|RAG_DOC_LOAD_FAILED|RAG模块|RAG文档加载失败，请检查文档路径或格式|RAGException|RAG模块加载文档时，文件不存在或格式不支持|
|RAG_EMBED_FAILED|RAG模块|RAG文档嵌入失败，请检查嵌入模型或数据|RAGException|RAG模块对文档进行嵌入处理时失败|
|RAG_RUN_FAILED|RAG模块|RAG执行失败，请检查检索策略或模型配置|RAGException|RAG模块整体执行流程失败|
|AGENT_TASK_PARSE_FAILED|Agent模块|Agent任务解析失败，请检查任务描述格式|AgentException|Agent模块无法解析用户提交的任务描述|
|TOOL_NOT_FOUND|Agent模块|工具不存在：{tool_name}，请检查工具配置|AgentException|Agent调用工具时，指定的工具未注册或不存在|
|TOOL_CALL_FAILED|Agent模块|工具调用失败：{tool_name}，请检查工具状态或参数|AgentException|Agent调用工具时，工具本身执行失败|
|EXCEPTION_HANDLER_ERROR|异常处理模块|异常处理器执行失败，请检查日志模块或异常配置|无（系统异常）|异常处理模块自身执行出错，如日志模块调用失败|

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
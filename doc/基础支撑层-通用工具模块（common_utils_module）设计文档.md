# 基础支撑层 - 通用工具模块（common_utils_module）设计文档

> **v2.1 (2026-05-29) 架构变更 — OO (#75) 5 个 cross-cutting 已提到独立 module**:
> - `hooks_module/` ← 原 `common_utils/utils/hooks.py` (Task Z #60 Hooks 系统)
> - `skills_module/` ← 原 `common_utils/utils/skills.py` (Task AA #61 Skill 系统)
> - `quota_module/` ← 原 `common_utils/utils/quota.py` (Task BB #62 配额/限流)
> - `audit_module/` ← 原 `common_utils/utils/audit_log.py` (Task CC #63 审计日志)
> - `project_memory_module/` ← 原 `common_utils/utils/project_memory.py` (Task U #55 项目记忆)
>
> `common_utils_module/__init__.py` 仍 re-export 这 5 个 module 的公共 API,
> 老 import `from common_utils_module import HookRegistry, ...` 仍可用 (identity 保留).
> `common_utils_module/utils/<name>.py` 也降级为 thin re-export shim, 让深度 import
> `from common_utils_module.utils.skills import parse_skill_file` 仍可用.
>
> 新调用方推荐直接 import 新 module: `from hooks_module import HookRegistry`. 详见 `CHANGELOG.md`.

# 1. 模块概述

## 1.1 模块目的

本模块为系统提供全局通用工具函数，供所有上层模块（数据层、核心业务层、接口层、应用层）直接调用，核心目标是避免重复开发，统一工具类实现标准，保障系统工具能力的一致性与可维护性。同时，模块适配初学者开发需求，明确接口使用规范，降低开发门槛，进一步提升团队整体开发效率。

## 1.2 适用人群

本团队所有开发人员（含资深开发者与初学者）、测试人员，均可将本文档作为模块开发、调用、测试及维护的唯一标准依据；同时，本文档也可为系统后续扩展开发提供工具类参考，确保新增工具方法与现有规范保持一致，保障系统整体规范性。

## 1.3 核心需求

- 提供系统级通用工具函数，涵盖文本处理、数据格式转换、参数校验、时间处理等基础能力，重点扩张时间相关方法，丰富各功能下的具体实现，满足多样化业务场景需求，确保工具方法的通用性和实用性。

- 遵循系统统一项目结构规范，引入抽象基类（ABC），将同类工具方法整合至对应类中，强制子类实现核心接口，保障模块的一致性、可扩展性和可维护性。

- 模块独立开发、可独立测试，不依赖其他基础支撑层模块（除系统统一规范依赖外），可被所有上层模块直接调用，有效降低模块间耦合度。

- 接口设计简洁易懂，适配初学者调用场景，提供清晰的调用示例、参数说明和异常提示，减少开发过程中的使用成本和错误率。

- 工具方法具备一定的容错性，对非法输入能抛出明确异常，便于开发者快速调试和问题定位，提升开发效率。

## 1.4 术语定义

|术语|定义|
|---|---|
|ABC|抽象基类，定义模块的核心接口与方法，强制子类实现，保障模块一致性、可扩展性，避免子类实现偏离模块规范。|
|工具函数|模块专属辅助函数，不包含核心业务逻辑，仅为模块自身或其他模块提供辅助能力，同类函数整合到对应工具类中，便于统一管理和调用。|
|工具类|整合同类工具方法的类，按功能划分（如文本处理类、数据转换类、时间处理类），统一管理同类功能，提升可维护性和代码复用率。|
|时间处理方法|归属于通用辅助工具类，涵盖时间获取、格式转换、差值计算、区间判断等相关操作，为系统提供全面的时间辅助能力，适配各类时间相关业务场景。|
|核心接口|由抽象基类定义的、子类必须实现的方法，是模块对外提供服务的统一入口，确保不同实现类的接口一致性。|
|具体实现类|继承抽象基类，实现所有抽象接口，是工具方法的具体落地载体，整合各类工具类实例，供上层模块统一调用。|
# 2. 模块项目结构

为保障模块规范性和可维护性，本模块严格遵循系统统一项目结构规范，模块根目录命名为common_utils_module，具体目录结构如下（开发者需严格遵循，不得随意修改目录名称与层级，初学者可直接复制该结构搭建项目，确保项目结构统一）：

```plain text
common_utils_module/                  # 模块根目录（全小写，多单词用下划线连接）
├── __init__.py                       # 模块初始化文件，暴露模块核心类/方法（必须包含，不能为空）
├── core/                             # 核心逻辑目录（存放模块核心实现，含ABC抽象类）
│   ├── __init__.py
│   ├── base.py                       # 抽象基类（ABC）文件，定义模块核心接口（必须包含）
│   └── impl.py                       # 具体实现类文件，继承base.py中的抽象类，整合同类方法（必须包含）
├── utils/                            # 模块工具目录（存放模块专属工具类，无则空目录）
│   ├── __init__.py
│   ├── text_tool.py                  # 文本处理工具类，整合所有文本相关辅助方法
│   ├── data_tool.py                  # 数据处理工具类，整合所有数据转换、处理辅助方法
│   └── assist_tool.py                # 通用辅助工具类，整合加密、时间、日志等辅助方法（重点扩张时间相关）
├── config/                           # 模块配置目录（存放模块专属配置，无则空目录）
│   ├── __init__.py
│   └── config.py                     # 配置文件（读取基础配置，可添加模块专属配置）
├── tests/                            # 测试目录（存放模块单元测试、集成测试用例，必须包含）
│   ├── __init__.py
│   ├── test_base.py                  # 抽象类测试用例（可选，初学者可简化）
│   └── test_impl.py                  # 具体实现类测试用例（必须包含，覆盖核心功能，含新增时间方法）
└── README.md                         # 模块说明文档（必须包含，说明模块功能、接口、使用方法，适配初学者）
```

## 2.1 目录结构说明

- common_utils_module：模块根目录，名称采用全小写、多单词下划线连接的命名方式，与模块功能高度对应，是模块对外暴露的唯一入口目录，便于上层模块识别和调用。

- __init__.py：每个目录必须包含该文件，核心作用是将目录标识为Python模块；其中，根目录的__init__.py需明确暴露模块核心类/方法（如CommonUtils），方便其他模块直接导入调用，避免导入路径繁琐，提升开发效率。

- core目录：模块核心逻辑的存放载体，是整个模块的核心所在。其中，base.py为抽象基类（ABC）文件，用于定义模块必须实现的接口方法，规范子类实现逻辑；impl.py为具体实现类文件，继承base.py中的抽象类，实现所有抽象方法，并将同类功能方法整合管理，重点完善时间相关接口的整合，确保核心功能落地见效。

- utils目录：模块专属工具类目录，按功能划分为text_tool.py、data_tool.py、assist_tool.py三个工具类，分别整合文本处理、数据处理、通用辅助相关的工具方法。其中，assist_tool.py重点扩张时间相关辅助方法，该目录下所有工具类均不包含核心业务逻辑，仅为模块自身或其他模块提供辅助支持，与core目录的工具类形成互补，提升模块整体辅助能力。

- config目录：模块专属配置目录，本模块无特殊专属配置，可保留空目录；若后续需添加配置（如时间格式默认值、加密密钥等），可在config.py中读取基础支撑层的全局配置并补充，确保配置统一管理，避免配置混乱。

- tests目录：测试用例存放目录，必须包含test_impl.py文件，用于覆盖模块核心功能的单元测试，重点覆盖新增的时间相关方法，确保各方法功能正常、符合预期；test_base.py为可选文件，初学者可简化编写，主要用于验证抽象基类的接口规范性，保障接口设计符合模块规范。

- README.md：模块说明文档，需详细说明模块功能、核心接口、使用方法、依赖项及常见问题，重点补充时间相关方法的说明，语言需简洁易懂，适配初学者查阅使用，同时作为模块维护和交接的重要参考资料。

# 3. 编码规范

遵循[系统架构设计](./RAG与Agent系统架构设计说明书.md)中的 3.2 统一编码规范

# 4. 模块详细设计

## 4.1 模块功能

本模块核心功能是提供系统通用工具函数，将同类方法合并到对应工具类中，供所有上层模块调用，避免重复开发，提升开发效率。其中，重点扩张时间相关方法，丰富后的核心功能按类划分管理，确保功能清晰、分类合理，具体如下：

### 4.1.1 文本处理类（TextTool）

- 基础清洗：去除文本中的特殊字符、多余空格、换行符、制表符，统一文本编码，处理文本中的乱码问题，确保文本格式规范，为后续文本处理提供基础。

- 内容处理：实现文本脱敏（隐藏手机号、身份证号、邮箱等敏感信息）、大小写转换、去除停用词等功能，满足文本处理中的隐私保护和内容优化需求。

- 格式处理：支持文本截取、换行符统一（Windows/Linux格式互转）、特殊符号转义等操作，适配不同场景下的文本格式要求，提升文本兼容性。

- 校验判断：提供文本空值判断、关键词包含判断、格式校验（纯数字/纯字母/邮箱/URL）等功能，为参数校验和内容判断提供支持。

### 4.1.2 数据转换类（DataTool）

- 格式转换：实现字典与JSON字符串互转、字典与XML字符串互转、列表与字符串互转、字符串与数字互转等功能，解决不同数据格式之间的转换需求，保障数据互通。

- 数据处理：支持字典去重、列表去重、字典筛选（保留指定key）、列表排序（按指定字段/规则）等操作，优化数据结构，提升数据处理效率。

- 类型转换：将字符串类型的布尔值（"true"/"false"）转为布尔类型、将字符串日期转为datetime类型，为后续数据处理和时间操作提供支持，确保数据类型统一。

### 4.1.3 参数校验类（集成于CommonUtils核心类）

- 必填项校验：校验待校验参数（字典/列表）是否包含所有必填项，支持嵌套参数校验（如"user.name"），确保上层模块调用时参数完整，避免因参数缺失导致程序异常。

- 类型校验：校验参数是否为指定类型（如int、str、list、datetime），支持可选类型校验，避免因参数类型错误导致的程序异常，提升模块稳定性。

- 范围校验：校验数字参数是否在指定范围（最大值/最小值）、字符串参数长度是否在指定范围，确保参数符合业务要求，保障数据合法性。

- 格式校验：校验手机号、身份证号、邮箱、URL、日期字符串是否符合标准格式，为数据合法性校验提供支持，减少非法数据传入。

### 4.1.4 通用辅助工具类（AssistTool）- 重点扩张时间相关方法

- 加密解密：提供MD5加密、AES简单加密/解密（适用于非核心敏感数据）、Base64编码/解码功能，满足简单的敏感数据保护需求，保障数据安全。

- 时间工具：

    - 时间获取：支持获取当前时间（多种格式）、指定日期的时间、当前时间戳（秒级/毫秒级），以及当天、当月、当年的起止时间，适配不同场景下的时间获取需求。

    - 格式转换：实现日期字符串与datetime类型互转、datetime类型与时间戳互转、不同时间格式互转（如YYYY-MM-DD与MM/DD/YYYY）、时区转换（如UTC时区与本地时区互转），解决不同时间类型和格式的转换问题，确保时间数据统一。

    - 差值计算：支持计算两个日期/时间的差值（天、小时、分钟、秒）、指定日期前后N天/小时/分钟的时间，以及两个日期之间的天数差、小时差，满足时间差值相关的业务需求。

    - 时间判断：支持判断某个时间是否在指定时间区间内、某个日期是否为工作日/节假日、两个时间的先后顺序，以及当前时间是否为指定时段（如凌晨、上午），为时间相关的逻辑判断提供支持。

    - 时间格式化：支持自定义时间格式、补零格式化（如将1月转为01月、1日转为01日），以及获取时间的指定部分（年、月、日、时、分、秒），满足不同场景下的时间展示和处理需求。

- 日志工具：提供简单日志记录功能（控制台输出/文件写入），支持不同日志级别（INFO、WARN、ERROR），便于模块运行过程中的问题排查和日志追溯，提升模块可维护性。

- 文件辅助：支持读取文本文件内容、写入文本文件、判断文件是否存在、获取文件大小等操作，为文件操作提供基础辅助能力，满足简单的文件处理需求。

## 4.2 核心接口设计（抽象基类）

抽象基类（core/base.py）是模块接口规范的核心，按功能划分核心类接口，强制具体实现类实现所有抽象方法，将同类方法整合到对应接口中，重点扩张时间相关接口，保障模块一致性和可扩展性。以下为代码基础构建（不包含具体实现逻辑，仅定义接口规范）：

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Union, Optional
from datetime import datetime


class BaseTextTool(ABC):
    """文本处理抽象基类，定义所有文本相关工具的核心接口，具体实现类需继承此类"""

    @abstractmethod
    def text_clean(self, text: str) -> str:
        """
        文本基础清洗：去除特殊字符、多余空格、换行符、制表符，统一编码
        :param text: 原始文本
        :return: 清洗后的标准文本
        """
        pass

    @abstractmethod
    def text_desensitize(self, text: str, type_: str) -> str:
        """
        文本脱敏：根据类型隐藏敏感信息
        :param text: 原始文本
        :param type_: 脱敏类型（支持"phone"、"id_card"、"email"）
        :return: 脱敏后的文本
        :raises ValueError: 脱敏类型不支持时抛出异常
        """
        pass

    @abstractmethod
    def text_validate(self, text: str, type_: str) -> bool:
        """
        文本格式校验：判断文本是否符合指定格式
        :param text: 待校验文本
        :param type_: 校验类型（支持"email"、"phone"、"id_card"、"url"）
        :return: 校验通过返回True，否则返回False
        """
        pass


class BaseDataTool(ABC):
    """数据处理抽象基类，定义所有数据转换、处理相关工具的核心接口"""

    @abstractmethod
    def dict_to_json(self, data: Dict) -> str:
        """
        字典转JSON字符串
        :param data: 待转换字典
        :return: JSON字符串
        :raises TypeError: 数据不是字典类型时抛出异常
        """
        pass

    @abstractmethod
    def data_convert(self, data: Any, target_type: str) -> Any:
        """
        通用数据类型转换
        :param data: 待转换数据
        :param target_type: 目标类型（支持"str"、"int"、"bool"、"datetime"）
        :return: 转换后的数据
        :raises ValueError: 目标类型不支持时抛出异常
        """
        pass

    @abstractmethod
    def list_deduplicate(self, data_list: List) -> List:
        """
        列表去重，保留原顺序
        :param data_list: 待去重列表
        :return: 去重后的列表
        """
        pass


class BaseParamValidate(ABC):
    """参数校验抽象基类，定义所有参数校验相关的核心接口"""

    @abstractmethod
    def required_validate(self, params: Dict, required_params: List[str]) -> bool:
        """
        必填参数校验：校验字典参数是否包含所有必填项，支持嵌套参数（格式："key1.key2"）
        :param params: 待校验参数（字典）
        :param required_params: 必填参数列表
        :return: 校验通过返回True，否则返回False
        """
        pass

    @abstractmethod
    def range_validate(self, value: Union[int, float, str], min_val: Union[int, float, int], max_val: Union[int, float, int]) -> bool:
        """
        范围校验：校验数值大小或字符串长度是否在指定范围
        :param value: 待校验值（数值/字符串）
        :param min_val: 最小值/最小长度
        :param max_val: 最大值/最大长度
        :return: 校验通过返回True，否则返回False
        """
        pass


class BaseAssistTool(ABC):
    """通用辅助工具抽象基类，定义辅助功能的核心接口，重点包含时间相关接口"""

    @abstractmethod
    def md5_encrypt(self, text: str) -> str:
        """
        MD5加密函数，用于敏感数据加密（如配置密码）
        :param text: 需要加密的文本
        :return: MD5加密后的字符串
        """
        pass

    # 时间相关接口（重点扩张）
    @abstractmethod
    def get_current_time(self, format_: str = "YYYY-MM-DD HH:MM:SS", time_zone: Optional[str] = None) -> str:
        """
        获取当前时间，支持自定义格式和时区
        :param format_: 时间格式，默认"YYYY-MM-DD HH:MM:SS"
        :param time_zone: 时区，默认None（使用本地时区）
        :return: 当前时间字符串
        """
        pass

    @abstractmethod
    def get_timestamp(self, time_str: Optional[str] = None, format_: str = "YYYY-MM-DD HH:MM:SS", millisecond: bool = False) -> int:
        """
        获取时间戳，支持指定时间字符串和秒级/毫秒级
        :param time_str: 指定时间字符串，None则获取当前时间戳
        :param format_: 时间字符串格式，默认"YYYY-MM-DD HH:MM:SS"
        :param millisecond: 是否返回毫秒级时间戳，默认False（秒级）
        :return: 时间戳（int类型）
        :raises ValueError: 时间字符串格式错误时抛出异常
        """
        pass

    @abstractmethod
    def time_convert(self, time_data: Union[str, datetime, int], target_type: str, format_: str = "YYYY-MM-DD HH:MM:SS") -> Union[str, datetime, int]:
        """
        时间类型转换：字符串/ datetime / 时间戳互转
        :param time_data: 待转换时间数据（字符串/ datetime / 时间戳）
        :param target_type: 目标类型（"str"/"datetime"/"timestamp"）
        :param format_: 时间字符串格式，默认"YYYY-MM-DD HH:MM:SS"（仅target_type为str时生效）
        :return: 转换后的时间数据
        :raises ValueError: 目标类型不支持或时间数据格式错误时抛出异常
        """
        pass

    @abstractmethod
    def time_diff_calculate(self, start_time: Union[str, datetime, int], end_time: Union[str, datetime, int], unit: str = "day") -> float:
        """
        计算两个时间的差值，支持多种时间单位
        :param start_time: 开始时间（字符串/ datetime / 时间戳）
        :param end_time: 结束时间（字符串/ datetime / 时间戳）
        :param unit: 差值单位（"day"/"hour"/"minute"/"second"），默认"day"（天）
        :return: 时间差值（浮点型，保留2位小数）
        :raises ValueError: 时间数据格式错误或单位不支持时抛出异常
        """
        pass

    @abstractmethod
    def time_offset(self, time_data: Union[str, datetime, int], offset: int, unit: str = "day", format_: str = "YYYY-MM-DD HH:MM:SS") -> str:
        """
        计算指定时间偏移N单位后的时间
        :param time_data: 基准时间（字符串/ datetime / 时间戳）
        :param offset: 偏移量（正数往后偏移，负数往前偏移）
        :param unit: 偏移单位（"day"/"hour"/"minute"/"second"），默认"day"（天）
        :param format_: 返回时间格式，默认"YYYY-MM-DD HH:MM:SS"）
        :return: 偏移后的时间字符串
        :raises ValueError: 时间数据格式错误或单位不支持时抛出异常
        """
        pass

    @abstractmethod
    def is_in_time_range(self, time_data: Union[str, datetime, int], start_range: Union[str, datetime, int], end_range: Union[str, datetime, int]) -> bool:
        """
        判断指定时间是否在指定时间区间内
        :param time_data: 待判断时间（字符串/ datetime / 时间戳）
        :param start_range: 时间区间起始值（字符串/ datetime / 时间戳）
        :param end_range: 时间区间结束值（字符串/ datetime / 时间戳）
        :return: 在区间内返回True，否则返回False
        :raises ValueError: 时间数据格式错误时抛出异常
        """
        pass

    @abstractmethod
    def get_time_segment(self, time_data: Union[str, datetime, int], format_: str = "YYYY-MM-DD HH:MM:SS") -> str:
        """
        获取指定时间的时段（凌晨/上午/下午/晚上）
        :param time_data: 待判断时间（字符串/ datetime / 时间戳）
        :param format_: 时间字符串格式，默认"YYYY-MM-DD HH:MM:SS"（仅time_data为str时生效）
        :return: 时段字符串（凌晨/上午/下午/晚上）
        :raises ValueError: 时间数据格式错误时抛出异常
        """
        pass


class BaseUtils(ABC):
    """通用工具总抽象基类，整合所有工具类接口，供上层模块统一调用"""

    @abstractmethod
    def get_text_tool(self) -> BaseTextTool:
        """获取文本处理工具实例"""
        pass

    @abstractmethod
    def get_data_tool(self) -> BaseDataTool:
        """获取数据处理工具实例"""
        pass

    @abstractmethod
    def get_param_validate(self) -> BaseParamValidate:
        """获取参数校验工具实例"""
        pass

    @abstractmethod
    def get_assist_tool(self) -> BaseAssistTool:
        """获取通用辅助工具实例（含扩张后的时间相关方法）"""
        pass
```

## 4.3 具体实现类基础构建

具体实现类（core/impl.py）继承抽象基类BaseUtils，整合所有工具类的具体实现，将同类方法对应到各工具类中，重点完善时间相关方法的整合，确保接口实现符合规范、逻辑连贯。以下为代码基础构建（不包含具体方法实现逻辑，仅搭建类结构）：

```python
from .base import (BaseUtils, BaseTextTool, BaseDataTool,
                   BaseParamValidate, BaseAssistTool)
from typing import Any, Dict, List, Union, Optional
from datetime import datetime, timedelta


class TextTool(BaseTextTool):
    """文本处理工具类，实现BaseTextTool所有抽象方法，整合所有文本相关工具"""

    def text_clean(self, text: str) -> str:
        # 实现文本基础清洗逻辑：去除特殊字符、多余空格、换行符、制表符，统一编码
        pass

    def text_desensitize(self, text: str, type_: str) -> str:
        # 实现文本脱敏逻辑：根据类型（phone/id_card/email）隐藏敏感信息，不支持类型抛异常
        pass

    def text_validate(self, text: str, type_: str) -> bool:
        # 实现文本格式校验逻辑：校验邮箱、手机号、身份证号、URL格式
        pass


class DataTool(BaseDataTool):
    """数据处理工具类，实现BaseDataTool所有抽象方法，整合所有数据相关工具"""

    def dict_to_json(self, data: Dict) -> str:
        # 实现字典转JSON字符串逻辑，非字典类型抛异常
        pass

    def data_convert(self, data: Any, target_type: str) -> Any:
        # 实现通用数据类型转换逻辑，支持str/int/bool/datetime，不支持类型抛异常
        pass

    def list_deduplicate(self, data_list: List) -> List:
        # 实现列表去重逻辑，保留原顺序
        pass


class ParamValidate(BaseParamValidate):
    """参数校验工具类，实现BaseParamValidate所有抽象方法，整合所有参数校验工具"""

    def required_validate(self, params: Dict, required_params: List[str]) -> bool:
        # 实现必填参数校验逻辑，支持嵌套参数校验（如"user.name"）
        pass

    def range_validate(self, value: Union[int, float, str], min_val: Union[int, float, int], max_val: Union[int, float, int]) -> bool:
        # 实现范围校验逻辑，支持数值大小、字符串长度校验
        pass


class AssistTool(BaseAssistTool):
    """通用辅助工具类，实现BaseAssistTool所有抽象方法，整合所有辅助工具，重点实现扩张的时间相关方法"""

    def md5_encrypt(self, text: str) -> str:
        # 实现MD5加密逻辑
        pass

    # 时间相关方法实现（重点扩张，不包含具体逻辑）
    def get_current_time(self, format_: str = "YYYY-MM-DD HH:MM:SS", time_zone: Optional[str] = None) -> str:
        # 实现获取当前时间逻辑，支持自定义格式和时区
        pass

    def get_timestamp(self, time_str: Optional[str] = None, format_: str = "YYYY-MM-DD HH:MM:SS", millisecond: bool = False) -> int:
        # 实现获取时间戳逻辑，支持指定时间和秒级/毫秒级
        pass

    def time_convert(self, time_data: Union[str, datetime, int], target_type: str, format_: str = "YYYY-MM-DD HH:MM:SS") -> Union[str, datetime, int]:
        # 实现时间类型互转逻辑（字符串/ datetime / 时间戳）
        pass

    def time_diff_calculate(self, start_time: Union[str, datetime, int], end_time: Union[str, datetime, int], unit: str = "day") -> float:
        # 实现两个时间差值计算逻辑，支持多种单位
        pass

    def time_offset(self, time_data: Union[str, datetime, int], offset: int, unit: str = "day", format_: str = "YYYY-MM-DD HH:MM:SS") -> str:
        # 实现时间偏移计算逻辑，支持正负偏移和多种单位
        pass

    def is_in_time_range(self, time_data: Union[str, datetime, int], start_range: Union[str, datetime, int], end_range: Union[str, datetime, int]) -> bool:
        # 实现时间区间判断逻辑
        pass

    def get_time_segment(self, time_data: Union[str, datetime, int], format_: str = "YYYY-MM-DD HH:MM:SS") -> str:
        # 实现时间时段判断逻辑（凌晨/上午/下午/晚上）
        pass


class CommonUtils(BaseUtils):
    """通用工具总具体实现类，整合所有工具类实例，供上层模块统一调用"""

    def __init__(self):
        # 初始化所有工具类实例，全局可复用，包含时间相关工具实例
        self.text_tool = TextTool()
        self.data_tool = DataTool()
        self.param_validate = ParamValidate()
        self.assist_tool = AssistTool()

    def get_text_tool(self) -> BaseTextTool:
        return self.text_tool

    def get_data_tool(self) -> BaseDataTool:
        return self.data_tool

    def get_param_validate(self) -> BaseParamValidate:
        return self.param_validate

    def get_assist_tool(self) -> BaseAssistTool:
        return self.assist_tool
```

## 4.4 模块调用示例

为适配初学者使用，降低调用门槛，以下提供模块核心功能的调用示例，清晰展示各工具类的调用方式，重点包含时间相关方法的调用示例，确保开发者能快速上手、正确调用模块功能：

```python
from common_utils_module.core.impl import CommonUtils

# 初始化通用工具实例（全局只需初始化一次）
common_utils = CommonUtils()

# 1. 文本处理工具调用示例
text_tool = common_utils.get_text_tool()
clean_text = text_tool.text_clean("  测试文本！！\n\t包含特殊字符  ")
desensitized_text = text_tool.text_desensitize("13812345678", "phone")
is_email = text_tool.text_validate("test@example.com", "email")
print("清洗后文本：", clean_text)
print("脱敏后手机号：", desensitized_text)
print("是否为邮箱：", is_email)

# 2. 数据处理工具调用示例
data_tool = common_utils.get_data_tool()
json_str = data_tool.dict_to_json({"name": "test", "age": 20})
int_data = data_tool.data_convert("123", "int")
deduplicated_list = data_tool.list_deduplicate([1, 2, 2, 3, 3, 3])
print("字典转JSON：", json_str)
print("字符串转整数：", int_data)
print("列表去重后：", deduplicated_list)

# 3. 参数校验工具调用示例
param_validate = common_utils.get_param_validate()
params = {"name": "test", "age": 20, "email": "test@example.com"}
required_params = ["name", "age"]
is_required = param_validate.required_validate(params, required_params)
is_in_range = param_validate.range_validate(20, 18, 30)
print("必填参数校验：", is_required)
print("年龄是否在18-30范围内：", is_in_range)

# 4. 通用辅助工具调用示例（重点展示时间相关方法）
assist_tool = common_utils.get_assist_tool()
# 时间获取
current_time = assist_tool.get_current_time()
current_timestamp = assist_tool.get_timestamp(millisecond=True)
day_start_end = assist_tool.get_day_start_end()  # 需在impl中补充实现
print("当前时间：", current_time)
print("当前毫秒级时间戳：", current_timestamp)
print("当天起止时间：", day_start_end)

# 时间转换
time_str = "2026-02-28 10:30:00"
datetime_obj = assist_tool.time_convert(time_str, "datetime")
timestamp = assist_tool.time_convert(datetime_obj, "timestamp")
print("字符串转datetime：", datetime_obj)
print("datetime转时间戳：", timestamp)

# 时间差值计算
start_time = "2026-02-27 10:00:00"
end_time = "2026-02-28 12:30:00"
time_diff = assist_tool.time_diff_calculate(start_time, end_time, unit="hour")
print("两个时间的小时差：", time_diff)

# 时间偏移
offset_time = assist_tool.time_offset(time_str, offset=3, unit="hour")
print("3小时后时间：", offset_time)

# 时间判断
is_in_range = assist_tool.is_in_time_range(time_str, "2026-02-28 00:00:00", "2026-02-28 23:59:59")
time_segment = assist_tool.get_time_segment(time_str)
print("时间是否在指定区间：", is_in_range)
print("时间所属时段：", time_segment)

# 加密解密
encrypted = assist_tool.md5_encrypt("test123")
print("MD5加密结果：", encrypted)
```

## 4.5 异常处理规范

为确保模块稳定性，避免因异常未处理导致程序崩溃，所有工具方法需遵循统一的异常处理规范，明确异常类型和触发条件，便于上层模块捕获和处理异常，具体规范如下：

- 参数异常：当输入参数为空、类型错误、格式错误时，抛出ValueError或TypeError，异常信息需清晰说明错误原因（如"时间字符串格式错误，需符合YYYY-MM-DD HH:MM:SS"），便于开发者快速定位问题。

- 功能异常：当方法执行过程中出现无法处理的异常（如文件读取失败、时区转换失败），抛出对应异常（如FileNotFoundError），并补充详细的异常信息，便于排查问题根源。

- 异常捕获：具体实现类中不建议捕获全局异常，需将异常抛出给上层模块，由上层模块根据业务场景进行捕获和处理，确保异常处理的灵活性，适配不同业务需求。

# 5. 模块测试规范

为确保模块功能正常、稳定可靠，模块需进行全面的单元测试和集成测试，覆盖所有核心功能，重点覆盖时间相关方法，确保工具方法功能符合预期、边界条件处理到位。测试用例需遵循以下规范：

## 5.1 测试范围

- 单元测试：覆盖所有核心工具方法，包括正常输入、异常输入、边界输入（如时间格式边界、参数范围边界），确保每个方法的功能符合预期，无逻辑漏洞。

- 集成测试：测试工具类之间的协同工作，以及模块与上层模块的调用兼容性，确保模块能正常被上层模块调用，无调用异常。

## 5.2 测试用例要求

- 测试用例需清晰标注测试场景、输入参数、预期输出，便于他人查看和维护，确保测试用例的可复用性和可追溯性。

- 重点覆盖时间相关方法的测试，包括不同时间格式、时区、偏移量、时间区间的测试，确保时间方法的准确性和稳定性，避免因时间处理异常影响业务正常运行。

- 异常测试用例需覆盖所有可能抛出的异常，验证异常信息的准确性和完整性，确保异常能被正确捕获和处理。

## 5.3 测试工具与执行

- 测试工具：使用Python内置的unittest模块或pytest框架编写测试用例，确保测试代码规范、可维护，便于后续扩展和修改。

- 测试执行：每次模块修改后，需执行所有测试用例，确保修改未影响原有功能；模块发布前，需执行全量测试，确保模块稳定性，避免将问题带入生产环境。

# 6. 模块维护与扩展

为确保模块长期稳定运行、适配系统后续扩展需求，模块维护与扩展需遵循以下规范，重点关注时间相关功能的维护和扩展，保障模块的可扩展性和可维护性。

## 6.1 维护规范

- 模块维护需严格遵循编码规范和项目结构规范，修改代码后需同步更新测试用例和README.md文档，确保文档与代码一致，便于后续维护和交接。

- 发现模块bug时，需及时修复，修复后需执行所有测试用例，确保bug已解决且未引入新的问题，保障模块稳定性。

- 定期对模块进行优化，重点提升时间相关方法的执行效率，同时优化其他工具方法的性能，确保模块能适配系统业务量增长需求。

## 6.2 扩展规范

- 新增工具方法时，需先在对应的抽象基类中定义接口，再在具体实现类中实现，确保接口规范一致，避免接口混乱。

- 新增功能需分类整合到对应工具类中，避免功能混乱；若新增功能不属于现有工具类，可新增工具类，同时更新抽象基类和具体实现类，确保模块结构清晰。

- 新增时间相关方法时，需遵循现有时间方法的命名规范和参数规范，确保时间功能的统一性；新增方法后需补充对应的测试用例和调用示例，便于开发者调用和测试。

# 7. 常见问题与解决方法

针对模块使用过程中可能出现的常见问题，结合模块功能特点（重点针对时间相关问题），整理以下解决方法，帮助开发者快速排查和解决问题：

|常见问题|解决方法|
|---|---|
|时间转换时抛出格式错误异常|检查输入时间字符串的格式是否与指定格式一致；若未指定格式，默认使用"YYYY-MM-DD HH:MM:SS"格式；确保时间字符串中的年、月、日、时、分、秒符合逻辑（如月份不超过12，日期不超过当月最大天数）。|
|模块导入失败|检查模块根目录的__init__.py文件是否暴露了核心类（如CommonUtils）；检查导入路径是否正确，确保模块根目录已添加到Python环境变量中；检查模块依赖是否已安装（查看requirements.txt）。|
|时间差值计算结果不准确|检查输入的两个时间是否为同一时区；确保时间数据类型一致（如均为datetime类型或均为时间戳）；检查差值单位是否正确，避免单位混淆（如将小时差误设为天差）。|
|文本脱敏后结果不符合预期|检查脱敏类型是否正确（仅支持"phone"、"id_card"、"email"）；检查输入文本是否符合对应脱敏类型的格式（如手机号需为11位数字）。|

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
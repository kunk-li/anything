# 基础支撑层 - 配置管理模块（config_module）设计文档

# 1. 文档概述

## 1.1 文档目的

本文档为RAG与Agent系统基础支撑层配置管理模块（config_module）的独立设计文档，遵循系统整体架构规范，用于指导开发人员（含初学者）进行该模块的独立开发、测试与集成。文档明确了模块功能、接口定义、项目结构、配置规范及调用方式，确保模块开发符合系统统一标准，可无缝对接其他依赖模块。

## 1.2 适用人群

本团队所有开发人员（含资深开发者与初学者）、测试人员，作为该模块开发、测试、部署及维护的唯一标准依据。

## 1.3 模块定位与依赖

模块定位：基础支撑层核心模块之一，统一管理系统所有配置（全局配置、各模块专属配置），为所有上层模块提供配置加载、读取、更新能力，是系统正常运行的基础依赖。

依赖关系：无模块依赖（可被其他所有模块依赖，为其提供配置服务）；依赖系统统一编码规范、项目结构规范。

# 2. 模块核心功能

本模块核心负责系统配置的全生命周期管理，具体功能如下：

- 配置加载：支持从指定路径加载yaml格式配置文件，默认加载模块自身config目录下的配置文件，适配不同环境（开发、测试、生产）；新增配置热加载能力，支持配置文件修改后自动重新加载（可配置开关），无需重启系统即可生效。

- 配置读取：支持读取单级、多级配置键，提供默认值兜底，确保配置读取的灵活性与稳定性；新增配置键模糊匹配、批量读取功能，支持按前缀批量获取相关配置，提升配置读取效率。

- 配置更新：支持在内存中更新配置值（不写入配置文件），满足运行时临时配置调整需求；新增配置持久化更新选项（可选择是否写入配置文件），支持批量更新配置，同时记录配置更新日志（包含更新人、更新时间、新旧值），便于追溯。

- 配置校验：隐含配置文件存在性校验，当配置文件缺失时抛出标准化异常，适配系统统一异常处理规范；新增配置值类型校验、范围校验、必填项校验（可通过配置文件标注校验规则），校验失败时抛出明确异常信息，降低开发调试成本。

- 配置备份与恢复：支持手动/自动备份配置文件（自动备份可配置备份频率、备份保留数量），当配置误改或异常时，可快速恢复至历史备份版本，提升配置安全性。

- 敏感配置加密：针对配置文件中的敏感信息（如API密钥、密码），支持对称加密存储，模块加载时自动解密，避免敏感信息明文泄露，适配系统安全规范。

- 配置多源加载：新增支持从环境变量、命令行参数、远程配置中心（如Nacos、Apollo）加载配置，多源配置自动合并，优先级可自定义，适配复杂部署场景。

# 3. 统一项目结构

严格遵循系统全局项目结构规范，模块目录结构如下（开发者需严格遵循，不得随意修改目录名称与层级）：

```plain text
config_module/                  # 模块根目录（全小写，多单词用下划线连接）
├── __init__.py               # 模块初始化文件，暴露模块核心类/方法（必须包含，不能为空）
├── core/                     # 核心逻辑目录（存放模块核心实现，含ABC抽象类）
│   ├── __init__.py
│   ├── base.py               # 抽象基类（ABC）文件，定义模块核心接口（必须包含）
│   └── impl.py               # 具体实现类文件，继承base.py中的抽象类（必须包含）
├── utils/                    # 模块工具目录（存放模块专属工具函数，无则空目录）
│   ├── __init__.py
│   └── tool_functions.py     # 工具函数文件（如配置格式校验辅助函数）
├── config/                   # 模块配置目录（存放模块专属配置文件）
│   ├── __init__.py
│   └── config.yaml           # 配置文件（yaml格式，存储系统全局及各模块配置）
├── tests/                    # 测试目录（存放模块单元测试用例，必须包含）
│   ├── __init__.py
│   └── test_impl.py          # 具体实现类测试用例（必须包含，覆盖核心功能）
└── README.md                 # 模块说明文档（必须包含，适配初学者）
```

## 3.1 目录结构说明

- config_module：模块根目录，名称严格遵循“全小写、多单词下划线连接”规则，与模块功能精准对应。

- __init__.py：每个目录必须包含，核心作用是将目录标识为Python模块；根目录的__init__.py需暴露模块核心类/方法（如from .core.impl import ConfigManager），方便其他模块调用。

- core目录：模块核心逻辑存放处，base.py定义抽象基类（ABC），规范模块必须实现的接口方法；impl.py是具体实现类，继承抽象基类并实现所有抽象方法。

- utils目录：模块专属工具函数目录，存放配置解析辅助、格式校验等工具函数，不包含核心业务逻辑，仅为模块提供辅助支撑。

- config目录：存放模块专属配置文件，核心为config.yaml，用于存储系统全局配置及各模块专属配置，支持环境差异化配置。

- tests目录：测试用例存放处，必须包含test_impl.py，覆盖配置加载、读取、更新等核心功能的单元测试，初学者可参考示例编写简单测试用例。

- README.md：模块说明文档，需详细说明模块功能、核心接口、使用方法、依赖项、常见问题，语言简洁易懂，适配初学者快速上手。

# 4. 编码规范

严格遵循系统统一编码规范，具体要求如下：

## 4.1 基础格式

编码格式：UTF-8，缩进采用4个空格（禁止使用Tab），每行代码长度不超过120字符。

## 4.2 命名规范

- 类名：大驼峰命名法（如BaseConfigManager、ConfigManager）；

- 方法名/函数名：小驼峰命名法（如load_config、get_config）；

- 变量名：小驼峰命名法（如config、config_path）；

- 常量名：全大写，多单词用下划线连接（如DEFAULT_CONFIG_PATH）；

- 模块名/目录名：全小写，多单词用下划线连接（如config_module、core）。

## 4.3 注释规范

- 类注释：使用文档字符串（"""），说明类的功能、参数、返回值（若有）；

- 方法/函数注释：使用文档字符串，说明功能、参数（名称、类型、含义）、返回值（类型、含义）、异常（若有）；

- 关键代码注释：对复杂逻辑、不易理解的代码，添加单行注释（#），说明逻辑用途。

## 4.4 依赖管理

模块依赖项统一写入根目录的requirements.txt文件，注明依赖包名称与版本（如pyyaml==6.0.1），避免版本冲突，确保所有开发人员使用一致的依赖环境。

# 5. 模块核心接口设计

## 5.1 抽象基类（core/base.py）

定义配置管理模块的核心接口，强制子类实现，保障模块一致性，基础代码构建如下：

```python
from abc import ABC, abstractmethod
from typing import Any, Dict, List

class BaseConfigManager(ABC):
    """配置管理抽象基类，定义配置加载、读取、更新接口"""

    @abstractmethod
    def load_config(self, config_path: str = None) -> None:
        """
        加载配置文件
        :param config_path: 配置文件路径（默认读取模块config目录下的config.yaml）
        :raises FileNotFoundError: 配置文件不存在时抛出异常
        :raises ValueError: 配置格式错误或校验失败时抛出异常
        """
        pass

    @abstractmethod
    def get_config(self, key: str, default: Any = None) -> Any:
        """
        读取配置值
        :param key: 配置键（支持多级键，如"vector_db.host"）；支持前缀模糊匹配（如"vector_db."获取所有子配置）
        :param default: 默认值（当配置键不存在时返回）
        :return: 配置值（单个值或批量配置字典）
        """
        pass

    @abstractmethod
    def update_config(self, key: str, value: Any, persist: bool = False) -> bool:
        """
        更新配置值
        :param key: 配置键（支持多级键）
        :param value: 新的配置值
        :param persist: 是否持久化到配置文件（默认False，仅内存更新）
        :return: 更新成功返回True，失败返回False
        """
        pass

    @abstractmethod
    def validate_config(self, validate_rules: Dict = None) -> bool:
        """
        配置校验（支持自定义校验规则）
        :param validate_rules: 自定义校验规则，格式：{配置键: {type: 类型, required: 是否必填, range: 范围}}
        :return: 校验通过返回True，失败抛出异常
        """
        pass

    @abstractmethod
    def backup_config(self, backup_path: str = None) -> str:
        """
        备份配置文件
        :param backup_path: 备份文件路径（默认备份至config/backup目录）
        :return: 备份文件完整路径
        """
        pass

    @abstractmethod
    def restore_config(self, backup_path: str) -> bool:
        """
        恢复配置文件
        :param backup_path: 备份文件路径
        :return: 恢复成功返回True，失败返回False
        """
        pass

    @abstractmethod
    def encrypt_sensitive_config(self, keys: List[str], secret_key: str) -> None:
        """
        加密敏感配置
        :param keys: 需要加密的配置键列表（如["vector_db.api_key", "llm.api_key"]）
        :param secret_key: 加密密钥（需与系统安全密钥统一）
        """
        pass

    @abstractmethod
    def load_remote_config(self, remote_url: str, config_type: str = "yaml") -> None:
        """
        加载远程配置中心配置
        :param remote_url: 远程配置中心地址
        :param config_type: 配置格式（默认yaml）
        """
        pass

```

## 5.2 具体实现类（core/impl.py）

继承抽象基类，实现所有抽象方法，基础代码构建如下（不包含具体方法实现细节）：

```python
import yaml
import os
import time
import shutil
from cryptography.fernet import Fernet
import requests
from .base import BaseConfigManager
from typing import Any, Dict, List, Optional

class ConfigManager(BaseConfigManager):
    """配置管理具体实现类，基于yaml配置文件，支持多源加载、敏感加密、备份恢复等功能"""

    def __init__(self):
        self.config: Dict[str, Any] = {}  # 存储加载的配置
        self.config_path: str = None      # 配置文件路径
        self.hot_reload: bool = False    # 热加载开关
        self.hot_reload_interval: int = 5  # 热加载检查间隔（秒）
        self.last_modify_time: float = 0  # 配置文件最后修改时间
        self.backup_dir: str = "config/backup"  # 备份目录
        self.secret_key: str = None      # 敏感配置加密密钥

    def load_config(self, config_path: str = None) -> None:
        # 实现配置文件加载逻辑，默认读取模块config目录下的config.yaml
        # 新增热加载初始化、配置校验、敏感配置解密逻辑
        pass

    def get_config(self, key: str, default: Any = None) -> Any:
        # 实现配置读取逻辑，支持多级键拆分、默认值兜底、前缀模糊匹配、批量读取
        pass

    def update_config(self, key: str, value: Any, persist: bool = False) -> bool:
        # 实现内存中配置更新、持久化写入、更新日志记录逻辑
        pass

    def validate_config(self, validate_rules: Dict = None) -> bool:
        # 实现配置类型、必填项、范围校验逻辑，默认使用配置文件中内置的校验规则
        pass

    def backup_config(self, backup_path: str = None) -> str:
        # 实现配置文件备份逻辑，自动创建备份目录，按时间戳命名备份文件
        pass

    def restore_config(self, backup_path: str) -> bool:
        # 实现从备份文件恢复配置的逻辑，恢复前自动备份当前配置
        pass

    def encrypt_sensitive_config(self, keys: List[str], secret_key: str) -> None:
        # 实现敏感配置加密逻辑，使用Fernet对称加密，加密后覆盖原配置并持久化
        pass

    def load_remote_config(self, remote_url: str, config_type: str = "yaml") -> None:
        # 实现从远程配置中心加载配置的逻辑，与本地配置合并，按优先级覆盖
        pass

    def _check_hot_reload(self):
        # 私有方法：热加载检查，定时检测配置文件修改时间，有变化则重新加载
        pass

    def _record_update_log(self, key: str, old_value: Any, new_value: Any):
        # 私有方法：记录配置更新日志，包含更新时间、更新键、新旧值
        pass

```

# 6. 配置文件规范

## 6.1 配置文件格式

采用yaml格式，命名为config.yaml，存放于config目录下，支持多级配置、环境差异化配置，格式清晰、易维护。

## 6.2 配置文件示例

```yaml
# 全局配置
global:
  env: "development"  # 环境：development（开发）、test（测试）、production（生产）
  log_level: "INFO"   # 日志级别：DEBUG、INFO、WARNING、ERROR、CRITICAL
  hot_reload: true    # 配置热加载开关（true/false）
  hot_reload_interval: 5  # 热加载检查间隔（秒）
  backup_enabled: true  # 自动备份开关
  backup_interval: 24  # 自动备份间隔（小时）
  backup_retention: 7  # 备份文件保留数量（天）

# 配置校验规则（与配置项一一对应，支持type、required、range校验）
validate_rules:
  global.env:
    type: "str"
    required: true
    range: ["development", "test", "production"]
  vector_db.vector_dimension:
    type: "int"
    required: true
    range: [128, 2048]
  llm.temperature:
    type: "float"
    required: true
    range: [0.0, 1.0]
  security.auth_enabled:
    type: "bool"
    required: true

# 向量数据库配置
vector_db:
  type: "faiss"       # 向量数据库类型（pinecone、chroma、faiss等）
  host: "localhost"
  api_key: "${VECTOR_DB_API_KEY}"  # 敏感信息从环境变量注入（或加密存储）
  index_name: "rag-agent-index"
  vector_dimension: 768  # 向量维度（与embedding模型一致）

# Embedding模型配置
embedding:
  model_name: "sentence-transformers/all-MiniLM-L6-v2"
  max_length: 512

# 大模型配置
llm:
  model_name: "gpt-3.5-turbo"
  api_key: "${LLM_API_KEY}"  # 敏感信息从环境变量注入（或加密存储）
  temperature: 0.7  # 生成温度，0-1，值越小越稳定

# Agent配置
agent:
  max_retries: 3  # 工具调用最大重试次数
  timeout: 30     # 工具调用超时时间（秒）

# 文档存储配置
document_store:
  dir: "documents"

# 状态存储配置
state_store:
  dir: "state_store"

# 安全配置
security:
  auth_enabled: true
  auth_type: "apikey"  # apikey / jwt / none
  api_keys:
    - "${API_KEY_1}"
  jwt_secret: "${JWT_SECRET}"
  sensitive_config_secret: "${SENSITIVE_CONFIG_SECRET}"  # 敏感配置加密密钥

# 远程配置中心配置（可选，开启远程加载时生效）
remote_config:
  enabled: false
  url: "http://nacos-server:8848/nacos/v1/cs/configs"
  data_id: "rag-agent-config"
  group: "DEFAULT_GROUP"
  config_type: "yaml"
```

## 6.3 配置加载规则

- 默认加载路径：模块config目录下的config.yaml，若未指定config_path，自动读取该路径。

- 环境变量注入：支持通过环境变量覆盖配置文件中的敏感值，格式为${ENV_VAR}，配置管理模块需实现环境变量解析逻辑。

- 配置优先级：环境变量 > 配置文件 > 默认值，确保运行时可灵活调整配置。

# 7. 接口调用示例

提供简洁易懂的接口调用示例，适配初学者快速使用该模块，示例如下：

```python
from config_module.core.impl import ConfigManager

# 初始化配置管理器（全局只需初始化一次）
config_manager = ConfigManager()

# 1. 基础配置加载（默认加载config/config.yaml）
config_manager.load_config()

# 2. 远程配置加载（开启远程配置时使用）
# config_manager.load_remote_config(remote_url="http://nacos-server:8848/nacos/v1/cs/configs")

# 3. 敏感配置加密（首次使用或敏感信息更新时执行）
# secret_key = "your-sensitive-secret-key"  # 建议从环境变量获取
# config_manager.encrypt_sensitive_config(
#     keys=["vector_db.api_key", "llm.api_key", "security.jwt_secret"],
#     secret_key=secret_key
# )

# 4. 配置读取示例
# 4.1 读取单级/多级配置
env = config_manager.get_config("global.env")
print(env)  # 输出：development
vector_db_host = config_manager.get_config("vector_db.host")
print(vector_db_host)  # 输出：localhost

# 4.2 读取配置并指定默认值
max_retries = config_manager.get_config("agent.max_retries", 5)
print(max_retries)  # 输出：3（配置文件中存在，返回配置值）

# 4.3 批量读取配置（按前缀匹配）
vector_db_config = config_manager.get_config("vector_db.")
print(vector_db_config)  # 输出：向量数据库相关所有配置字典

# 5. 配置更新示例
# 5.1 仅内存更新
update_result = config_manager.update_config("llm.temperature", 0.5)
print(update_result)  # 输出：True（更新成功）
print(config_manager.get_config("llm.temperature"))  # 输出：0.5

# 5.2 持久化更新（写入配置文件）
update_result = config_manager.update_config("agent.timeout", 60, persist=True)
print(update_result)  # 输出：True（更新成功且写入文件）

# 6. 配置备份与恢复示例
# 6.1 手动备份配置
backup_path = config_manager.backup_config()
print(f"配置备份至：{backup_path}")

# 6.2 恢复配置（从备份文件）
# restore_result = config_manager.restore_config(backup_path=backup_path)
# print(f"配置恢复：{'成功' if restore_result else '失败'}")

# 7. 配置校验示例
# 自定义校验规则（可选，未指定则使用配置文件中validate_rules）
validate_rules = {
    "llm.temperature": {"type": "float", "required": True, "range": [0.0, 1.0]},
    "vector_db.vector_dimension": {"type": "int", "required": True, "range": [128, 2048]}
}
try:
    config_manager.validate_config(validate_rules)
    print("配置校验通过")
except ValueError as e:
    print(f"配置校验失败：{e}")
```

# 8. 测试用例规范

测试用例需覆盖模块核心功能，存放于tests/test_impl.py，基础代码构建如下（不包含具体测试细节）：

```python
import unittest
import os
import tempfile
from config_module.core.impl import ConfigManager

class TestConfigManager(unittest.TestCase):
    def setUp(self):
        """测试前置：初始化配置管理器，创建临时配置文件"""
        self.config_manager = ConfigManager()
        # 创建临时配置文件
        self.temp_config_dir = tempfile.mkdtemp()
        self.temp_config_path = os.path.join(self.temp_config_dir, "config.yaml")
        with open(self.temp_config_path, "w", encoding="utf-8") as f:
            f.write("""
global:
  env: "test"
  hot_reload: false
llm:
  temperature: 0.7
vector_db:
  vector_dimension: 768
validate_rules:
  llm.temperature:
    type: "float"
    range: [0.0, 1.0]
            """)

    def tearDown(self):
        """测试后置：清理临时文件"""
        import shutil
        shutil.rmtree(self.temp_config_dir)

    def test_load_config(self):
        """测试配置文件加载功能（正常加载、文件不存在、格式错误）"""
        # 正常加载
        self.config_manager.load_config(self.temp_config_path)
        self.assertEqual(self.config_manager.get_config("global.env"), "test")

        # 测试文件不存在
        with self.assertRaises(FileNotFoundError):
            self.config_manager.load_config("non_existent.yaml")

    def test_get_config(self):
        """测试配置读取功能（单级、多级、默认值、批量读取）"""
        self.config_manager.load_config(self.temp_config_path)
        # 单级配置
        self.assertEqual(self.config_manager.get_config("global.env"), "test")
        # 多级配置（不存在，返回默认值）
        self.assertEqual(self.config_manager.get_config("global.non_existent", "default"), "default")
        # 批量读取
        llm_config = self.config_manager.get_config("llm.")
        self.assertIn("temperature", llm_config)

    def test_update_config(self):
        """测试配置更新功能（内存更新、持久化更新）"""
        self.config_manager.load_config(self.temp_config_path)
        # 内存更新
        self.assertTrue(self.config_manager.update_config("llm.temperature", 0.5))
        self.assertEqual(self.config_manager.get_config("llm.temperature"), 0.5)
        # 持久化更新
        self.assertTrue(self.config_manager.update_config("global.env", "production", persist=True))
        # 重新加载验证
        self.config_manager.load_config(self.temp_config_path)
        self.assertEqual(self.config_manager.get_config("global.env"), "production")

    def test_validate_config(self):
        """测试配置校验功能（类型校验、范围校验、必填项校验）"""
        self.config_manager.load_config(self.temp_config_path)
        # 校验通过
        self.assertTrue(self.config_manager.validate_config())
        # 篡改配置，校验失败
        self.config_manager.update_config("llm.temperature", 1.5)
        with self.assertRaises(ValueError):
            self.config_manager.validate_config()

    def test_backup_and_restore_config(self):
        """测试配置备份与恢复功能"""
        self.config_manager.load_config(self.temp_config_path)
        # 备份
        backup_path = self.config_manager.backup_config(backup_path=self.temp_config_dir)
        self.assertTrue(os.path.exists(backup_path))
        # 修改配置
        self.config_manager.update_config("global.env", "modified", persist=True)
        # 恢复
        self.assertTrue(self.config_manager.restore_config(backup_path))
        self.assertEqual(self.config_manager.get_config("global.env"), "test")

    def test_encrypt_sensitive_config(self):
        """测试敏感配置加密功能"""
        self.config_manager.load_config(self.temp_config_path)
        secret_key = "test_secret_key_123456"
        # 加密敏感配置
        self.config_manager.encrypt_sensitive_config(keys=["llm.temperature"], secret_key=secret_key)
        # 验证加密后的值（非明文）
        encrypted_value = self.config_manager.get_config("llm.temperature")
        self.assertNotEqual(encrypted_value, 0.7)
        # 重新加载后自动解密
        self.config_manager.load_config(self.temp_config_path)
        self.assertEqual(self.config_manager.get_config("llm.temperature"), 0.7)

if __name__ == "__main__":
    unittest.main()
```

# 9. 模块交付物清单（强制）

模块开发完成后，需提交以下交付物，确保符合系统统一交付规范：

- core/base.py：抽象基类（ABC抽象接口），包含新增功能的抽象方法；

- core/impl.py：具体实现类，实现所有抽象方法（含新增优化功能）；

- tests/test_impl.py：核心功能单元测试用例，覆盖新增的热加载、备份恢复、敏感加密等功能；

- README.md：模块说明文档（面向初学者，包含功能、接口、使用方法，补充新增功能说明）；

- requirements.txt：模块依赖包（固定版本，新增cryptography、requests等依赖）；

- config/config.yaml：配置文件示例，包含新增的热加载、备份、校验规则、远程配置等相关配置项；

- utils/tool_functions.py：新增工具函数（如加密解密工具、远程配置请求工具、配置校验工具）。

# 10. 可替换性约束（强制）

- 其他模块只能依赖本模块的抽象接口（BaseConfigManager）或统一输出结构，禁止直接引用impl.py中的内部私有方法。

- 若需替换配置管理实现（如改用JSON配置文件、远程配置服务），只需实现BaseConfigManager抽象接口，保持接口一致性，上层模块无需修改代码。

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
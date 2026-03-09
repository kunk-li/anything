# 核心业务层 - Embedding 模块（embedding_module）设计说明书

# 1. 文档概述

## 1.1 文档目的

本文档为RAG 与 Agent 系统核心业务层 - Embedding 模块的独立、完整设计说明书，严格遵循系统整体架构规范，明确模块功能、项目结构、接口定义、依赖关系、数据格式及开发要求，用于指导开发人员（含初学者）进行该模块的独立开发、测试与集成，确保模块与系统无缝兼容、可扩展、可替换。

## 1.2 适用人群

本团队所有开发人员（含资深开发者与初学者）、测试人员，作为 Embedding 模块开发、测试、维护的唯一标准依据；项目管理人员可参考本说明书进行模块开发进度管控。

## 1.3 核心需求回顾

- 模块功能：将单条 / 批量文本转换为标准化向量，支持向量归一化，为 RAG 模块、向量数据库模块提供核心向量化能力，屏蔽不同厂商向量模型的接口差异。

- 开发语言：Python 3.10+，与系统整体保持一致。

- 开发模式：独立开发、互不依赖，基于本说明书即可完成开发，开发完成后通过统一接口集成至核心业务层。

- 文档要求：详细、易懂，适配初学者，明确模块所有可提前定义的内容（接口、数据格式、项目结构等）。

- 模块约束：需包含抽象基类（ABC），确保模块一致性；代码可与系统其他模块交换，数据格式符合系统统一标准。

## 1.4 术语定义

|术语|定义|
|---|---|
|Embedding|文本嵌入，将文本转换为高维向量，用于向量检索与语义匹配，是 RAG 系统的核心基础能力。|
|向量模型|生成文本向量的大模型（如 OpenAI text-embedding-ada-002、本地 Sentence-BERT 等），由大模型对接模块提供适配。|
|ABC|抽象基类，定义模块的核心接口与方法，强制子类实现，保障模块一致性。|
|归一化|将向量长度缩放为 1，用于提升余弦相似度计算的准确性，是向量检索的标准预处理操作。|
# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于系统核心业务层，是 RAG 模块的核心依赖，仅负责文本向量化，不涉及文件解析、存储、检索操作：

- 接收单条 / 批量文本输入，调用大模型对接模块的向量模型适配器生成向量；

- 提供向量归一化、格式校验、异常处理能力；

- 输出标准化向量结果，供 RAG 模块、向量数据库模块直接使用；

- 屏蔽不同厂商向量模型的接口差异，实现模型无缝切换。

## 2.2 输入输出规范

### 2.2.1 输入

- 单条文本向量化：text: str（任意长度文本，模块自动适配模型输入限制）；

- 批量文本向量化：texts: List[str]（批量文本列表，支持批量处理提升效率）；

- 可选参数：model_name（指定向量模型名称，默认使用系统配置）、normalize（是否归一化，默认 True）。

### 2.2.2 输出

- 单条文本输出：List[float]（一维浮点型向量列表，维度与向量数据库配置一致）；

- 批量文本输出：List[List[float]]（二维浮点型向量列表，每个元素对应一条文本的向量）；

- 异常输出：遵循系统统一异常码规范，抛出标准化异常。

## 2.3 依赖关系

本模块依赖系统基础支撑层与数据层 - 大模型对接模块，是核心业务层与数据层的桥梁：

### 基础支撑层依赖：

1. 通用工具模块（common_utils_module）：文本清洗、格式校验；

2. 配置管理模块（config_module）：读取向量模型配置、维度、批量大小；

3. 日志模块（log_module）：记录向量化日志、异常信息；

4. 异常处理模块（exception_module）：抛出标准化异常。

### 数据层依赖：

1. 大模型对接模块（llm_adapter_module）：调用向量模型适配器，生成文本向量。

本模块不依赖文档解析、文档存储、向量数据库模块，仅为 RAG 模块提供向量化服务。

# 3. 统一项目结构规范

严格遵循系统整体项目结构规范，模块根目录命名为embedding_module（全小写，多单词用下划线连接），目录结构如下，开发者不得随意修改目录名称与层级，初学者可直接复制该结构搭建项目。

```plaintext
embedding_module/                  # 模块根目录
├── __init__.py                    # 模块初始化文件，暴露核心类/方法
├── core/                          # 核心逻辑目录（抽象基类+实现类）
│   ├── __init__.py
│   ├── base.py                    # 抽象基类（ABC），定义核心接口
│   └── impl.py                    # 具体实现类，继承抽象基类
├── model/                         # 数据模型目录（统一请求/响应模型）
│   ├── __init__.py
│   └── data_model.py              # 向量请求/响应标准化模型
├── utils/                         # 模块专属工具函数
│   ├── __init__.py
│   └── tool_functions.py          # 向量归一化、格式校验等工具
├── config/                        # 模块专属配置
│   ├── __init__.py
│   └── config.py                  # 读取全局配置，补充模块专属配置
├── tests/                         # 测试用例目录
│   ├── __init__.py
│   └── test_impl.py               # 核心功能测试用例
└── README.md                      # 模块说明文档（适配初学者）
```

## 3.1 目录结构说明

- embedding_module：模块根目录，名称固定，与功能精准对应；

- __init__.py：每个目录必须包含，根目录暴露核心类（如STEmbedding、EmbeddingService），方便其他模块调用；

- core：核心逻辑目录，base.py定义抽象接口，impl.py实现具体逻辑；

- model：模块专属数据模型，定义向量请求、响应的标准化格式，确保数据统一；

- utils：模块专属工具函数，向量归一化、格式校验、批量处理辅助操作；

- config：读取全局向量配置，补充模块专属参数（如批量大小、归一化开关）；

- tests：覆盖单条 / 批量向量化、异常场景、模型切换的测试用例；

- README.md：详细说明模块功能、接口、使用方法、依赖项、扩展步骤。

# 4. 核心数据模型设计

本模块定义统一的向量请求 / 响应模型，所有接口均基于该模型交互，确保模块内部及与外部模块的数据格式统一，遵循系统整体数据规范。

## 4.1 向量请求模型（EmbeddingRequest）

```python
from typing import List, Optional
from dataclasses import dataclass

@dataclass
class EmbeddingRequest:
    """向量生成统一请求模型"""
    # 输入类型：SINGLE（单条文本）、BATCH（批量文本）
    input_type: str
    # 单条输入文本（input_type=SINGLE时必填）
    single_text: Optional[str] = None
    # 批量输入文本列表（input_type=BATCH时必填）
    batch_texts: Optional[List[str]] = None
    # 向量模型名称（默认使用系统配置）
    model_name: Optional[str] = None
    # 是否归一化向量（默认True）
    normalize: bool = True
    # 批量处理大小（默认使用系统配置）
    batch_size: Optional[int] = None
```

## 4.2 向量响应模型（EmbeddingResponse）

```python
from typing import List, Optional
from dataclasses import dataclass

@dataclass
class EmbeddingResponse:
    """向量生成统一响应模型，遵循系统统一异常码规范"""
    # 响应码：SUCCESS（成功）、系统异常码（失败）
    code: str
    # 响应信息：成功为"ok"，失败为具体错误信息
    message: str
    # 向量结果：单条为一维列表，批量为二维列表
    vector_result: Optional[List[List[float]]] = None
    # 调用耗时（秒，可选）
    cost_time: Optional[float] = None
    # 链路追踪ID（可选）
    trace_id: Optional[str] = None
```

# 5. 核心接口设计（抽象基类）

## 5.1 Embedding 抽象基类（BaseEmbedding）

定义模块核心接口，强制所有实现类必须实现，保障模块一致性、可替换性：

```python
from abc import ABC, abstractmethod
from typing import List
from embedding_module.model.data_model import EmbeddingRequest, EmbeddingResponse

class BaseEmbedding(ABC):
    """Embedding模块抽象基类，所有向量生成实现类必须继承此类"""

    @abstractmethod
    def embed_text(self, text: str) -> List[float]:
        """
        单条文本向量化
        :param text: 输入文本
        :return: 一维浮点型向量列表
        :raises RAGException: 向量化失败时抛出标准化异常
        """
        pass

    @abstractmethod
    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """
        批量文本向量化
        :param texts: 批量输入文本列表
        :return: 二维浮点型向量列表
        :raises RAGException: 向量化失败时抛出标准化异常
        """
        pass

    @abstractmethod
    def call_embedding(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """
        统一向量化调用接口（对外核心入口）
        :param request: 向量请求模型
        :return: 向量响应模型
        """
        pass
```

# 6. 核心实现设计

## 6.1 本地 Sentence-BERT 实现类（STEmbedding）

继承抽象基类，实现本地向量模型（Sentence-BERT）的向量化逻辑，不依赖外部 API，适合开发 / 测试环境，基础代码构建如下：

```python
from typing import List
from embedding_module.core.base import BaseEmbedding
from embedding_module.model.data_model import EmbeddingRequest, EmbeddingResponse
from common_utils_module.core.impl import CommonUtils
from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from exception_module.core.impl import RAGException

# 本地向量模型依赖（写入requirements.txt）
try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    SentenceTransformer = None

class STEmbedding(BaseEmbedding):
    """本地Sentence-BERT向量模型实现类，适合开发/测试环境"""

    def __init__(self):
        """初始化配置、日志、模型，加载系统向量配置"""
        self.utils = CommonUtils()
        self.logger = SystemLogger()
        self.config = ConfigManager()
        self.config.load_config()

        # 从系统配置读取核心参数
        self.model_name = self.config.get_config("embedding.model_name", "all-MiniLM-L6-v2")
        self.vector_dim = self.config.get_config("vector_db.vector_dimension", 768)
        self.default_normalize = self.config.get_config("llm.common.normalize_vector", True)
        self.default_batch_size = self.config.get_config("llm.common.batch_size", 32)

        # 校验模型依赖
        if SentenceTransformer is None:
            raise RAGException("EMBEDDING_INIT_FAILED", "未安装sentence-transformers，请执行：pip install sentence-transformers")

        # 初始化本地向量模型
        try:
            self.model = SentenceTransformer(self.model_name)
            self.logger.info(f"本地Embedding模型初始化成功：{self.model_name}，向量维度：{self.vector_dim}")
        except Exception as e:
            raise RAGException("EMBEDDING_INIT_FAILED", f"本地Embedding模型初始化失败：{str(e)}")

    def embed_text(self, text: str) -> List[float]:
        """实现抽象方法：单条文本向量化，自动文本清洗+归一化"""
        pass

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """实现抽象方法：批量文本向量化，支持批量处理+归一化"""
        pass

    def call_embedding(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """实现抽象方法：统一向量化调用入口，请求校验+异常封装"""
        pass
```

## 6.2 大模型对接适配实现类（LLMEmbedding）

继承抽象基类，对接系统大模型对接模块，调用远程向量模型（如 OpenAI、智谱 AI），适合生产环境，基础代码构建如下：

```python
from typing import List
from embedding_module.core.base import BaseEmbedding
from embedding_module.model.data_model import EmbeddingRequest, EmbeddingResponse
from llm_adapter_module.core.impl import LLMService
from common_utils_module.core.impl import CommonUtils
from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from exception_module.core.impl import RAGException

class LLMEmbedding(BaseEmbedding):
    """远程向量模型实现类：对接大模型对接模块，调用远程向量API（生产环境推荐）"""

    def __init__(self):
        """初始化大模型服务、配置、日志"""
        self.utils = CommonUtils()
        self.logger = SystemLogger()
        self.config = ConfigManager()
        self.config.load_config()
        self.llm_service = LLMService()  # 对接大模型统一服务

        # 读取系统默认向量模型配置
        self.default_vector_model = self.config.get_config("llm.default_vector_model", "text-embedding-ada-002")
        self.vector_dim = self.config.get_config("vector_db.vector_dimension", 768)

    def embed_text(self, text: str) -> List[float]:
        """实现抽象方法：单条文本向量化，调用远程向量模型"""
        pass

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        """实现抽象方法：批量文本向量化，调用远程向量模型"""
        pass

    def call_embedding(self, request: EmbeddingRequest) -> EmbeddingResponse:
        """实现抽象方法：统一向量化调用入口，对接大模型服务"""
        pass
```

# 7. 模块调用示例

```python
from embedding_module.core.impl import STEmbedding, LLMEmbedding
from embedding_module.model.data_model import EmbeddingRequest

# 1. 本地向量模型调用（开发/测试）
local_embedding = STEmbedding()
# 单条向量化
single_vector = local_embedding.embed_text("RAG系统核心业务层设计")
# 批量向量化
batch_vectors = local_embedding.embed_texts(["文本1", "文本2", "文本3"])

# 2. 远程向量模型调用（生产）
remote_embedding = LLMEmbedding()
# 统一接口调用
request = EmbeddingRequest(
    input_type="BATCH",
    batch_texts=["测试文本1", "测试文本2"],
    normalize=True
)
response = remote_embedding.call_embedding(request)
if response.code == "SUCCESS":
    print(f"批量向量化成功，生成{len(response.vector_result)}个向量")
```

# 8. 测试规范

## 8.1 测试范围

- 单条 / 批量文本向量化功能；

- 向量归一化、格式校验；

- 模型初始化、配置检查；

- 异常场景（依赖缺失、文本为空、模型调用失败）；

- 本地 / 远程模型切换测试。

## 8.2 测试用例基础框架

```python
import unittest
from embedding_module.core.impl import STEmbedding, LLMEmbedding
from exception_module.core.impl import RAGException

class TestEmbeddingModule(unittest.TestCase):
    """Embedding模块单元测试，覆盖核心功能与异常场景"""

    def setUp(self):
        self.local_embedding = STEmbedding()
        self.remote_embedding = LLMEmbedding()
        self.test_text = "RAG系统Embedding模块测试文本"
        self.test_batch_texts = ["测试文本1", "测试文本2", "测试文本3"]

    def test_local_embed_single(self):
        """测试本地单条文本向量化"""
        vector = self.local_embedding.embed_text(self.test_text)
        self.assertEqual(len(vector), 768)  # 校验向量维度

    def test_local_embed_batch(self):
        """测试本地批量文本向量化"""
        vectors = self.local_embedding.embed_texts(self.test_batch_texts)
        self.assertEqual(len(vectors), 3)

    def test_remote_embed(self):
        """测试远程向量模型调用"""
        vector = self.remote_embedding.embed_text(self.test_text)
        self.assertEqual(len(vector), 768)

    def test_empty_text_embed(self):
        """测试空文本向量化，验证异常抛出"""
        with self.assertRaises(RAGException):
            self.local_embedding.embed_text("")

if __name__ == "__main__":
    unittest.main()
```

# 9. 交付物清单（强制）

1. core/base.py：抽象基类，定义 Embedding 核心接口；

2. core/impl.py：具体实现类（本地 + 远程向量模型）；

3. model/data_model.py：向量请求 / 响应标准化数据模型；

4. utils/tool_functions.py：向量归一化、格式校验工具函数；

5. config/config.py：模块配置读取逻辑；

6. tests/test_impl.py：核心功能测试用例；

7. README.md：模块说明文档（适配初学者）；

8. requirements.txt：依赖包清单（sentence-transformers 等）。

# 10. 可替换性约束（强制）

1. 上层模块（RAG）仅依赖BaseEmbedding抽象接口，禁止直接引用具体实现类；

2. 新增向量模型（如智谱 AI、文心一言）仅需实现BaseEmbedding抽象接口，无需修改上层代码；

3. 向量输出格式必须统一：一维 / 二维浮点型列表，维度与向量数据库配置一致；

4. 异常必须遵循系统统一异常码规范，抛出RAGException。

# 11. 常见问题（FAQ）

1. 向量维度与向量数据库不一致？答：检查系统配置中vector_db.vector_dimension与向量模型输出维度一致，本地模型与远程模型需使用相同维度。

2. 本地模型初始化失败？答：执行pip install sentence-transformers安装依赖，检查模型名称是否正确。

3. 远程向量模型调用失败？答：检查大模型对接模块配置是否完整（API 密钥、地址），确保网络连通性。

4. 批量向量化效率低？答：调整系统配置中batch_size参数，根据硬件性能优化批量大小。

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
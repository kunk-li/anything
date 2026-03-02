# 数据层-向量数据库模块（vector_db_module）设计说明书

# 1. 文档概述

## 1.1 文档目的

本文档为RAG与Agent系统数据层向量数据库模块（vector_db_module）的专项设计说明书，用于指导开发团队（含初学者）进行该模块的独立开发、测试与集成。文档明确了模块功能、接口定义、项目结构、配置规范、测试要求及与其他模块的依赖关系，确保开发人员可依据本文档完成模块开发，无需依赖其他模块细节，开发完成后可通过统一接口与系统其他模块无缝集成。

## 1.2 适用人群

本团队所有开发人员（含资深开发者与初学者）、测试人员，作为该模块开发、测试、部署及维护的唯一标准依据；项目管理人员可参考本文档了解模块职责与开发优先级。

## 1.3 模块定位与核心需求

本模块属于系统数据层核心模块，是RAG模块检索能力的基础支撑，负责存储文档向量表示并提供高效相似性检索服务。核心需求如下：

- 功能需求：支持向量的插入/更新、相似度检索、删除操作，返回统一格式结果，供RAG模块直接调用。

- 开发规范：遵循系统统一的项目结构、编码规范，包含抽象基类（ABC），确保模块可替换、可复用。

- 依赖需求：依赖基础支撑层的通用工具模块、配置管理模块、日志模块、异常处理模块，无其他模块依赖。

- 适配需求：提供本地FAISS示例实现，便于初学者快速落地，生产环境可替换为Pinecone/Chroma/Milvus等向量数据库。

## 1.4 术语定义

| 术语        | 定义                                       |
|-----------|------------------------------------------|
| 向量数据库     | 用于存储文档向量表示，支持高效相似性检索，为RAG模块提供检索能力。       |
| Embedding | 文本嵌入，将文本转换为高维向量，用于向量检索与语义匹配，本模块接收该向量并存储。 |
| ABC       | 抽象基类，定义模块的核心接口与方法，强制子类实现，保障模块一致性。        |
| upsert    | 向量的插入/更新操作，若向量ID已存在则更新，不存在则插入。           |
| query     | 向量相似度检索，根据查询向量，返回相似度最高的前N条向量结果。          |

# 2. 模块详细设计

## 2.1 模块功能

本模块核心功能是为系统提供向量存储与相似度检索服务，具体包括：

1. 支持向量的upsert（插入/更新）操作，接收外部传入的向量列表，完成存储或更新。

2. 支持向量的query（相似度检索）操作，接收查询向量、返回条数及过滤条件，返回符合要求的检索结果。

3. 支持向量的delete（删除）操作，可按向量ID或过滤条件删除指定向量。

4. 所有操作返回统一格式结果，确保与RAG模块、其他数据层模块的接口兼容性。

5. 集成日志记录、异常处理功能，确保模块运行可观测、可排查。

## 2.2 项目结构

本模块严格遵循系统统一的项目结构规范，目录结构如下（模块名称固定为vector_db_module）：

```plain text
vector_db_module/                  # 模块根目录（全小写，多单词用下划线连接）
├── __init__.py               # 模块初始化文件，暴露模块核心类/方法（不能为空）
├── core/                     # 核心逻辑目录（存放模块核心实现，含ABC抽象类）
│   ├── __init__.py
│   ├── base.py               # 抽象基类（ABC）文件，定义模块核心接口（必须包含）
│   └── impl.py               # 具体实现类文件，继承base.py中的抽象类（必须包含）
├── utils/                    # 模块工具目录（存放模块专属工具函数，无则空目录）
│   ├── __init__.py
│   └── tool_functions.py     # 工具函数文件（如向量格式校验、过滤条件处理等）
├── config/                   # 模块专属配置目录（存放模块专属配置）
│   ├── __init__.py
│   └── config.py             # 配置文件（读取基础配置，添加模块专属配置）
├── tests/                    # 测试目录（存放模块单元测试用例，必须包含）
│   ├── __init__.py
│   └── test_impl.py          # 具体实现类测试用例（必须包含，覆盖核心功能）
└── README.md                 # 模块说明文档（必须包含，适配初学者）
```

### 2.2.1 目录结构说明

- vector_db_module：模块根目录，名称固定，与模块功能对应，确保其他模块可正确导入。

- __init__.py：每个目录必须包含，根目录的__init__.py需暴露模块核心类（如从.core.impl import FaissVectorDB），方便其他模块调用。

- core目录：核心逻辑存放处，base.py定义抽象基类，impl.py实现抽象方法，支持多种向量数据库的替换。

- utils目录：存放模块专属工具函数，如向量格式校验、过滤条件解析等，不包含核心业务逻辑，仅提供辅助支持。

- config目录：存放模块专属配置，如向量维度、存储路径等，可读取基础支撑层的全局配置，补充模块个性化配置。

- tests目录：必须包含test_impl.py，覆盖模块核心功能的单元测试，初学者可参考示例编写简单测试用例。

- README.md：详细说明模块功能、核心接口、使用方法、依赖项、常见问题，语言简洁易懂，适配初学者。

## 2.3 核心接口设计（抽象基类）

抽象基类（core/base.py）定义模块必须实现的核心接口，强制子类遵循统一规范，确保模块可替换性。基础代码构建如下：

```python
from abc import ABC, abstractmethod
from typing import List, Dict, Optional


class BaseVectorDB(ABC):
    """向量数据库抽象基类，定义向量存储与检索核心接口，所有具体实现类必须继承此类并实现所有抽象方法"""

    @abstractmethod
    def upsert_vectors(self, vectors: List[Dict]) -> bool:
        """
        写入/更新向量
        :param vectors: 向量列表，每个元素格式（强制）：
            {"vector_id": str, "embedding": List[float], "metadata": dict}
            说明：vector_id为向量唯一标识，embedding为文本嵌入向量，metadata为附加信息（至少包含doc_id、chunk_id）
        :return: 操作成功返回True，失败返回False
        """
        pass

    @abstractmethod
    def query(self, query_vector: List[float], top_k: int = 5,
              filter: Optional[Dict] = None) -> List[Dict]:
        """
        向量相似度检索
        :param query_vector: 查询向量（与存储向量维度一致）
        :param top_k: 可选，返回相似度最高的向量条数，默认值为5
        :param filter: 可选，过滤条件（如{"doc_id": "...", "chunk_id": "..."}），用于精准检索
        :return: 检索结果列表，每个元素格式（强制）：
            {"vector_id": str, "score": float, "metadata": dict}
            说明：score为相似度得分（0~1），得分越高相似度越高
        """
        pass

    @abstractmethod
    def delete(self, vector_ids: Optional[List[str]] = None, filter: Optional[Dict] = None) -> bool:
        """
        删除向量：支持按vector_ids批量删除，或按filter条件删除
        :param vector_ids: 可选，向量ID列表，若传入则删除对应向量
        :param filter: 可选，过滤条件，若传入则删除符合条件的所有向量
        :return: 操作成功返回True，失败返回False
        """
        pass
```

## 2.4 具体实现类设计（基础代码构建）

具体实现类（core/impl.py）继承抽象基类，实现所有抽象方法，提供本地FAISS示例实现（便于初学者落地），生产环境可替换为其他向量数据库。基础代码构建如下，不包含具体方法实现细节：

```python
import os
from typing import List, Dict, Optional
from .base import BaseVectorDB
from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from exception_module.core.impl import VectorDBException

# 引入向量数据库依赖（FAISS示例）
try:
    import faiss
except ImportError:
    faiss = None


class FaissVectorDB(BaseVectorDB):
    """FAISS本地向量库实现（示例），用于开发/测试环境，生产环境可替换为Pinecone/Milvus等"""

    def __init__(self):
        """初始化方法：加载配置、初始化日志、初始化FAISS索引及相关存储"""
        self.config = ConfigManager()
        self.config.load_config()
        self.logger = SystemLogger()

        # 从配置中读取核心参数（向量维度、存储目录）
        self.dim = int(self.config.get_config("vector_db.vector_dimension", 768))
        self.store_dir = self.config.get_config("vector_db.local_dir", "vector_store")

        # 初始化存储目录（不存在则创建）
        if not os.path.exists(self.store_dir):
            os.makedirs(self.store_dir)

        # 校验FAISS依赖是否安装
        if faiss is None:
            raise VectorDBException("FAISS_NOT_INSTALLED", "未安装faiss，请在requirements中添加faiss-cpu")

        # 初始化FAISS索引、ID映射、元数据映射
        self.index_path = os.path.join(self.store_dir, "faiss.index")
        self.meta_path = os.path.join(self.store_dir, "meta.json")
        self.index = faiss.IndexFlatIP(self.dim)  # 余弦相似度计算（需向量归一化）
        self.id_map: List[str] = []  # 向量ID与FAISS索引下标的映射
        self.meta_map: Dict[str, Dict] = {}  # 向量ID与元数据的映射（修正：原List改为Dict，符合映射逻辑）

        # 加载已存在的索引和元数据（若有）
        self._load_if_exists()

    def _load_if_exists(self):
        """私有方法：加载已存在的FAISS索引和元数据，具体实现略"""
        pass

    def _persist(self):
        """私有方法：将当前索引和元数据持久化到本地，具体实现略"""
        pass

    def upsert_vectors(self, vectors: List[Dict]) -> bool:
        """实现抽象方法：写入/更新向量，具体实现略"""
        pass

    def query(self, query_vector: List[float], top_k: int = 5,
              filter: Optional[Dict] = None) -> List[Dict]:
        """实现抽象方法：向量相似度检索，具体实现略"""
        pass

    def delete(self, vector_ids: Optional[List[str]] = None, filter: Optional[Dict] = None) -> bool:
        """实现抽象方法：删除向量，FAISS Flat索引不支持直接删除，生产实现需替换索引结构或向量库"""
        raise VectorDBException("VECTOR_DELETE_NOT_SUPPORTED",
                                "示例FaissVectorDB不支持删除，请在生产实现中使用可删除索引或外部向量库")
```

## 2.5 配置规范

本模块配置需遵循系统统一的配置管理规范，配置文件（config/config.py）负责读取全局配置，补充模块专属配置；配置参数可通过环境变量覆盖，确保部署灵活性。

### 2.5.1 配置示例（config/config.yaml）

```yaml
vector_db:
  type: "faiss"  # 向量数据库类型，可替换为pinecone/milvus等
  vector_dimension: 768  # 向量维度，需与Embedding模块输出维度一致
  local_dir: "vector_store"  # 本地存储目录（FAISS示例用）
  # 若使用外部向量库（如Pinecone），需添加对应配置
  # pinecone:
  #   api_key: "${PINECONE_API_KEY}"
  #   environment: "us-west1-gcp"
  #   index_name: "rag-agent-index"
```

### 2.5.2 配置读取逻辑（config/config.py）

```python
from config_module.core.impl import ConfigManager


class VectorDBConfig:
    """向量数据库模块专属配置类，读取全局配置并补充模块配置"""

    def __init__(self):
        self.config_manager = ConfigManager()
        self.config_manager.load_config()

    def get_vector_dimension(self) -> int:
        """获取向量维度，默认768"""
        return int(self.config_manager.get_config("vector_db.vector_dimension", 768))

    def get_local_store_dir(self) -> str:
        """获取本地存储目录，默认vector_store"""
        return self.config_manager.get_config("vector_db.local_dir", "vector_store")

    def get_vector_db_type(self) -> str:
        """获取向量数据库类型，默认faiss"""
        return self.config_manager.get_config("vector_db.type", "faiss")
```

## 2.6 测试用例设计（基础框架）

测试用例需覆盖模块核心功能（upsert、query、异常场景），遵循系统统一的测试规范，基础测试框架（tests/test_impl.py）如下：

```python
import unittest
from vector_db_module.core.impl import FaissVectorDB
from exception_module.core.impl import VectorDBException


class TestFaissVectorDB(unittest.TestCase):
    """FAISS向量数据库实现类的单元测试，覆盖核心功能与异常场景"""

    def setUp(self):
        """测试前置：初始化FAISS向量库实例"""
        self.db = FaissVectorDB()

    def test_upsert_and_query(self):
        """测试向量插入与检索功能"""
        # 构造测试向量
        test_vectors = [
            {"vector_id": "v1", "embedding": [0.1] * 768, "metadata": {"doc_id": "d1", "chunk_id": "d1#c000001"}},
            {"vector_id": "v2", "embedding": [0.2] * 768, "metadata": {"doc_id": "d2", "chunk_id": "d2#c000001"}},
        ]
        # 测试upsert操作
        self.assertTrue(self.db.upsert_vectors(test_vectors))
        # 测试query操作
        query_vector = [0.1] * 768
        result = self.db.query(query_vector, top_k=2)
        # 断言结果有效性
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["vector_id"], "v1")
        self.assertIn("doc_id", result[0]["metadata"])

    def test_delete_not_supported(self):
        """测试删除操作（FAISS示例不支持，预期抛出异常）"""
        with self.assertRaises(VectorDBException) as context:
            self.db.delete(vector_ids=["v1"])
        self.assertEqual(context.exception.code, "VECTOR_DELETE_NOT_SUPPORTED")  # 修正：异常code大小写统一

    def test_embedding_dimension_mismatch(self):
        """测试向量维度不匹配场景（预期抛出异常），具体实现略"""
        pass


if __name__ == "__main__":
    unittest.main()
```

# 3. 模块依赖与集成

## 3.1 模块依赖

本模块仅依赖基础支撑层的4个模块，无其他模块依赖，依赖关系如下：

- 配置管理模块（config_module）：用于读取全局配置与模块专属配置。

- 日志模块（log_module）：用于记录模块运行日志（操作日志、错误日志）。

- 异常处理模块（exception_module）：用于定义和抛出模块专属异常，统一异常处理。

- 通用工具模块（common_utils_module）：用于向量格式校验、路径处理等辅助操作（可选）。

## 3.2 集成规范

本模块通过抽象基类提供统一接口，集成时需遵循以下规范：

1. 其他模块（主要是RAG模块）调用本模块时，仅依赖BaseVectorDB抽象基类，禁止直接引用具体实现类（如FaissVectorDB），确保模块可替换。

2. 模块初始化时，通过配置文件指定向量数据库类型，实现不同向量数据库的无缝切换，无需修改上层代码。

3. 向量数据写入时，需确保metadata中包含doc_id、chunk_id字段（遵循系统Chunking规范），便于后续检索结果追溯。

4. 模块返回结果需严格遵循抽象基类定义的格式，确保与RAG模块接口兼容。

# 4. 开发与交付规范

## 4.1 编码规范

遵循[系统架构设计](./RAG与Agent系统架构设计说明书.md)中的 3.2 统一编码规范

## 4.2 交付物清单（必须）

模块开发完成后，需提交以下交付物，确保符合系统统一交付标准：

- core/base.py：抽象基类文件，定义核心接口。

- core/impl.py：具体实现类文件，提供FAISS示例实现。

- utils/tool_functions.py：模块专属工具函数（若有）。

- config/config.py：模块配置读取逻辑文件。

- tests/test_impl.py：单元测试用例文件，覆盖核心功能。

- requirements.txt：模块依赖清单，明确依赖包及版本。

- README.md：模块说明文档，适配初学者，说明模块功能、接口、使用方法。

## 4.3 可替换性约束（强制）

- 具体实现类必须继承BaseVectorDB抽象基类，实现所有抽象方法，不得修改接口定义。

- 替换向量数据库（如从FAISS改为Milvus）时，仅需修改impl.py中的具体实现，上层调用代码无需修改。

- 禁止在具体实现类中添加抽象基类未定义的公共方法，确保接口统一性。

# 5. 常见问题与注意事项

- 向量维度一致性：存储的向量维度需与Embedding模块输出的向量维度一致，否则会导致检索失败，配置文件中需统一向量维度参数。

- FAISS示例限制：本地FAISS Flat索引不支持直接删除操作，生产环境需替换为可删除索引结构（如IndexIDMap）或外部向量库。

- 元数据规范：向量写入时，metadata必须包含doc_id、chunk_id字段，否则会导致RAG模块无法追溯检索结果对应的原文片段。

- 依赖安装：使用FAISS示例时，需在requirements.txt中添加faiss-cpu依赖，避免导入失败。

- 性能考虑：本地FAISS适用于开发/测试环境，海量向量场景需使用分布式向量库（如Milvus、Pinecone），确保检索性能。

返回[系统架构设计](./RAG与Agent系统架构设计说明书.md)
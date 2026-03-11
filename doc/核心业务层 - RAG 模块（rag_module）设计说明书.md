# 核心业务层 - RAG 模块（rag_module）设计说明书

# 1. 文档概述

## 1.1 文档目的

本文档为RAG 与 Agent 系统核心业务层 - RAG 模块的独立、完整设计说明书，严格遵循系统整体架构规范，明确模块功能、项目结构、接口定义、依赖关系、数据格式及开发要求，用于指导开发人员（含初学者）进行该模块的独立开发、测试与集成，确保模块与系统无缝兼容、可扩展、可替换。

## 1.2 适用人群

本团队所有开发人员（含资深开发者与初学者）、测试人员，作为 RAG 模块开发、测试、维护的唯一标准依据；项目管理人员可参考本说明书进行模块开发进度管控。

## 1.3 核心需求回顾

- 模块功能：实现检索增强生成全流程，接收用户问题，完成文本检索、上下文拼接、大模型回答生成，输出标准化答案与检索依据。

- 开发语言：Python 3.10+，与系统整体保持一致。

- 开发模式：独立开发、互不依赖，基于本说明书即可完成开发，开发完成后通过统一接口集成至核心业务层。

- 文档要求：详细、易懂，适配初学者，明确模块所有可提前定义的内容（接口、数据格式、项目结构等）。

- 模块约束：需包含抽象基类（ABC），确保模块一致性；代码可与系统其他模块交换，数据格式符合系统统一标准。

## 1.4 术语定义

|术语|定义|
|---|---|
|RAG|检索增强生成（Retrieval-Augmented Generation），核心业务层核心模块，结合向量检索与大模型生成，提升回答准确性与事实性。|
|检索|根据用户问题向量，从向量数据库中匹配最相似的文本片段，是 RAG 的核心检索环节。|
|上下文拼接|将检索到的文本片段按规则拼接为大模型输入提示词（Prompt），控制上下文长度。|
|ABC|抽象基类，定义模块的核心接口与方法，强制子类实现，保障模块一致性。|
|标准化响应|模块输出统一格式结果，包含答案、检索依据、状态码、错误信息，遵循系统统一异常码规范。|
# 2. 模块核心设计

## 2.1 模块定位与职责

本模块属于系统核心业务层，是 RAG 系统的核心中枢，串联数据层与基础支撑层所有相关模块，完整实现 RAG 全流程：

1. 接收用户问题 / 查询，完成参数校验、异常处理；

2. 调用 Embedding 模块将问题转换为向量；

3. 调用向量数据库模块执行相似度检索，获取相关文本片段；

4. 上下文拼接与 Prompt 模板渲染，生成标准化大模型输入；

5. 调用大模型对接模块生成增强回答；

6. 输出标准化 RAG 响应，包含答案、检索依据、状态信息；

7. 屏蔽底层模块差异，支持配置化切换向量模型、大模型、向量库。

## 2.2 输入输出规范

### 2.2.1 输入

- 核心输入：query: str（用户问题 / 查询文本）；

- 可选参数：top_k: int（返回最相似的片段数量，默认 5）、model_name: str（指定生成大模型，默认系统配置）。

### 2.2.2 输出

标准化 RAG 响应格式（遵循系统统一异常码规范）：

```json
{
  "code": "SUCCESS",
  "message": "RAG执行成功",
  "data": {
    "query": "用户问题",
    "top_k": 5,
    "contexts_count": 3,
    "answer": "生成的增强回答",
    "retrieved": [
      {
        "vector_id": "v1",
        "score": 0.92,
        "doc_id": "d1",
        "metadata": {"chunk_id": "d1#c000001", "file_name": "test.pdf"}
      }
    ]
  }
}
```

## 2.3 依赖关系

本模块是核心业务层最核心的模块，依赖基础支撑层、数据层、核心业务层其他模块，是系统数据流转的中枢：

**基础支撑层依赖：**

1. 通用工具模块（common_utils_module）：文本清洗、参数校验；

2. 配置管理模块（config_module）：读取 RAG 参数、Prompt 模板、上下文长度限制；

3. 日志模块（log_module）：记录 RAG 全流程日志、异常信息；

4. 异常处理模块（exception_module）：抛出标准化 RAG 异常。

**数据层依赖：**

1. 向量数据库模块（vector_db_module）：执行向量相似度检索；

2. 文档存储模块（document_store_module）：根据检索结果获取原文文本；

3. 大模型对接模块（llm_adapter_module）：调用聊天大模型生成增强回答。

**核心业务层依赖：**

1. Embedding 模块（embedding_module）：将用户问题转换为向量。

# 3. 统一项目结构规范

严格遵循系统整体项目结构规范，模块根目录命名为rag_module（全小写，多单词用下划线连接），目录结构如下，开发者不得随意修改目录名称与层级，初学者可直接复制该结构搭建项目。

```plaintext
rag_module/                  # 模块根目录
├── __init__.py              # 模块初始化文件，暴露核心类/方法
├── core/                    # 核心逻辑目录（抽象基类+实现类）
│   ├── __init__.py
│   ├── base.py              # 抽象基类（ABC），定义RAG核心接口
│   └── impl.py              # 具体实现类，继承抽象基类
├── model/                   # 数据模型目录（统一请求/响应模型）
│   ├── __init__.py
│   └── data_model.py        # RAG请求/响应标准化模型
├── prompt/                  # Prompt模板目录（RAG专属）
│   ├── __init__.py
│   └── prompt_template.py    # RAG增强生成Prompt模板
├── utils/                   # 模块专属工具函数
│   ├── __init__.py
│   └── tool_functions.py    # 上下文拼接、Prompt渲染、检索结果处理
├── config/                  # 模块专属配置
│   ├── __init__.py
│   └── config.py            # 读取全局配置，补充RAG专属配置
├── tests/                   # 测试用例目录
│   ├── __init__.py
│   └── test_impl.py         # 核心功能测试用例
└── README.md                # 模块说明文档（适配初学者）
```

## 3.1 目录结构说明

- rag_module：模块根目录，名称固定，与功能精准对应；

- __init__.py：每个目录必须包含，根目录暴露核心类（如SimpleRAG、RAGService），方便其他模块调用；

- core：核心逻辑目录，base.py定义抽象接口，impl.py实现 RAG 全流程；

- model：模块专属数据模型，定义 RAG 请求、响应的标准化格式；

- prompt：RAG 专属 Prompt 模板管理，支持配置化切换模板；

- utils：模块专属工具函数，上下文拼接、Prompt 渲染、检索结果过滤；

- config：读取系统 RAG 配置，补充模块专属参数（如上下文长度、截断规则）；

- tests：覆盖检索、生成、全流程、异常场景的测试用例；

- README.md：详细说明模块功能、接口、使用方法、依赖项、扩展步骤。

# 4. 核心数据模型设计

本模块定义统一的 RAG 请求 / 响应模型，所有接口均基于该模型交互，确保模块内部及与外部模块的数据格式统一，遵循系统整体数据规范。

## 4.1 RAG 请求模型（RAGRequest）

```python
from typing import Optional
from dataclasses import dataclass

@dataclass
class RAGRequest:
    """RAG增强生成统一请求模型"""
    # 用户问题/查询（必填）
    query: str
    # 返回最相似的片段数量（默认5）
    top_k: int = 5
    # 生成大模型名称（默认使用系统配置）
    llm_model_name: Optional[str] = None
    # 向量模型名称（默认使用系统配置）
    embedding_model_name: Optional[str] = None
    # 上下文最大长度（默认使用系统配置）
    max_context_length: Optional[int] = None
```

## 4.2 检索结果模型（RetrievedChunk）

```python
from typing import Dict, Optional, float
from dataclasses import dataclass

@dataclass
class RetrievedChunk:
    """RAG检索结果标准化模型"""
    vector_id: str
    score: float          # 相似度得分（0~1）
    doc_id: str           # 文档ID（关联文档存储）
    chunk_id: str         # 文本片段ID
    file_name: str        # 源文件名
    content: Optional[str] = None  # 片段文本内容
```

## 4.3 RAG 响应模型（RAGResponse）

```python
from typing import List, Optional, Dict
from dataclasses import dataclass

@dataclass
class RAGResponse:
    """RAG增强生成统一响应模型，遵循系统统一异常码规范"""
    # 响应码：SUCCESS（成功）、系统异常码（失败）
    code: str
    # 响应信息：成功为"ok"，失败为具体错误信息
    message: str
    # 响应数据
    data: Optional[Dict] = None
    # 调用耗时（秒，可选）
    cost_time: Optional[float] = None
    # 链路追踪ID（可选）
    trace_id: Optional[str] = None
```

# 5. 核心接口设计（抽象基类）

## 5.1 RAG 抽象基类（BaseRAG）

定义模块核心接口，强制所有实现类必须实现，保障模块一致性、可替换性：

```python
from abc import ABC, abstractmethod
from typing import Dict
from rag_module.model.data_model import RAGRequest, RAGResponse

class BaseRAG(ABC):
    """RAG模块抽象基类，所有RAG实现类必须继承此类"""

    @abstractmethod
    def retrieve(self, query: str, top_k: int = 5) -> List[Dict]:
        """
        检索步骤：根据用户问题检索相关文本片段
        :param query: 用户问题
        :param top_k: 返回片段数量
        :return: 标准化检索结果列表
        :raises RAGException: 检索失败时抛出标准化异常
        """
        pass

    @abstractmethod
    def generate(self, query: str, contexts: List[str]) -> str:
        """
        生成步骤：根据检索上下文生成增强回答
        :param query: 用户问题
        :param contexts: 检索到的文本片段列表
        :return: 生成的回答文本
        :raises RAGException: 生成失败时抛出标准化异常
        """
        pass

    @abstractmethod
    def run(self, query: str, top_k: int = 5) -> Dict:
        """
        RAG全流程执行入口（对外核心接口）
        :param query: 用户问题
        :param top_k: 返回片段数量
        :return: 标准化RAG响应结果
        :raises RAGException: 流程执行失败时抛出标准化异常
        """
        pass

    @abstractmethod
    def call_rag(self, request: RAGRequest) -> RAGResponse:
        """
        统一RAG调用接口（对外标准化入口）
        :param request: RAG请求模型
        :return: RAG响应模型
        """
        pass
```

# 6. 核心实现设计

## 6.1 标准 RAG 实现类（SimpleRAG）

继承抽象基类，实现完整 RAG 全流程，串联所有依赖模块，是系统默认使用的 RAG 实现类，基础代码构建如下：

```python
from typing import List, Dict
from rag_module.core.base import BaseRAG
from rag_module.model.data_model import RAGRequest, RAGResponse
from embedding_module.core.impl import STEmbedding
from vector_db_module.core.impl import FaissVectorDB
from document_store_module.core.impl import LocalDocumentStore
from llm_adapter_module.core.impl import LLMService
from common_utils_module.core.impl import CommonUtils
from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from exception_module.core.impl import RAGException

class SimpleRAG(BaseRAG):
    """标准RAG实现类：串联检索+生成全流程，系统默认实现"""

    def __init__(self, llm_client):
        """
        初始化RAG模块，注入大模型客户端，加载系统配置
        :param llm_client: 大模型客户端（由外部注入，解耦依赖）
        """
        # 基础支撑层初始化
        self.utils = CommonUtils()
        self.logger = SystemLogger()
        self.config = ConfigManager()
        self.config.load_config()

        # 核心依赖模块初始化
        self.embedding = STEmbedding()                # 向量生成模块
        self.vector_db = FaissVectorDB()              # 向量数据库模块
        self.doc_store = LocalDocumentStore()          # 文档存储模块
        self.llm = llm_client                         # 大模型客户端（外部注入）

        # 读取系统RAG核心配置
        self.default_top_k = self.config.get_config("rag.default_top_k", 5)
        self.max_context_length = self.config.get_config("rag.max_context_length", 4096)
        self.context_truncate_length = self.config.get_config("rag.context_truncate_length", 1200)

        self.logger.info("RAG模块初始化完成，加载系统默认配置")

    def retrieve(self, query: str, top_k: int = 5) -> List[Dict]:
        """实现抽象方法：检索步骤，问题向量化+向量检索+结果格式化"""
        pass

    def generate(self, query: str, contexts: List[str]) -> str:
        """实现抽象方法：生成步骤，上下文拼接+Prompt渲染+大模型生成"""
        pass

    def run(self, query: str, top_k: int = 5) -> Dict:
        """实现抽象方法：RAG全流程执行，检索→获取原文→生成→结果封装"""
        pass

    def call_rag(self, request: RAGRequest) -> RAGResponse:
        """实现抽象方法：标准化RAG调用入口，请求校验+异常封装"""
        pass
```

# 7. 模块调用示例

```python
from rag_module.core.impl import SimpleRAG
from llm_adapter_module.core.impl import LLMService
from rag_module.model.data_model import RAGRequest

# 1. 初始化大模型客户端
llm_service = LLMService()

# 2. 初始化RAG模块（注入大模型客户端）
rag = SimpleRAG(llm_client=llm_service)

# 3. 直接调用全流程接口
query = "RAG系统核心业务层设计包含哪些模块？"
result = rag.run(query, top_k=5)

# 4. 标准化接口调用（推荐）
request = RAGRequest(
    query="RAG系统核心业务层设计包含哪些模块？",
    top_k=5
)
response = rag.call_rag(request)

# 5. 处理响应
if response.code == "SUCCESS":
    print(f"用户问题：{response.data['query']}")
    print(f"RAG回答：{response.data['answer']}")
    print(f"检索到{response.data['contexts_count']}个相关片段")
```

# 8. 测试规范

## 8.1 测试范围

- 检索步骤：问题向量化、向量检索、结果格式化；

- 生成步骤：上下文拼接、Prompt 渲染、大模型生成；

- 全流程执行：检索 + 生成端到端测试；

- 异常场景：问题为空、检索无结果、大模型调用失败、配置缺失；

- 配置切换：向量模型、大模型、检索参数切换测试。

## 8.2 测试用例基础框架

```python
import unittest
from rag_module.core.impl import SimpleRAG
from llm_adapter_module.core.impl import LLMService
from exception_module.core.impl import RAGException

class TestRAGModule(unittest.TestCase):
    """RAG模块单元测试，覆盖核心功能与异常场景"""

    def setUp(self):
        """测试前置：初始化RAG实例、大模型服务、测试数据"""
        self.llm_service = LLMService()
        self.rag = SimpleRAG(llm_client=self.llm_service)
        self.test_query = "RAG系统核心业务层设计包含哪些模块？"
        self.empty_query = ""

    def test_rag_retrieve(self):
        """测试RAG检索步骤，验证检索结果格式与数量"""
        retrieved = self.rag.retrieve(self.test_query, top_k=3)
        self.assertEqual(len(retrieved), 3)
        self.assertIn("doc_id", retrieved[0])

    def test_rag_full_run(self):
        """测试RAG全流程执行，验证响应格式与答案生成"""
        result = self.rag.run(self.test_query)
        self.assertEqual(result["code"], "SUCCESS")
        self.assertIn("answer", result["data"])

    def test_empty_query_run(self):
        """测试空问题RAG调用，验证异常抛出"""
        with self.assertRaises(RAGException):
            self.rag.run(self.empty_query)

    def test_no_retrieve_result(self):
        """测试无检索结果场景，验证生成逻辑与响应"""
        pass

if __name__ == "__main__":
    unittest.main()
```

# 9. 交付物清单（强制）

1. core/base.py：抽象基类，定义 RAG 核心接口；

2. core/impl.py：具体实现类（标准 RAG 全流程）；

3. model/data_model.py：RAG 请求 / 响应标准化数据模型；

4. prompt/prompt_template.py：RAG 增强生成 Prompt 模板；

5. utils/tool_functions.py：上下文拼接、Prompt 渲染、检索结果处理工具；

6. config/config.py：模块配置读取逻辑；

7. tests/test_impl.py：核心功能测试用例；

8. README.md：模块说明文档（适配初学者）；

9. requirements.txt：依赖包清单（无额外专属依赖，复用系统依赖）。

# 10. 可替换性约束（强制）

1. 上层模块（协同调度、Agent）仅依赖BaseRAG抽象接口，禁止直接引用具体实现类；

2. 新增 RAG 实现（如多轮对话 RAG、知识库分类 RAG）仅需实现BaseRAG抽象接口，无需修改上层代码；

3. 检索结果、生成结果、全流程响应格式必须严格遵循系统统一标准；

4. 异常必须遵循系统统一异常码规范，抛出RAGException；

5. 模块依赖仅通过抽象接口注入，支持无缝切换 Embedding、向量库、大模型实现。

# 11. 常见问题（FAQ）

1. 检索结果为空怎么办？答：检查向量数据库是否已构建索引，确认文档已完成向量化与入库；调整top_k参数，放宽检索范围。

2. 生成回答与检索内容无关？答：优化 Prompt 模板，强化 “仅根据上下文回答” 的约束；降低大模型temperature参数，提升回答事实性；检查上下文拼接是否正确。

3. RAG 执行超时？答：检查大模型调用、向量检索的超时配置；缩短上下文长度，减少检索片段数量；优化文档分段长度。

4. 检索结果与问题不匹配？答：切换向量模型（本地 / 远程），确保向量维度一致；优化文档分段，提升文本语义完整性；检查问题向量化是否正常。

5. 上下文长度超出大模型限制？答：启用上下文截断功能，调整context_truncate_length参数；减少top_k检索数量，控制总上下文长度。

返回[系统架构设计](RAG%E4%B8%8EAgent%E7%B3%BB%E7%BB%9F%E6%9E%B6%E6%9E%84%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)
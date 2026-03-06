# 数据层-大模型对接模块（llm_adapter_module）设计说明书

# 1. 文档概述

## 1.1 文档目的

本文档为RAG与Agent系统数据层-大模型对接模块（llm_adapter_module）的独立设计说明书，遵循系统整体架构规范，用于指导开发团队（含初学者）进行该模块的模块化、独立化开发。文档明确模块功能、接口定义、项目结构、数据格式、调用规范，确保模块可独立开发、无缝集成至系统数据层，为上层RAG、Agent模块提供标准化的向量模型、聊天大模型、多模态大模型对接能力，同时支持上传文件内容（文本/图片/音频/视频/混合文件）的解析与大模型输入适配。

## 1.2 适用人群

本团队所有开发人员（含资深开发者与初学者）、测试人员、项目管理人员，作为该模块开发、测试、部署及维护的唯一标准依据。

## 1.3 核心需求回顾

- 模块功能：实现向量模型（Embedding）、聊天大模型（LLM）、多模态大模型（MLLM）的标准化对接，支持多格式文件内容解析与大模型输入适配，提供统一的大模型调用入口，屏蔽不同厂商、不同类型模型的接口差异。

- 开发语言：后端统一采用Python（版本3.10+），与系统整体开发语言保持一致。

- 开发模式：多开发人员异地协同，模块独立开发、互不依赖其他业务模块，基于本说明书即可完成开发，开发后通过统一接口集成至数据层。

- 模块依赖：仅依赖系统基础支撑层的通用工具、配置管理、日志、异常处理模块，为数据层其他模块（文档解析、向量数据库）及上层核心业务层（RAG、Agent）提供服务。

- 扩展要求：支持多厂商模型无缝扩展（如OpenAI、智谱AI、文心一言、通义千问、Midjourney等），新增模型无需修改上层调用逻辑；支持多格式文件解析扩展（TXT/MD/PDF/Word/PNG/JPG/MP3/MP4等）。

## 1.4 术语定义

|术语|定义|
|---|---|
|大模型对接模块|数据层核心模块，封装向量模型、聊天模型、多模态模型的对接逻辑，提供统一调用接口，适配文件内容输入。|
|向量模型（Embedding）|将文本转换为高维向量的模型，为RAG模块的向量检索提供核心支撑。|
|聊天模型（LLM）|具备自然语言理解与生成能力的大模型，支撑RAG的增强生成环节与Agent的任务推理、交互对话。|
|多模态模型（MLLM）|支持文本、图片、音频、视频等多种媒体类型输入，具备跨模态理解与生成能力的大模型，支撑Agent的多模态感知、图文问答、音视频内容分析等场景。|
|模型适配器|实现不同厂商、不同类型大模型的接口适配，屏蔽厂商间的请求/响应格式差异，遵循统一的适配器接口规范。|
|文件内容适配|将解析后的文件文本/媒体内容进行预处理（分段、清洗、格式转换、特征提取），适配大模型的输入要求（如上下文长度、批量输入、媒体格式限制）。|
|统一调用入口|模块对外提供的标准化API，上层模块通过该入口调用不同类型大模型，无需关注底层厂商与类型实现。|
# 2. 模块定位与架构设计

## 2.1 模块整体定位

本模块属于系统数据层核心模块，是连接上层业务（RAG、Agent）与底层大模型服务的桥梁，核心定位如下：

1. 能力封装：封装向量模型、聊天模型、多模态模型的调用逻辑，向上层暴露简洁、统一的调用接口；

2. 差异屏蔽：通过适配器模式消除不同厂商、不同类型大模型的接口、参数、响应格式差异；

3. 输入适配：对接系统文档解析模块，完成上传文件（文本/图片/音频/视频/混合文件）的解析、预处理，生成大模型可直接使用的标准化输入；

4. 能力支撑：为RAG模块提供文本向量化、检索结果增强生成能力；为Agent模块提供任务解析、推理、多轮对话、多模态感知能力。

## 2.2 模块层级与依赖关系

### 2.2.1 系统层级关系

基础支撑层 → 数据层-大模型对接模块 → 数据层其他模块（文档解析、向量数据库） → 核心业务层（RAG、Agent、协同调度）

### 2.2.2 模块依赖关系

- 依赖模块：基础支撑层的通用工具模块、配置管理模块、日志模块、异常处理模块（仅依赖基础支撑层，确保模块独立性）；

- 被依赖模块：数据层文档解析模块、核心业务层RAG模块、核心业务层Agent模块、协同调度模块。

## 2.3 模块内部架构设计

本模块采用适配器模式+分层设计，内部划分为4个子模块，各子模块独立开发、通过统一接口通信，确保高内聚、低耦合，同时提升扩展性。模块内部架构如下：

```plain text
llm_adapter_module/
├── 适配器子模块：定义通用适配器接口，实现各厂商向量/聊天/多模态模型的具体适配器
├── 服务子模块：提供文件内容适配、大模型统一调用、参数校验等核心服务
├── 模型子模块：定义统一的请求/响应数据模型、文件内容封装模型（支持多模态）
└── 工具子模块：提供大模型参数处理、文本分段清洗、媒体预处理、格式转换等专属工具函数
```

## 2.4 核心设计原则

1. 接口统一：所有类型模型适配器遵循统一的抽象接口，上层调用无感知厂商与类型差异；

2. 可扩展性：新增厂商模型仅需实现抽象适配器接口，新增文件/媒体类型仅需扩展文件适配工具；

3. 容错性：内置异常处理、降级策略，大模型调用失败时返回标准化错误信息，支持重试机制；

4. 易用性：对外提供简洁的统一调用入口，上层模块无需关注底层实现细节；

5. 兼容性：适配系统统一的编码规范、项目结构规范、异常码规范，确保与系统无缝集成；

6. 多模态兼容：统一数据模型与接口设计，天然支持文本、图片、音频、视频等多种输入类型，无需单独扩展接口。

# 3. 统一项目结构规范

本模块严格遵循RAG与Agent系统的统一项目结构规范，开发者需严格遵守，不得随意修改目录名称与层级，初学者可直接复制该结构搭建项目。

```plain text
# 大模型对接模块统一目录结构
llm_adapter_module/                  # 模块根目录（全小写，多单词用下划线连接）
├── __init__.py                       # 模块初始化文件，暴露核心类/方法（必须包含，不能为空）
├── core/                             # 核心逻辑目录（存放抽象基类、核心实现类）
│   ├── __init__.py
│   ├── base.py                       # 抽象基类（ABC）文件，定义适配器、服务核心接口（必须包含）
│   └── impl.py                       # 具体实现类文件，实现抽象接口（必须包含）
├── model/                            # 数据模型目录（存放请求/响应/文件内容模型，专属）
│   ├── __init__.py
│   └── data_model.py                 # 统一数据模型定义文件（支持多模态）
├── utils/                            # 模块工具目录（存放专属工具函数）
│   ├── __init__.py
│   └── tool_functions.py             # 工具函数文件（参数处理、文本适配、媒体预处理、格式转换等）
├── config/                           # 模块配置目录（存放专属配置）
│   ├── __init__.py
│   └── config.py                     # 配置文件（读取全局配置，补充模块专属配置）
├── tests/                            # 测试目录（存放单元测试、集成测试用例，必须包含）
│   ├── __init__.py
│   ├── test_base.py                  # 抽象类测试用例（可选，初学者可简化）
│   └── test_impl.py                  # 具体实现类测试用例（必须包含，覆盖核心功能）
└── README.md                         # 模块说明文档（必须包含，适配初学者）
```

## 3.1 目录结构说明

1. llm_adapter_module：模块根目录，名称与功能严格对应，遵循“全小写、多单词下划线连接”规则；

2. __init__.py：每个目录必须包含，根目录的__init__.py需暴露模块核心类/方法（如大模型统一服务、核心适配器），方便其他模块调用；

3. core：核心逻辑目录，base.py定义适配器、服务的抽象基类（ABC），强制子类实现核心接口；impl.py实现抽象接口，包含各厂商向量/聊天/多模态适配器、统一调用服务的具体逻辑；

4. model：模块专属数据模型目录，区别于系统通用工具模块，存放大模型请求、响应、文件内容（含多模态）的标准化数据模型，确保数据格式统一；

5. utils：模块专属工具目录，存放大模型参数校验、文本分段清洗、文件内容适配、媒体预处理（图片压缩、音频转码等）、格式转换等工具函数，不包含核心业务逻辑；

6. config：模块专属配置目录，读取基础支撑层的全局配置（如大模型API密钥、地址、参数、支持的媒体类型），补充模块专属配置（如重试次数、超时时间、媒体大小限制）；

7. tests：测试用例目录，必须包含test_impl.py，覆盖适配器、统一服务、工具函数的核心功能，确保模块功能正常；

8. README.md：模块说明文档，详细说明模块功能、核心接口、使用方法、依赖项、扩展步骤、常见问题，语言简洁易懂，适配初学者。

# 4. 核心数据模型设计

本模块定义统一的请求模型、响应模型、文件内容模型，所有适配器、服务均基于该模型进行数据交互，确保模块内部及与外部模块的数据格式统一。模型定义在model/data_model.py中，遵循Python类型注解规范，天然支持多模态输入输出。

## 4.1 文件内容模型（FileContent）

封装解析后的上传文件内容（含多模态），为大模型提供标准化的输入，对接系统文档解析模块。

```python
from typing import List, Optional, Dict
from dataclasses import dataclass

@dataclass
class MediaContent:
    """媒体内容子模型（适配多模态）"""
    media_type: str          # 媒体类型：image/audio/video
    media_path: str          # 媒体文件路径（本地/云存储）
    media_base64: Optional[str] = None  # 媒体文件Base64编码（小文件适用）
    media_metadata: Optional[Dict] = None  # 媒体元信息（尺寸、时长、格式等）

@dataclass
class FileContent:
    """文件内容标准化模型（支持多模态），对接文档解析模块"""
    file_name: str          # 文件名（含后缀）
    file_type: str          # 文件类型（如txt、md、pdf、docx、png、mp3、mp4等）
    text_content: Optional[str] = None  # 文件解析后的完整文本内容（文本文件/多模态文件的OCR/字幕）
    split_contents: Optional[List[str]] = None  # 文本分段内容（适配大模型批量输入）
    media_contents: Optional[List[MediaContent]] = None  # 媒体内容列表（多模态文件专用）
    file_size: Optional[int] = None  # 文件大小（字节，可选）
    parse_time: Optional[str] = None # 解析时间（可选，格式：YYYY-MM-DD HH:MM:SS）
```

## 4.2 大模型请求模型（LLMRequest）

统一的大模型请求参数模型，支持向量模型、聊天模型、多模态模型的所有通用参数，屏蔽厂商与类型参数差异。

```python
from typing import List, Optional, Dict
from dataclasses import dataclass

@dataclass
class LLMParam:
    """大模型通用参数子模型，所有参数均提供默认值"""
    temperature: float = 0.7  # 生成温度（0~1，聊天/多模态模型生效）
    top_k: int = 40           # 采样TopK（聊天/多模态模型生效）
    max_tokens: int = 2000    # 最大生成令牌数（聊天/多模态模型生效）
    batch_size: int = 32      # 批量处理大小（向量模型生效）
    normalize: bool = True    # 向量是否归一化（向量模型生效）
    media_process_mode: str = "auto"  # 媒体处理模式（多模态模型生效：auto/extract/raw）
    extra_params: Optional[Dict] = None  # 厂商专属扩展参数（可选）

@dataclass
class LLMRequest:
    """大模型统一请求模型（支持多模态）"""
    request_type: str        # 请求类型：VECTOR（向量模型）、CHAT（聊天模型）、MULTIMODAL（多模态模型）
    input_text: Optional[str] = None  # 单条输入文本（适用于单条向量化、单轮对话、多模态文本指令）
    batch_input: Optional[List[str]] = None  # 批量输入文本（适用于向量模型批量向量化）
    file_content: Optional[FileContent] = None  # 文件内容模型（适用于基于文件的大模型调用）
    media_input: Optional[List[MediaContent]] = None  # 直接媒体输入（适用于多模态模型单独调用）
    model_param: LLMParam = LLMParam()  # 大模型参数，默认使用LLMParam的默认值
    model_name: str = "default"  # 调用的模型名称（与配置中的模型名称对应）
```

## 4.3 大模型响应模型（LLMResponse）

统一的大模型响应结果模型，支持向量模型、聊天模型、多模态模型的响应结果封装，包含标准化的状态码、错误信息，遵循系统统一异常码规范。

```python
from typing import List, Optional, Dict
from dataclasses import dataclass

@dataclass
class MultimodalResult:
    """多模态模型响应子模型"""
    text_result: Optional[str] = None  # 文本类结果（描述、回答、识别结果等）
    media_result: Optional[Dict] = None  # 媒体类结果（生成的图片/音频链接等，可选）
    confidence: Optional[float] = None  # 结果置信度（可选）

@dataclass
class LLMResponse:
    """大模型统一响应模型（支持多模态），遵循系统统一异常码规范"""
    code: str               # 响应码：SUCCESS（成功）、对应系统异常码（失败）
    message: str            # 响应信息：成功为"ok"，失败为具体错误信息
    vector_result: Optional[List[List[float]]] = None  # 向量模型结果：二维浮点型列表（批量向量）
    chat_result: Optional[str] = None  # 聊天模型结果：生成的文本内容
    multimodal_result: Optional[MultimodalResult] = None  # 多模态模型结果
    request_info: Optional[Dict] = None  # 请求信息（可选，用于调试）
    cost_time: Optional[float] = None  # 调用耗时（秒，可选）
    trace_id: Optional[str] = None  # 链路追踪ID（可选，遵循系统日志规范）
```

# 5. 核心接口设计（抽象基类）

本模块的核心接口均定义在core/base.py中，采用抽象基类（ABC）实现，强制子类实现所有抽象方法，确保模块内部及与外部模块的接口一致性。核心接口分为适配器抽象接口和服务抽象接口两类，涵盖向量、聊天、多模态三种模型类型。

## 5.1 通用大模型适配器抽象接口（BaseLLMAdapter）

定义所有大模型适配器的通用接口，是向量模型、聊天模型、多模态模型适配器的基类，屏蔽不同厂商、不同类型大模型的接口差异。

```python
from abc import ABC, abstractmethod
from typing import Any
from llm_adapter_module.model.data_model import LLMRequest, LLMResponse

class BaseLLMAdapter(ABC):
    """大模型通用适配器抽象基类，所有模型适配器必须继承此类"""
    @abstractmethod
    def __init__(self, model_name: str):
        """
        初始化适配器
        :param model_name: 模型名称，与配置中的模型名称对应
        """
        pass

    @abstractmethod
    def call(self, request: LLMRequest) -> LLMResponse:
        """
        大模型核心调用方法
        :param request: 大模型统一请求模型
        :return: 大模型统一响应模型
        """
        pass

    @abstractmethod
    def check_config(self) -> bool:
        """
        检查模型配置是否完整（如API密钥、地址是否存在）
        :return: 配置完整返回True，否则返回False
        """
        pass
```

## 5.2 向量模型适配器抽象接口（BaseVectorAdapter）

继承自BaseLLMAdapter，为向量模型适配器提供专属抽象接口，强化向量模型的批量处理、向量归一化等特性。

```python
from abc import abstractmethod
from llm_adapter_module.core.base import BaseLLMAdapter
from llm_adapter_module.model.data_model import LLMRequest, LLMResponse
from typing import List

class BaseVectorAdapter(BaseLLMAdapter):
    """向量模型适配器抽象基类，继承自BaseLLMAdapter，所有向量模型适配器必须继承此类"""
    @abstractmethod
    def embed_single(self, text: str, request: LLMRequest) -> List[float]:
        """
        单条文本向量化（专属方法）
        :param text: 单条输入文本
        :param request: 大模型统一请求模型（含参数）
        :return: 一维浮点型向量列表
        """
        pass

    @abstractmethod
    def embed_batch(self, texts: List[str], request: LLMRequest) -> List[List[float]]:
        """
        批量文本向量化（专属方法）
        :param texts: 批量输入文本列表
        :param request: 大模型统一请求模型（含参数）
        :return: 二维浮点型向量列表
        """
        pass
```

## 5.3 聊天模型适配器抽象接口（BaseChatAdapter）

继承自BaseLLMAdapter，为聊天模型适配器提供专属抽象接口，强化聊天模型的上下文管理、生成控制等特性。

```python
from abc import abstractmethod
from llm_adapter_module.core.base import BaseLLMAdapter
from llm_adapter_module.model.data_model import LLMRequest, LLMResponse
from typing import List, Dict

class BaseChatAdapter(BaseLLMAdapter):
    """聊天模型适配器抽象基类，继承自BaseLLMAdapter，所有聊天模型适配器必须继承此类"""
    @abstractmethod
    def generate(self, prompt: str, request: LLMRequest) -> str:
        """
        单轮文本生成（专属方法）
        :param prompt: 输入提示词
        :param request: 大模型统一请求模型（含参数）
        :return: 生成的文本内容
        """
        pass

    @abstractmethod
    def chat_with_context(self, messages: List[Dict], request: LLMRequest) -> str:
        """
        多轮对话（上下文管理，专属方法）
        :param messages: 对话上下文列表，格式：[{"role": "user/assistant", "content": "文本"}]
        :param request: 大模型统一请求模型（含参数）
        :return: 生成的回复内容
        """
        pass
```

## 5.4 多模态模型适配器抽象接口（BaseMultimodalAdapter）

继承自BaseLLMAdapter，为多模态模型适配器提供专属抽象接口，强化多模态模型的跨模态输入处理、混合内容理解等特性。

```python
from abc import abstractmethod
from llm_adapter_module.core.base import BaseLLMAdapter
from llm_adapter_module.model.data_model import LLMRequest, LLMResponse, MediaContent
from typing import List, Dict, Optional

class BaseMultimodalAdapter(BaseLLMAdapter):
    """多模态模型适配器抽象基类，继承自BaseLLMAdapter，所有多模态模型适配器必须继承此类"""
    @abstractmethod
    def understand_text_media(self, text: str, media_list: List[MediaContent], request: LLMRequest) -> MultimodalResult:
        """
        文本+媒体混合理解（专属方法）
        :param text: 文本指令/问题
        :param media_list: 媒体内容列表
        :param request: 大模型统一请求模型（含参数）
        :return: 多模态结果（文本+可选媒体）
        """
        pass

    @abstractmethod
    def media_to_text(self, media_list: List[MediaContent], request: LLMRequest) -> str:
        """
        媒体内容转文本（专属方法，如图片OCR、音频转写、视频字幕提取）
        :param media_list: 媒体内容列表
        :param request: 大模型统一请求模型（含参数）
        :return: 转换后的文本内容
        """
        pass

    @abstractmethod
    def multimodal_chat(self, messages: List[Dict], request: LLMRequest) -> MultimodalResult:
        """
        多模态多轮对话（上下文管理，专属方法）
        :param messages: 对话上下文列表，格式：[{"role": "user/assistant", "content": "文本", "media": [MediaContent]}]
        :param request: 大模型统一请求模型（含参数）
        :return: 多模态回复结果
        """
        pass
```

## 5.5 大模型统一服务抽象接口（BaseLLMService）

定义模块对外的核心服务接口，是上层模块调用大模型的唯一入口，封装适配器选择、请求校验、文件内容适配、异常处理等逻辑，天然支持多类型模型调用。

```python
from abc import ABC, abstractmethod
from llm_adapter_module.model.data_model import LLMRequest, LLMResponse, FileContent

class BaseLLMService(ABC):
    """大模型统一服务抽象基类，模块对外唯一核心服务接口（支持多模态）"""
    @abstractmethod
    def init_adapters(self) -> None:
        """
        初始化所有模型适配器（从配置中加载，自动注册向量/聊天/多模态适配器）
        :return: None
        """
        pass

    @abstractmethod
    def call_llm(self, request: LLMRequest) -> LLMResponse:
        """
        大模型统一调用方法（对外核心入口，支持所有类型模型）
        :param request: 大模型统一请求模型
        :return: 大模型统一响应模型
        """
        pass

    @abstractmethod
    def call_by_file(self, file_content: FileContent, request_type: str, model_param: LLMRequest.LLMParam = None) -> LLMResponse:
        """
        基于文件内容的大模型调用（专属方法，简化文件场景调用，支持多模态文件）
        :param file_content: 文件内容模型（支持多模态）
        :param request_type: 请求类型：VECTOR/CHAT/MULTIMODAL
        :param model_param: 大模型参数，可选
        :return: 大模型统一响应模型
        """
        pass

    @abstractmethod
    def validate_request(self, request: LLMRequest) -> (bool, str):
        """
        请求参数校验（专属方法，支持多类型模型校验）
        :param request: 大模型统一请求模型
        :return: 校验结果（bool）、错误信息（str，成功为空）
        """
        pass
```

# 6. 核心实现设计（框架说明，不包含具体代码）

本模块的具体实现类定义在core/impl.py中，均继承自对应的抽象基类，实现所有抽象方法。核心实现包含厂商适配器实现、统一服务实现，同时集成系统基础支撑层的配置、日志、异常处理模块，以下为核心实现框架说明。

## 6.1 多类型模型适配器实现框架

各厂商适配器均遵循对应抽象接口，实现专属方法，屏蔽厂商差异，以下为核心实现框架：

- 向量模型适配器：实现BaseVectorAdapter，封装文本向量化逻辑，支持单条/批量处理；

- 聊天模型适配器：实现BaseChatAdapter，封装文本生成与多轮对话逻辑；

- 多模态模型适配器：实现BaseMultimodalAdapter，封装文本+媒体混合处理、媒体转文本、多模态对话逻辑，支持图片、音频、视频等多种媒体输入。

## 6.2 大模型统一服务实现框架

LLMService作为模块对外的核心服务实现，继承自BaseLLMService，核心功能框架如下：

1. 适配器注册与加载：从配置中读取所有类型模型（向量/聊天/多模态）的配置，自动注册对应的适配器实例；

2. 请求校验：针对不同请求类型（VECTOR/CHAT/MULTIMODAL）进行参数校验，确保输入合法（如多模态模型需校验媒体格式与大小）；

3. 输入适配：对接文件解析模块，将FileContent转换为模型可接受的输入格式（文本分段、媒体预处理等）；

4. 适配器路由：根据request_type与model_name选择对应的适配器，调用核心call方法；

5. 异常处理与响应封装：捕获调用过程中的异常，按系统统一异常码规范封装响应，包含trace_id与耗时信息。

# 7. 模块配置规范

本模块的配置遵循系统统一配置规范，配置项定义在系统全局配置文件中（由配置管理模块加载），模块自身不单独维护配置文件，仅通过配置管理模块读取配置。配置项采用分层命名，便于管理和扩展，核心配置项如下。

## 7.1 配置项结构（YAML格式）

```yaml
# 大模型对接模块全局配置（支持向量/聊天/多模态）
llm:
  # 默认模型配置
  default_vector_model: "text-embedding-ada-002"  # 默认向量模型
  default_chat_model: "gpt-3.5-turbo"              # 默认聊天模型
  default_multimodal_model: "gpt-4-vision-preview" # 默认多模态模型
  # 厂商配置：OpenAI（可扩展智谱AI、文心一言、Midjourney等）
  openai:
    # 向量模型
    text-embedding-ada-002:
      api_key: "${OPENAI_EMBED_API_KEY}"  # 从环境变量读取，避免明文
      api_base: "https://api.openai.com/v1"
      request_type: "VECTOR"              # 请求类型：VECTOR
      adapter_class: "OpenAIVectorAdapter" # 适配器类名
    # 聊天模型
    gpt-3.5-turbo:
      api_key: "${OPENAI_CHAT_API_KEY}"   # 从环境变量读取，避免明文
      api_base: "https://api.openai.com/v1"
      request_type: "CHAT"                # 请求类型：CHAT
      adapter_class: "OpenAIChatAdapter"  # 适配器类名
    # 多模态模型
    gpt-4-vision-preview:
      api_key: "${OPENAI_MULTIMODAL_API_KEY}"  # 从环境变量读取
      api_base: "https://api.openai.com/v1"
      request_type: "MULTIMODAL"               # 请求类型：MULTIMODAL
      adapter_class: "OpenAIMultimodalAdapter" # 适配器类名
      support_media: ["image", "audio"]        # 支持的媒体类型
      max_media_size: 20                      # 单媒体最大大小（MB）
  # 模块通用配置
  common:
    max_retry: 3                           # 大模型调用最大重试次数
    timeout: 30                            # 调用超时时间（秒）
    batch_size: 32                         # 默认批量处理大小
    normalize_vector: true                 # 向量是否默认归一化
    media_temp_dir: "temp/media"           # 媒体临时存储目录
```

## 7.2 配置加载规则

1. 环境变量注入：所有敏感配置（如API密钥）均通过环境变量注入，格式为${ENV_VAR}，由配置管理模块解析，避免明文存储；

2. 默认值兜底：所有配置项均提供默认值，未配置时使用模块内部默认值，确保模块正常运行；

3. 分层管理：按「厂商→模型名称」分层配置，区分向量/聊天/多模态模型类型，新增厂商/模型时仅需在配置中添加对应节点，无需修改代码；

4. 多模态专属配置：多模态模型配置包含支持的媒体类型、最大媒体大小、临时存储目录等专属配置，适配多模态输入特性；

5. 动态加载：配置支持热加载（依赖配置管理模块的热加载能力），修改配置后无需重启模块即可生效。

# 8. 模块调用规范

本模块对外提供唯一的统一调用入口（LLMService.call_llm和LLMService.call_by_file），上层模块（RAG、Agent）通过该入口调用所有类型大模型（向量/聊天/多模态），无需关注底层适配器实现。调用流程遵循系统统一的接口调用规范，确保交互一致性。

## 8.1 基础调用流程

1. 上层模块初始化LLMService实例（全局只需初始化一次）；

2. 上层模块构建LLMRequest请求模型，设置请求类型、输入、模型参数等；

3. 上层模块调用LLMService.call_llm方法，传入LLMRequest实例；

4. 大模型对接模块完成请求校验、适配器选择、大模型调用；

5. 上层模块接收LLMResponse响应模型，根据code判断调用结果，按请求类型处理返回数据（向量结果/聊天结果/多模态结果）。

## 8.2 调用示例（分类型）

### 8.2.1 向量模型调用（RAG场景）

```python
# 1. 导入模块
from llm_adapter_module.core.impl import LLMService
from llm_adapter_module.model.data_model import LLMRequest, LLMParam
from document_parser_module.core.impl import LocalDocumentParser

# 2. 初始化实例
llm_service = LLMService()
document_parser = LocalDocumentParser()

# 3. 解析文本文件
file_content = document_parser.parse_file("data_docs/test.md")

# 4. 构建向量模型请求
vector_request = LLMRequest(
    request_type="VECTOR",
    file_content=file_content,
    model_name="text-embedding-ada-002",
    model_param=LLMParam(batch_size=64, normalize=True)
)

# 5. 调用大模型对接模块
vector_response = llm_service.call_llm(vector_request)

# 6. 处理响应（向量结果用于RAG检索）
if vector_response.code == "SUCCESS":
    vectors = vector_response.vector_result
    print(f"文件向量化成功，生成{len(vectors)}个向量")
```

### 8.2.2 聊天模型调用（Agent对话场景）

```python
# 1. 导入模块
from llm_adapter_module.core.impl import LLMService
from llm_adapter_module.model.data_model import LLMRequest, LLMParam

# 2. 初始化实例
llm_service = LLMService()

# 3. 构建聊天模型请求
chat_request = LLMRequest(
    request_type="CHAT",
    input_text="请规划一个RAG系统开发流程",
    model_name="gpt-3.5-turbo",
    model_param=LLMParam(temperature=0.5, max_tokens=1000)
)

# 4. 调用大模型对接模块
chat_response = llm_service.call_llm(chat_request)

# 5. 处理响应（聊天结果用于Agent交互）
if chat_response.code == "SUCCESS":
    plan = chat_response.chat_result
    print(f"开发流程规划：{plan}")
```

### 8.2.3 多模态模型调用（Agent图文理解场景）

```python
# 1. 导入模块
from llm_adapter_module.core.impl import LLMService
from llm_adapter_module.model.data_model import LLMRequest, LLMParam, MediaContent
from document_parser_module.core.impl import LocalDocumentParser

# 2. 初始化实例
llm_service = LLMService()
document_parser = LocalDocumentParser()

# 3. 解析多模态文件（含图片+文本）
file_content = document_parser.parse_file("data_docs/multimodal_report.pdf")

# 4. 构建多模态模型请求（直接使用文件中的媒体内容）
multimodal_request = LLMRequest(
    request_type="MULTIMODAL",
    input_text="请分析这份报告中的图表数据，总结核心结论",
    file_content=file_content,
    model_name="gpt-4-vision-preview",
    model_param=LLMParam(media_process_mode="extract")
)

# 5. 调用大模型对接模块
multimodal_response = llm_service.call_llm(multimodal_request)

# 6. 处理响应（多模态结果用于Agent分析）
if multimodal_response.code == "SUCCESS":
    conclusion = multimodal_response.multimodal_result.text_result
    print(f"图表分析结论：{conclusion}")
```

### 8.2.4 简化调用示例（基于文件，支持多类型）

```python
from llm_adapter_module.core.impl import LLMService
from document_parser_module.core.impl import LocalDocumentParser

# 初始化实例
llm_service = LLMService()
document_parser = LocalDocumentParser()

# 解析文件（文本/多模态均可）
file_content = document_parser.parse_file("data_docs/test_file.pdf")

# 基于文件的向量模型调用（简化）
vector_response = llm_service.call_by_file(file_content, request_type="VECTOR")

# 基于文件的聊天模型调用（简化）
chat_response = llm_service.call_by_file(file_content, request_type="CHAT")

# 基于文件的多模态模型调用（简化）
multimodal_response = llm_service.call_by_file(file_content, request_type="MULTIMODAL")
```

# 9. 扩展规范

本模块采用适配器模式和分层设计，具备良好的可扩展性，新增厂商模型、文件/媒体类型、功能特性时，无需修改原有代码，仅需按规范扩展即可。

## 9.1 新增厂商模型扩展步骤

1. 新增适配器类：在core/impl.py中新增厂商的向量/聊天/多模态模型适配器类，继承自对应抽象基类（BaseVectorAdapter/BaseChatAdapter/BaseMultimodalAdapter），实现所有抽象方法；

2. 配置注册：在系统全局配置文件中添加该厂商的模型配置节点，指定api_key、api_base、request_type、adapter_class、支持的媒体类型（多模态模型）等；

3. 初始化适配器：在LLMService.init_adapters方法中添加厂商的适配器注册逻辑（若为通用厂商，可通过配置自动注册）；

4. 编写测试用例：在tests/test_impl.py中编写该适配器的测试用例，覆盖核心功能；

5. 更新文档：更新模块README.md，添加该厂商模型的调用说明。

## 9.2 新增文件/媒体类型扩展步骤

1. 扩展文件解析：对接系统文档解析模块，新增文件/媒体类型的解析逻辑，生成标准化的FileContent模型（含media_contents字段）；

2. 扩展媒体预处理：在模块utils/tool_functions.py中新增该媒体类型的预处理逻辑（如格式转换、压缩、元信息提取）；

3. 配置更新：在系统配置中添加该文件/媒体类型到支持列表，更新多模态模型的support_media配置；

4. 编写测试用例：测试文件解析后的数据能否正常被多模态模型调用；

## 9.3 新增功能特性扩展步骤

1. 扩展抽象接口：若新增功能为所有适配器通用，在BaseLLMAdapter中新增抽象方法；若为特定类型适配器专属，在对应抽象基类（如BaseMultimodalAdapter）中新增抽象方法；

2. 实现功能：在对应的适配器类中实现新增的抽象方法；

3. 扩展服务接口：若需要对外提供该功能，在BaseLLMService和LLMService中新增服务方法；

4. 编写测试用例：覆盖新增功能的测试。

# 10. 测试规范

本模块的测试遵循系统统一测试规范，测试用例存放在tests/目录下，必须覆盖核心功能、异常场景、扩展场景，确保模块功能正常、稳定、可扩展。

## 10.1 测试范围

1. 适配器测试：各厂商不同类型适配器（向量/聊天/多模态）的call方法、专属方法、配置检查；

2. 统一服务测试：请求校验、适配器加载、统一调用、基于文件的调用（文本/多模态文件）；

3. 工具函数测试：向量归一化、文本分段、媒体预处理、参数处理等；

4. 异常场景测试：配置缺失、参数错误、大模型调用失败、超时、媒体格式不支持等；

5. 扩展场景测试：新增厂商模型的调用、新增文件/媒体类型的适配。

## 10.2 测试用例要求

1. 单元测试：覆盖每个类、每个方法的核心功能，确保独立功能正常；

2. 集成测试：覆盖模块与文档解析模块、向量数据库模块、上层RAG/Agent模块的集成调用；

3. 异常测试：覆盖所有可能的异常场景，确保异常处理符合系统统一异常码规范；

4. 性能测试：测试批量向量化、大文件/媒体解析后的调用性能，确保满足系统性能指标；

5. 多模态专项测试：测试不同媒体类型（图片/音频/视频）的输入适配与模型调用效果。

## 10.3 测试用例示例框架（tests/test_impl.py）

```python
import unittest
from llm_adapter_module.core.impl import LLMService
from llm_adapter_module.model.data_model import LLMRequest, LLMParam, FileContent, MediaContent
from exception_module.core.impl import CONFIG_KEY_MISSING

class TestLLMAdapterModule(unittest.TestCase):
    def setUp(self):
        """测试前置：初始化实例与测试数据"""
        self.llm_service = LLMService()
        # 向量/聊天模型名称
        self.vector_model_name = "text-embedding-ada-002"
        self.chat_model_name = "gpt-3.5-turbo"
        self.multimodal_model_name = "gpt-4-vision-preview"
        # 构建测试文本文件内容模型
        self.text_file_content = FileContent(
            file_name="test.md",
            file_type="md",
            text_content="测试文本内容",
            split_contents=["测试文本内容"]
        )
        # 构建测试多模态文件内容模型
        self.multimodal_file_content = FileContent(
            file_name="test_multimodal.pdf",
            file_type="pdf",
            text_content="测试多模态文件文本",
            media_contents=[
                MediaContent(
                    media_type="image",
                    media_path="test_image.png",
                    media_metadata={"width": 1920, "height": 1080, "format": "png"}
                )
            ]
        )

    def test_vector_adapter_call(self):
        """测试向量模型适配器调用"""
        request = LLMRequest(
            request_type="VECTOR",
            file_content=self.text_file_content,
            model_name=self.vector_model_name
        )
        response = self.llm_service.call_llm(request)
        self.assertEqual(response.code, "SUCCESS")
        self.assertIsNotNone(response.vector_result)

    def test_chat_adapter_call(self):
        """测试聊天模型适配器调用"""
        request = LLMRequest(
            request_type="CHAT",
            input_text="测试聊天",
            model_name=self.chat_model_name
        )
        response = self.llm_service.call_llm(request)
        self.assertEqual(response.code, "SUCCESS")
        self.assertIsNotNone(response.chat_result)

    def test_multimodal_adapter_call(self):
        """测试多模态模型适配器调用"""
        request = LLMRequest(
            request_type="MULTIMODAL",
            input_text="分析图片内容",
            file_content=self.multimodal_file_content,
            model_name=self.multimodal_model_name
        )
        response = self.llm_service.call_llm(request)
        self.assertEqual(response.code, "SUCCESS")
        self.assertIsNotNone(response.multimodal_result)

    def test_request_validate(self):
        """测试请求参数校验（含多模态）"""
        # 无效多模态请求（无媒体输入）
        invalid_multimodal_request = LLMRequest(
            request_type="MULTIMODAL",
            input_text="分析图片",
            model_name=self.multimodal_model_name
        )
        result, msg = self.llm_service.validate_request(invalid_multimodal_request)
        self.assertEqual(result, False)
        self.assertIn("多模态请求需提供媒体输入", msg)

if __name__ == "__main__":
    unittest.main()
```

# 11. 交付物清单（强制）

模块开发完成后，需提交以下交付物，确保符合系统统一交付规范，便于集成、测试、部署和维护。

1. core/base.py：抽象基类文件，定义向量/聊天/多模态适配器、服务的核心抽象接口（ABC）；

2. core/impl.py：具体实现类文件，包含厂商适配器（向量/聊天/多模态）、大模型统一服务的具体实现框架；

3. model/data_model.py：数据模型文件，定义FileContent（含多模态）、LLMRequest、LLMResponse等统一数据模型；

4. utils/tool_functions.py：工具函数文件，包含向量归一化、文本分段、媒体预处理、参数处理等专属工具函数；

5. config/config.py：配置文件，读取全局配置，补充模块专属配置；

6. tests/test_impl.py：测试用例文件，覆盖模块核心功能、异常场景、集成场景、多模态场景；

7. README.md：模块说明文档，详细说明模块功能、核心接口、使用方法、依赖项、扩展步骤、常见问题；

8. requirements.txt：模块依赖包清单，注明包名称与版本（如大模型SDK、媒体处理库等）。

# 12. 可替换性约束（强制）
为确保模块的可替换性和系统的整体一致性，本模块遵循以下强制约束：
1. 上层模块仅依赖抽象接口：上层模块（RAG、Agent）只能依赖本模块的抽象基类（BaseLLMService、各类型适配器接口）或统一数据模型，禁止直接引用impl.py中的内部私有方法和具体实现类； 
2. 适配器仅实现抽象接口：所有厂商、所有类型的适配器必须继承对应的抽象基类，实现所有抽象方法，不得修改抽象基类的定义； 
3. 数据格式统一：模块内部及与外部模块的所有数据交互，必须使用本模块定义的统一数据模型（FileContent、LLMRequest、LLMResponse）； 
4. 异常码统一：模块所有异常必须遵循系统统一异常码规范，抛出的异常均为系统基础异常类（SystemBaseException）或其子类； 
5. 配置规范统一：模块配置必须遵循系统统一配置规范，通过配置管理模块加载，不单独维护配置文件； 
6. 日志规范统一：模块所有日志记录必须遵循系统统一日志规范，使用日志模块（log_module）的 SystemLogger 类，包含 trace_id、模块名称等信息； 
7. 多模态兼容性约束：新增任何类型适配器或工具函数，必须兼容现有统一数据模型，不得破坏多模态输入输出的一致性。
# 13. 常见问题（FAQ）
## 13.1 新增厂商多模态模型后，调用时提示 “未注册的模型名称”？
解答：检查配置文件中是否添加了该模型的配置节点，且model_name与配置中的节点名称一致；检查LLMService.init_adapters方法中是否添加了该厂商多模态适配器的注册逻辑。
## 13.2 多模态模型调用时提示 “媒体格式不支持”？
解答：检查该多模态模型的配置中support_media字段是否包含当前媒体类型；检查媒体文件格式是否符合模型要求（如图片仅支持 png/jpg，音频仅支持 mp3）；检查媒体文件大小是否超过配置中的max_media_size限制。
## 13.3 向量模型调用后，向量维度与向量数据库不一致？
解答：检查大模型配置中的模型名称是否正确，不同向量模型的输出维度不同；检查向量数据库的维度配置是否与大模型输出维度一致；检查适配器是否开启了向量归一化（归一化不改变维度）。
## 13.4 基于多模态文件的调用，生成结果未包含媒体分析内容？
解答：检查文件解析模块是否正确提取了媒体内容，FileContent.media_contents是否不为空；检查多模态模型请求的media_process_mode参数是否为auto或extract；检查输入文本指令是否明确要求分析媒体内容（如 “分析图片中的数据”）。
## 13.5 大模型调用时提示 “配置缺失”？
解答：检查配置文件中是否配置了该模型的api_key、api_base等核心配置；检查环境变量是否正确注入（敏感配置通过环境变量读取）；检查配置管理模块是否正常加载配置。
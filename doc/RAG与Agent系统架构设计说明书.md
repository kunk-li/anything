# RAG与Agent系统架构设计说明书

| 文档版本  | v2.0       |
|:------|:-----------|
| 最后更新  | 2026-05-27 |
| 维护责任人 | 架构负责人      |
| 状态    | 修订版(实现对齐) |

> v2.0 修订:跟随两轮架构优化(共 22 个 Task,详见下方"修订日志"与"实现状态一览"),
> 把"文档说的"与"代码做的"重新对齐。新增的 schema_module / deps_module / chunker_module /
> rag_module.extensions 子目录与 BasicDeps / StartupError / RequestEnvelope /
> handle_exception_to_envelope / ReAct 等概念已在本文档相应章节补充。

## 修订日志

| 版本 | 日期 | 主要变更 |
|---|---|---|
| v2.0 | 2026-05-27 | 实现对齐第二轮:数据层 DI 统一 + StartupError fail-fast + 内部 schema 化 + ReAct + Rerank/Rewrite 接通 + 业务质量评测集 + 配置优先级文档化 |
| v1.1 | 2026-03-19 | 第一轮修订:命名/索引链路/接口边界 |
| v1.0 | 初版    | 系统整体架构、ABC 抽象接口、5 层分层模型 |

## 实现状态一览(v2.0)

> 章节后括号标注实现状态:✅ 已实现 | 🔧 部分实现 | 📋 规划中

### 基础设施(已实现)
- ✅ **schema_module** — Pydantic v2 数据契约(RequestEnvelope / ResponseEnvelope /
  Chunk / RetrievedChunk / Citation / ToolStep)
- ✅ **deps_module** — BasicDeps DI 容器 + StartupError + is_dev_mode +
  handle_exception_to_envelope(共用异常包装)
- ✅ **chunker_module** — 实现文档第 11 章 Chunking 规范
- ✅ **ABC 契约严格对齐** — 10 个 (Base, Impl) 配对全部 aligned,有
  `scripts/check_abc_alignment.py` 守护
- ✅ **DI 注入贯通全链路** — bootstrap → data layer → business layer → interface → application,
  全程只构造一份 BasicDeps
- ✅ **启动 fail-fast** — 默认严格模式,任一关键依赖初始化失败抛 StartupError;
  `ANYTHING_DEV_MODE=1` 显式启用 dev 降级

### 业务能力
- ✅ **统一请求/响应信封** — RequestHandler 边界走 Pydantic 强校验
- ✅ **Query Rewrite**(文档 12.2 节) — LLMQueryRewriter 接通,LLM 不可用时退化为原 query
- ✅ **Reranker**(文档 12.4 节) — LLMReranker 接通,LLM 不可用时退化为原顺序
- ✅ **Agent LLM 驱动规划**(parse_task) — JSON 解析失败/未注册工具/异常时 fallback 规则式
- ✅ **Agent ReAct 多轮规划** — `agent.execution_strategy=react` 启用,observe→reflect→next
  循环,7 类降级路径
- ✅ **Citation 引用回溯** — chunk_id ↔ vector_id ↔ doc 三层映射稳定

### 工程化
- ✅ **122 个单测 / 9 模块** — `scripts/run_tests.sh` 本地回归,`.github/workflows/ci.yml`
  矩阵(Python 3.10 / 3.12)
- ✅ **业务质量评测集** — `evaluation/` 目录,RAG + Agent 数据集 + 评测脚本 + CI 阈值守护
- ✅ **配置优先级文档化** — `docs/configuration-priority.md` 5 个配置源 +
  推荐入口 `ConfigManager.get_effective_value`
- 🔧 **GitHub Actions CI** — yml 已就绪,首次推送跑红,正在调试(详见 Task #13)

### 规划中(尚未做)
- 📋 **Cross-encoder Reranker** — `BaseReranker` 抽象已就位,可直接换实现
- 📋 **真实生产 LLM 接入** — 需要 API key 配置 + secrets 管理
- 📋 **业务质量指标进 CI** — 需要 nightly workflow 配 secrets
- 📋 **ReAct 失败时强制重新规划** — 当前依赖 LLM 感知,未做硬约束
- 📋 **ConfigManager 启动期打印生效配置摘要** — 便于排查"为什么 X 值不对"
- 📋 **多语言/英文 query 评测覆盖** — 当前评测集只有中文

## 设计偏离声明(v2.0 新增)

以下模块**有意偏离** §3 "统一项目结构规范"中的 `core/base.py + impl.py` 二分结构,
理由是它们是**声明式数据/容器/无状态算法**而非行为类:

| 模块 | 位置 | 偏离理由 |
|---|---|---|
| `schema_module` | `basic_support/` | Pydantic schema 是数据契约,无抽象/实现二分需求 |
| `deps_module` | `basic_support/` | 依赖容器 + 工厂函数,无行为对象 |
| `chunker_module` | `data_layer/` | 无状态算法函数集 |
| `rag_module/extensions/` | `business/rag_module/` | 子目录用单文件(rewriter.py + reranker.py)直接包含 Base + 默认实现,小组件二分冗余 |

每个偏离模块的 README.md 中也都明确写了偏离声明。

# 1. 文档概述

## 1.1 文档目的

本文档为RAG（检索增强生成）与Agent（智能代理）系统的完整架构设计说明书，用于指导开发团队（含初学者）进行模块化、独立化开发。文档明确了系统整体架构、各模块职责、接口定义、数据格式、项目结构、通用核心模块及开发规范，确保所有开发人员可根据分配的模块独立开发，无需相互依赖，开发完成后可通过统一接口集成，保障系统整体一致性与可扩展性。

## 1.2 适用人群

本团队所有开发人员（含资深开发者与初学者）、测试人员、项目管理人员，作为开发、测试、部署及维护的唯一标准依据。

## 1.3 核心需求回顾

- 系统功能：实现RAG检索增强生成与Agent智能代理核心能力，支持两者协同工作。

- 开发语言：后端统一采用 Python 3.10+。
- 运行最低版本：Python 3.10。
- 推荐版本：Python 3.12。
- CI / 生产镜像建议同时验证 Python 3.10 与 3.12 兼容性。

- 开发模式：多开发人员异地协同，各模块独立开发、互不影响，基于说明书即可完成各自任务。

- 文档要求：详细、易懂，适配初学者，明确所有可提前定义的内容（接口、数据格式、项目结构等）。

- 模块要求：各模块代码可相互交换，数据格式统一，项目结构一致，核心通用模块需包含抽象基类（ABC）。

## 术语定义

| 术语        | 定义                                               |
|-----------|--------------------------------------------------|
| RAG       | 检索增强生成，通过检索外部知识库的相关信息，辅助大模型生成更准确、更具针对性的回答。       |
| Agent     | 智能代理，具备任务解析、工具调用、决策规划能力，可自主完成复杂任务，协同RAG模块提升响应质量。 |
| ABC       | 抽象基类，定义模块的核心接口与方法，强制子类实现，保障模块一致性。                |
| 向量数据库     | 用于存储文档向量表示，支持高效相似性检索，为RAG模块提供检索能力。               |
| Embedding | 文本嵌入，将文本转换为高维向量，用于向量检索与语义匹配。                     |
| 工具调用      | Agent模块调用外部工具（如RAG检索、计算器、API等）完成特定任务的能力。         |

# 2. 系统整体架构设计

## 2.1 架构总览

本系统采用分层架构+模块化设计，整体分为5层，每层包含多个独立模块，各模块通过统一接口通信，确保独立开发与集成兼容性。架构分层从下到上依次为：基础支撑层、数据层、核心业务层、接口层、应用层。

核心设计原则：模块解耦、接口统一、结构一致、可扩展、易维护，适配初学者开发，明确各模块边界与职责，避免开发冲突。

## 2.2 架构分层详情

| 架构分层  | 核心职责                                      | 包含模块                                 | 开发优先级           |
|-------|-------------------------------------------|--------------------------------------|-----------------|
| 应用层   | 接收用户请求，展示系统响应，提供用户交互入口（如API接口调用、简单控制台）    | API服务模块、控制台交互模块                      | 低（最后开发，依赖接口层）   |
| 接口层   | 统一接口管理，实现各模块之间的通信，封装核心业务逻辑调用，提供标准化请求/响应格式 | 接口封装模块、请求响应处理模块                      | 中（核心模块开发完成后开发）  |
| 核心业务层 | 实现RAG与Agent核心功能，是系统核心模块集合                 | RAG模块、Agent模块、协同调度模块                 | 高（优先开发）         |
| 数据层   | 负责数据存储、读取、更新，包括文档数据、向量数据、Agent状态数据、大模型对接等 | 文档解析模块、文档存储模块、向量数据库模块、大模型对接模块、状态存储模块 | 高（与核心业务层同步开发）   |
| 基础支撑层 | 提供系统通用能力，支撑所有上层模块，包含通用工具、配置管理、日志、异常处理等    | 通用工具模块、配置管理模块、日志模块、异常处理模块            | 最高（最先开发，所有模块依赖） |

## 2.3 系统交互流程

### 2.3.1 RAG 场景：文件内容向量化入库流程

1. 应用层接收用户上传文件，通过接口层触发索引构建流程;
2. 文件解析服务完成文本提取、分段，生成标准化文档结构（包含 content、file_name、meta 等字段）；
3. 大模型统一服务接收向量化请求，调用向量模型适配器；
4. 向量模型适配器对接第三方 Embedding 模型，生成文本向量；
5. 基础数据模块将向量 + 文本元数据写入向量库 / 文本库，完成 RAG 知识库入库。

### 2.3.2 Agent+RAG 场景：文件内容智能问答流程

1. 应用层接收用户问题 + 上传文件，通过接口层转发至协同调度模块；
2. Agent 模块调用数据层文件解析服务，获取文件标准化文本；
3. Agent 模块触发 RAG 检索：调用数据层向量模型适配器，将用户问题向量化，检索向量库匹配文件片段；
4. Agent 模块拼接「检索结果 + 用户问题」，调用数据层聊天模型适配器；
5. 聊天模型适配器对接对话大模型，生成推理结果，经 Agent 模块优化后返回至接入层。

# 3. 统一项目结构规范

为确保各模块项目结构一致，所有模块（基础支撑层、数据层、核心业务层、接口层、应用层）均采用以下统一目录结构，开发者需严格遵循，不得随意修改目录名称与层级，初学者可直接复制该结构搭建项目。
每个模块必须包含以下必选目录与文件：

```
module_name/
├── __init__.py
├── core/
│   ├── __init__.py
│   ├── base.py
│   └── impl.py
├── config/
│   ├── __init__.py
│   └── config.py 或 config.yaml
├── tests/
│   ├── __init__.py
│   └── test_impl.py
├── README.md
└── requirements.txt（若模块无专属依赖，可为空或省略）
```

可选扩展目录（按模块职责选择）：

- model/：数据模型，如 request/response/dataclass
- utils/：工具函数
- tools/：工具注册与工具实现
- adapters/：输入/输出/渲染适配器
- storage/：本地历史、缓存、轻量存储
- prompt/：Prompt 模板
- examples/：批处理、脚本、演示样例
- router/、middleware/：仅应用层 API 模块可使用

说明：

- “统一结构”要求的是必选目录一致，不禁止模块根据职责增加可选扩展目录。
- 若模块新增可选扩展目录，必须在 README 中说明职责与边界。

## 3.1 目录结构说明

- module_name：模块根目录，名称必须全小写，多单词用下划线连接（如agent_module、document_store_module），与模块功能对应。

- __init__.py：每个目录必须包含，核心作用是将目录标识为Python模块，根目录的__init__.py需暴露模块核心类/方法（如from
  .core.impl import XXXClass），方便其他模块调用。

- core目录：核心逻辑存放处，base.py是抽象基类（ABC），定义模块必须实现的接口方法，impl.py是具体实现，继承base.py的抽象类，实现所有抽象方法。

- utils目录：模块专属工具函数，如数据格式转换、参数校验等，不包含核心业务逻辑，仅为模块提供辅助。

- config目录：模块专属配置，如数据库连接参数、模型路径等，可读取基础支撑层的全局配置，补充模块专属配置。

- tests目录：测试用例存放处，必须包含test_impl.py，覆盖模块核心功能的单元测试，初学者可参考示例编写简单测试（如参数校验、功能调用成功/失败场景）。

- README.md：模块说明文档，需详细说明模块功能、核心接口、使用方法、依赖项、常见问题，语言简洁易懂，适配初学者。

## 3.2 统一编码规范

为确保代码可交换、可维护、可复用，降低团队协作成本，本模块严格遵循系统统一编码规范，初学者需严格执行，资深开发者需做好规范把控，确保所有代码符合统一标准。

### 3.2.1 基础编码格式

- 编码格式：统一使用UTF-8编码，缩进采用4个空格（禁止使用Tab），每行代码长度不超过120字符，过长代码需合理换行，确保代码可读性，便于团队成员查阅和修改。

-
依赖管理：模块的所有依赖项统一写入根目录的requirements.txt文件，明确标注依赖包名称与版本号，避免版本冲突；若时间相关方法需额外依赖（如pytz用于时区处理），需在此文件中明确标注，便于其他开发者快速安装依赖，避免因依赖缺失导致模块无法正常运行。

- 代码文件：每个代码文件顶部需添加模块说明注释，注明文件功能、作者及创建日期，便于后续维护和追溯，提升代码可维护性。

### 3.2.2 命名规范

- 类名：PascalCase（如 `RequestHandler`、`FastAPIService`、`LocalStateStore`）
- 方法名/函数名：snake_case（如 `validate_request`、`create_app`、`save_state`）
- 变量名：snake_case（如 `trace_id`、`session_id`、`top_k`）
- 常量名：全大写下划线（如 `DEFAULT_TIMEOUT`、`SUPPORTED_FILE_TYPES`）
- 模块名/目录名：全小写下划线（如 `vector_db_module`、`request_response_module`）
- JSON 字段名：统一采用 snake_case

### 3.2.3 注释规范

- 类注释：使用文档字符串（"""
  ），详细说明类的功能、核心作用及适用场景，若有参数或返回值需明确标注；其中，工具类需明确说明其整合的功能范围，尤其是通用辅助工具类，需明确时间相关方法的涵盖范围，便于开发者快速了解类的用途。

- 方法/函数注释：使用文档字符串，详细说明方法功能、参数（名称、类型、含义、默认值）、返回值（类型、含义）及异常（异常类型、触发条件）；时间相关方法需明确标注时间格式、时区等关键信息，避免使用歧义，确保开发者能正确调用。

- 关键代码注释：对复杂逻辑、不易理解的代码，添加单行注释（#），说明逻辑用途、设计思路，便于后续维护和他人阅读；无需对简单、直观的代码添加注释，避免注释冗余，影响代码可读性。

- 注释语言：统一使用中文注释，语言简洁、准确，避免使用模糊、歧义的表述，确保注释的实用性，便于所有团队成员理解。

多进程编码规范：进程锁需确保“获取-释放”成对出现，避免死锁；每个进程的日志句柄需单独初始化，避免共用；日志写入完成后及时释放资源，减少内存占用。

# 4. 各模块详细设计（按分层顺序）

## 4.1 基础支撑层模块设计

基础支撑层是所有模块的依赖，提供通用能力，需最先开发，包含4个独立模块，各模块独立开发，互不依赖（除配置管理模块可被其他模块依赖）。

### 4.1.1 通用工具模块（common_utils_module）

详细设计见[基础支撑层-通用工具模块（common_utils_module）设计文档](%E5%9F%BA%E7%A1%80%E6%94%AF%E6%92%91%E5%B1%82-%E9%80%9A%E7%94%A8%E5%B7%A5%E5%85%B7%E6%A8%A1%E5%9D%97%EF%BC%88common_utils_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E6%96%87%E6%A1%A3.md)

### 4.1.2 配置管理模块（config_module）

详细设计见[基础支撑层-配置管理模块（config_module）设计文档](%E5%9F%BA%E7%A1%80%E6%94%AF%E6%92%91%E5%B1%82-%E9%85%8D%E7%BD%AE%E7%AE%A1%E7%90%86%E6%A8%A1%E5%9D%97%EF%BC%88config_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E6%96%87%E6%A1%A3.md)

### 4.1.3 日志模块（log_module）

详细设计见[基础支撑层-日志模块（log_module）设计文档](%E5%9F%BA%E7%A1%80%E6%94%AF%E6%92%91%E5%B1%82-%E6%97%A5%E5%BF%97%E6%A8%A1%E5%9D%97%EF%BC%88log_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E6%96%87%E6%A1%A3.md)

### 4.1.4 异常处理模块（exception_module）

详细设计见[基础支撑层-异常处理模块（exception_module）设计文档](%E5%9F%BA%E7%A1%80%E6%94%AF%E6%92%91%E5%B1%82-%E5%BC%82%E5%B8%B8%E5%A4%84%E7%90%86%E6%A8%A1%E5%9D%97%EF%BC%88exception_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E6%96%87%E6%A1%A3.md)

## 4.2 数据层模块设计

数据层负责系统所有数据的存储与读取，包含5个独立模块，依赖基础支撑层的通用工具、配置管理、日志、异常处理模块，各模块独立开发，互不依赖。

### 4.2.1 文档解析模块（document_parser_module）

详细设计见[数据层-文档解析模块（document_parser_module）设计说明书](%E6%95%B0%E6%8D%AE%E5%B1%82-%E6%96%87%E6%A1%A3%E8%A7%A3%E6%9E%90%E6%A8%A1%E5%9D%97%EF%BC%88document_parser_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)

### 4.2.2 文档存储模块（document_store_module）

详细设计见[数据层-文档存储模块（document_store_module）设计说明书](%E6%95%B0%E6%8D%AE%E5%B1%82-%E6%96%87%E6%A1%A3%E5%AD%98%E5%82%A8%E6%A8%A1%E5%9D%97%EF%BC%88document_store_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)

### 4.2.3 向量数据库模块（vector_db_module）

详细设计见[数据层-向量数据库模块（vector_db_module）设计说明书](%E6%95%B0%E6%8D%AE%E5%B1%82-%E5%90%91%E9%87%8F%E6%95%B0%E6%8D%AE%E5%BA%93%E6%A8%A1%E5%9D%97%EF%BC%88vector_db_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)

### 4.2.4 状态存储模块（state_store_module）

详细设计见[数据层-状态存储模块（state_store_module）设计说明书](%E6%95%B0%E6%8D%AE%E5%B1%82-%E7%8A%B6%E6%80%81%E5%AD%98%E5%82%A8%E6%A8%A1%E5%9D%97%EF%BC%88state_store_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)

### 4.2.5 大模型对接模块（llm_adapter_module）

详细设计见[数据层-大模型对接模块（llm_adapter_module）设计说明书.md](%E6%95%B0%E6%8D%AE%E5%B1%82-%E5%A4%A7%E6%A8%A1%E5%9E%8B%E5%AF%B9%E6%8E%A5%E6%A8%A1%E5%9D%97%EF%BC%88llm_adapter_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)

## 4.3 核心业务层模块设计

核心业务层实现RAG与Agent核心能力，包含RAG模块、Agent模块、协同调度模块。各模块通过统一接口通信，禁止相互直接依赖对方的内部实现类。

### 4.3.1 Embedding模块（embedding_module）

说明：原文中Embedding作为术语出现，但为保证完整性，这里补充一个独立Embedding模块，供RAG与向量库统一调用。

详细设计见[核心业务层-Embedding模块（embedding_module）设计说明书](%E6%A0%B8%E5%BF%83%E4%B8%9A%E5%8A%A1%E5%B1%82-Embedding%E6%A8%A1%E5%9D%97%EF%BC%88embedding_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)

### 4.3.2 RAG模块（rag_module）

详细设计见[核心业务层-RAG模块（rag_module）设计说明书](%E6%A0%B8%E5%BF%83%E4%B8%9A%E5%8A%A1%E5%B1%82-RAG%E6%A8%A1%E5%9D%97%EF%BC%88rag_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)

### 4.3.3 Agent模块（agent_module）

详细设计见[核心业务层-Agent模块（agent_module）设计说明书](%E6%A0%B8%E5%BF%83%E4%B8%9A%E5%8A%A1%E5%B1%82-Agent%E6%A8%A1%E5%9D%97%EF%BC%88agent_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)

### 4.3.4 协同调度模块（orchestrator_module）

详细设计见[核心业务层-协同调度模块（orchestrator_module）设计说明书](%E6%A0%B8%E5%BF%83%E4%B8%9A%E5%8A%A1%E5%B1%82-%E5%8D%8F%E5%90%8C%E8%B0%83%E5%BA%A6%E6%A8%A1%E5%9D%97%EF%BC%88orchestrator_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)

## 4.4 接口层模块设计

接口层用于统一请求/响应格式、校验参数、封装核心业务层调用，确保应用层只关心一个入口。

### 4.4.1 请求响应处理模块（request_response_module）

详细设计见[接口层-请求响应处理模块（request_response_module）设计说明书](%E6%8E%A5%E5%8F%A3%E5%B1%82-%E8%AF%B7%E6%B1%82%E5%93%8D%E5%BA%94%E5%A4%84%E7%90%86%E6%A8%A1%E5%9D%97%EF%BC%88request_response_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)

## 4.5 应用层模块设计

应用层提供用户入口（API服务/控制台），不包含业务逻辑，仅调用接口层。

### 4.5.1 API服务模块（api_service_module）

详细设计见[应用层-API服务模块（api_service_module）设计说明书](%E5%BA%94%E7%94%A8%E5%B1%82-API%E6%9C%8D%E5%8A%A1%E6%A8%A1%E5%9D%97%EF%BC%88api_service_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)

### 4.5.2 控制台交互模块（console_app_module）

详细设计见[应用层-控制台交互模块（console_app_module）设计说明书](%E5%BA%94%E7%94%A8%E5%B1%82-%E6%8E%A7%E5%88%B6%E5%8F%B0%E4%BA%A4%E4%BA%92%E6%A8%A1%E5%9D%97%EF%BC%88console_app_module%EF%BC%89%E8%AE%BE%E8%AE%A1%E8%AF%B4%E6%98%8E%E4%B9%A6.md)

# 5. 系统集成与启动规范（必须包含）

## 5.1 设计原则

- 统一通过 bootstrap 入口完成依赖注入
- 应用层只依赖接口层抽象接口
- 接口层只依赖协同调度抽象接口
- 核心业务层优先依赖抽象接口或统一输出结构，不直接依赖下层具体实现类
- 示例实现允许直接注入默认实现；生产实现必须支持替换

## 5.2 推荐组装方式

- build_basic_support()
- build_data_layer()
- build_business_layer()
- build_interface_layer()
- build_application_layer()

```python
def build_handler() -> BaseRequestHandler:
    llm_client = build_llm_client()
    vector_db = build_vector_db()
    doc_store = build_document_store()
    state_store = build_state_store()

    rag = SimpleRAG(
        llm_client=llm_client,
        vector_db=vector_db,
        doc_store=doc_store
    )

    agent = SimpleAgent(
        tools=build_tools(rag=rag),
        state_store=state_store
    )

    orchestrator = SimpleOrchestrator(
        rag_runner=rag,
        agent_runner=agent
    )

    return RequestHandler(orchestrator=orchestrator)


def build_api_app():
    handler = build_handler()
    return FastAPIService(handler=handler).create_app()

def build_console_app():
    handler = build_handler()
    return ConsoleApp(handler=handler)
```

## 5.3 安全要求

- 示例中不得再使用 `eval`
- 计算器工具需使用安全表达式解析方式或白名单运算实现

# 6. 数据构建与索引流程规范（修订）

## 6.1 标准索引链路（强制）

1. document_parser.parse_file / parse_folder
2. document_store.create_document / save_document
3. chunker.chunk_document
4. embedding.embed_texts
5. vector_db.upsert_vectors

## 6.2 关键约束

- 必须先切 chunk，再生成向量
- vector_id 推荐等于 chunk_id
- 向量 metadata 至少包含：doc_id、chunk_id、file_name、chunk_index
- 原始文档与 chunk 必须可回溯

说明：
chunker 为索引构建阶段的标准能力，可作为独立模块实现，也可先作为 rag_module 的内部组件实现；
无论实现位置如何，均必须遵循第 11 章 Chunking 规范。
```python
for p in parsed_list:
    doc = store.create_document(p["content"], p["file_name"])
    store.save_document(doc)

    chunks = chunker.chunk_document(
        doc_id=doc["doc_id"],
        content=doc["content"],
        file_name=doc["file_name"]
    )

    vectors = embedding.embed_texts([c["content"] for c in chunks])

    upsert_items = []
    for chunk, vec in zip(chunks, vectors):
        upsert_items.append({
            "vector_id": chunk["chunk_id"],
            "embedding": vec,
            "metadata": {
                "doc_id": chunk["doc_id"],
                "chunk_id": chunk["chunk_id"],
                "file_name": chunk["meta"]["file_name"],
                "chunk_index": chunk["meta"]["chunk_index"]
            }
        })

    vdb.upsert_vectors(upsert_items)
```

# 7. 开发与交付规范（补全闭环）

## 7.1 每个模块交付物清单（必须）

* core/base.py（ABC抽象接口）
* core/impl.py（默认实现）
* tests/test_impl.py（核心测试）
* README.md（面向初学者）
* requirements.txt（固定版本）

若模块使用 model / utils / tools / adapters / storage / prompt / examples 等扩展目录，
则对应目录也必须纳入测试覆盖范围，并在 README 中说明职责。

## 7.2 可替换性约束（强制）

* 上层模块只能依赖下层模块的 抽象接口或统一输出结构
* 禁止跨模块直接引用对方的 impl.py 内部私有方法
* 替换向量库/Embedding/LLM时，上层不改代码（只改注入与配置）

# 8. 统一请求格式（系统对外唯一标准，修订）

```json
{
  "type": "rag|agent|hybrid",
  "query": "string|null",
  "task": "string|null",
  "session_id": "string|null",
  "top_k": 5,
  "trace_id": "string|null",
  "extra_params": {}
}
```

规则：

- rag：query 必填
- agent/hybrid：task 必填
- session_id：rag 可选，agent/hybrid 推荐传入；若未传，由接口层补齐
- trace_id：由应用层入口生成并透传
- extra_params：用于扩展参数透传，不允许各层自定义改名

### 8.x 配套实现(v2.0)

| 概念 | 实现位置 |
|---|---|
| 请求/响应信封 Pydantic schema | `basic_support/schema_module/schema.py` — `RequestEnvelope` / `ResponseEnvelope` |
| 边界强校验 | `interface/request_response_module` 调用 `validate_request_dict()` |
| session_id 单层补齐 | `RequestHandler._standardize_request`,SimpleAgent 收到时若仍缺失会记 `[contract violation]` ERROR 而非静默兜底 |
| trace_id 单层生成 | `ApiService` middleware / `ConsoleApp.run_once`,下游一律只透传不再生成 |

# 9. 安全性设计

## 9.1 认证与授权

系统对外API需进行身份认证，防止未授权调用。API服务模块支持以下认证方式（可配置）：

- API Key：请求头携带 X-API-Key，服务端校验Key有效性。
- JWT：适用于用户会话场景，Token中携带用户标识与有效期。
- 内部服务调用：可通过白名单IP或服务网格mTLS进行认证。
  配置示例（config.yaml）：

```yaml
security:
  auth_enabled: true
  auth_type: "apikey"  # apikey / jwt / none
  api_keys:
    - "key1"
    - "key2"
  jwt_secret: "${JWT_SECRET}"  # 从环境变量读取
```

## 9.2 敏感信息保护

- 凭证管理：所有敏感信息（如API Key、数据库密码）禁止明文写在配置文件中，必须通过环境变量或密钥管理服务（如Hashicorp
  Vault）注入。配置管理模块需支持从环境变量覆盖配置值（详见附录B）。
- 传输加密：API服务必须启用HTTPS（生产环境），内部模块间通信建议使用TLS。
- 数据存储加密：文档存储、状态存储的落盘文件建议启用文件系统加密或应用层加密；向量数据库如使用云服务，应启用静态加密。

## 9.3 输入校验与防注入

- API层输入校验：请求响应处理模块需对用户输入进行严格校验，防止XSS、SQL注入等攻击。特别地，Agent工具中的calculator_tool禁止直接使用eval，应改用安全表达式解析库（如asteval）或仅支持预定义运算。
- 输出过滤：RAG生成的答案可能包含敏感内容，需集成敏感词过滤模块（可选）。

## 9.4 审计日志

关键操作（如文档上传、删除、Agent执行）需记录审计日志，包括操作人（若有）、时间、操作内容、结果。日志模块应支持将审计日志输出到独立文件或外部系统（如Splunk）。

# 10. 错误码表（统一标准）

## 10.1 目标与原则

为保证 RAG / Agent / Hybrid 各类接口在成功返回、参数校验、依赖异常、超时与限流等场景下行为一致，本文定义统一的 HTTP
返回规范与业务错误码体系，满足以下目标：

- 一致性：不同模块（解析、向量库、RAG、Agent、评测）返回结构统一，便于调用方处理。

- 可观测性：所有 5xx / 504 等服务端错误必须返回 trace_id，便于日志与链路追踪定位。

- 可恢复性：通过 retryable 指示调用方是否应重试，并建议采用指数退避。

- 可演进性：details 字段承载结构化信息，方便前端/调用方做更好的提示与引导。

## 10.2 统一响应结构（Response Envelope）

- 所有成功响应与失败响应都必须返回 trace_id
- trace_id 由应用层入口生成（API 中间件 / Console 入口）
- 下游模块只能透传，不得重新生成新的 trace_id
- details 字段必须使用结构化模板，不允许随意拼接非结构化错误文本

HTTP 状态码只由应用层负责映射；
业务错误码由接口层及下游模块返回；
接口层不直接决定 HTTP 状态码。

## 10.3 HTTP 状态码与业务码映射规则

- 2xx：成功（SUCCESS）

- 4xx：调用方错误（参数缺失/不合法、类型不支持、未认证、无权限、资源不存在、媒体类型不支持）

- 5xx：服务端内部错误（配置缺失、依赖缺失、向量库异常、RAG/Agent 执行异常、评测回退等）

- 504：执行超时（Agent/RAG 长链路超时）

- 429：触发限流（建议返回 Retry-After 响应头）

注意：业务码用于精确定位问题，HTTP 状态码用于表达大类错误语义；两者需保持一致。

## 10.4 错误码表（工程化版本）

### 10.4.1 错误码命名规范（强制）

- 全大写 + 下划线：MODULE_ACTION_REASON

- 稳定不随文案变更；message 可调整、code 不可随意改

- 分段前缀：

    - CONFIG_...

    - VECTOR_...

    - DOCUMENT_...

    - RAG_...

    - AGENT_...

    - API_...

    - AUTH_...

    - EVAL_...

字段含义：

- severity：info / warn / error

- retryable：是否建议重试

- trace：是否要求返回 trace_id（建议所有响应都返回；表中 “是” 表示强制要求）

| 分类    | code                        | HTTP | message示例               | severity | retryable | 触发场景                    | 调用方处理建议（user_action） | 服务端处理建议（ops_action）        | trace |
|-------|-----------------------------|-----:|-------------------------|----------|-----------|-------------------------|----------------------|----------------------------|-------|
| 成功    | SUCCESS                     |  200 | ok                      | info     | -         | 成功返回                    | -                    | -                          | -     |
| 请求参数  | PARAM_MISSING               |  400 | 缺少必填参数：xxx              | warn     | 否         | 入参缺字段                   | 补齐字段；参考接口示例          | 校验请求体；返回字段示例/文档锚点          | 否     |
| 请求参数  | PARAM_INVALID               |  400 | 参数不合法：xxx               | warn     | 否         | 类型/范围不对                 | 修正类型/范围              | 返回期望类型/范围；补充示例             | 否     |
| 请求类型  | BAD_REQUEST                 |  400 | 不支持的type：xxx            | warn     | 否         | type 非 rag/agent/hybrid | 改为 rag/agent/hybrid  | 枚举硬校验；OpenAPI枚举约束          | 否     |
| 文档解析  | DOCUMENT_NOT_FOUND          |  404 | 文档文件不存在                 | warn     | 否         | 解析路径不存在                 | 确认上传/路径              | 校验落盘产物；打印路径与请求ID           | 是     |
| 文档解析  | FOLDER_NOT_FOUND            |  404 | 文件夹不存在                  | warn     | 否         | parse_folder路径不存在       | 修正目录参数               | 启动自检目录；给出可用目录提示            | 是     |
| 文档解析  | UNSUPPORTED_FILE_TYPE       |  415 | 不支持的文件类型                | warn     | 否         | ext 不在白名单               | 更换支持格式/转格式           | 扩展解析器；白名单配置化               | 否     |
| 文档解析  | DOCUMENT_PARSE_FAILED       |  500 | 文档解析失败                  | error    | 是         | 解析异常                    | 稍后重试/更换文件            | 记录异常栈；定位解析器；必要时降级          | 是     |
| 配置    | CONFIG_NOT_FOUND            |  500 | 配置文件不存在                 | error    | 否         | 配置文件缺失                  | -                    | 检查挂载/路径；启动前自检；探针阻断         | 是     |
| 配置    | CONFIG_KEY_MISSING          |  500 | 配置缺失：xxx                | error    | 否         | 必要键缺失                   | -                    | 补配置或默认值；配置schema校验         | 是     |
| 向量库   | FAISS_NOT_INSTALLED         |  500 | 未安装faiss                | error    | 否         | 缺依赖                     | -                    | requirements增加；镜像构建校验；启动自检 | 是     |
| 向量库   | VECTOR_UPSERT_FAILED        |  500 | 向量写入失败                  | error    | 是         | upsert异常                | 稍后重试                 | 重试/队列化；失败降级；记录批大小/维度       | 是     |
| 向量库   | VECTOR_QUERY_FAILED         |  500 | 向量检索失败                  | error    | 是         | query异常                 | 稍后重试                 | 重试/报警；熔断到关键词检索；监控延迟        | 是     |
| 向量库   | VECTOR_DELETE_NOT_SUPPORTED |  501 | 不支持删除                   | warn     | 否         | FAISS示例不支持delete        | -                    | 更换可删索引/外部库；能力表声明           | 否     |
| RAG   | EMBEDDING_CONFIG_MISSING    |  500 | embedding.model_name未配置 | error    | 否         | 缺配置                     | -                    | 补配置；启动前schema校验            | 是     |
| RAG   | EMBEDDING_INIT_FAILED       |  500 | Embedding初始化失败          | error    | 是         | 模型加载失败                  | 稍后重试                 | 检查依赖/模型服务；fallback到备用模型    | 是     |
| RAG   | RAG_RUN_FAILED              |  500 | RAG执行失败                 | error    | 是         | run流程异常                 | 稍后重试/缩小输入            | 记录上下文（topk、prompt版本）；降级策略  | 是     |
| Agent | TOOL_NOT_FOUND              |  400 | 工具不存在：xxx               | warn     | 否         | 工具未注册                   | 修正tool名称             | 检查tools注册；启动时校验工具清单        | 否     |
| Agent | TOOL_CALL_FAILED            |  500 | 工具调用失败                  | error    | 是         | 工具连续失败                  | 稍后重试                 | 重试/熔断；隔离故障工具；记录I/O摘要       | 是     |
| Agent | AGENT_TIMEOUT               |  504 | Agent执行超时               | error    | 是         | 超过timeout               | 重试；拆分任务              | 调整timeout；拆分链路；阶段性超时与降级    | 是     |
| API   | API_RATE_LIMITED            |  429 | 请求过于频繁                  | warn     | 是         | 限流触发                    | 指数退避重试；降低并发          | 返回Retry-After；监控QPS；检查限流策略 | 否     |
| 安全    | AUTH_REQUIRED               |  401 | 未认证                     | warn     | 否         | 需要token                 | 携带token/登录           | 接入鉴权中间件；明确token获取方式        | 否     |
| 安全    | AUTH_FORBIDDEN              |  403 | 无权限                     | warn     | 否         | 资源隔离                    | 申请权限/切换租户            | RBAC/ACL校验；审计日志            | 是     |
| 评测    | EVAL_DATA_INVALID           |  400 | 评测集格式错误                 | warn     | 否         | 输入不合法                   | 修复数据格式               | 增加schema校验；提供模板与示例         | 否     |
| 评测    | EVAL_REGRESSION             |  500 | 指标回退                    | error    | 否         | CI阈值不满足                 | -                    | 生成对比报告；排查变更；必要时回滚          | 是     |

## 10.5 details 结构建议（按错误类型给模板）

### 10.5.1 参数缺失 / 不合法

```json
{
  "field": "top_k",
  "expected": "integer (1~50)",
  "actual": "string",
  "example": 10
}
```

### 10.5.2 不支持的 type

```json
{
  "field": "type",
  "allowed": [
    "rag",
    "agent",
    "hybrid"
  ],
  "example": "rag"
}
```

### 10.5.3 文档/路径不存在

```json
{
  "path": "/data/uploads/xxx.pdf",
  "hint": "请确认文件已上传并完成落盘"
}
```

### 10.5.4 向量库异常（便于排障）

```json
{
  "index": "faiss_default",
  "operation": "query",
  "top_k": 10,
  "embedding_dim": 1024
}
```

### 10.5.5 超时（便于定位阶段）

```json
{
  "timeout_ms": 60000,
  "stage": "tool_call",
  "hint": "建议拆分任务或降低单次输入规模"
}
```

## 10.6 重试与降级策略（建议写进实现与SOP）

- retryable=true 的错误（如 VECTOR_QUERY_FAILED、TOOL_CALL_FAILED、AGENT_TIMEOUT、API_RATE_LIMITED）：

    - 调用方建议 指数退避（例如 200ms → 400ms → 800ms …），并设置最大重试次数（例如 3 次）

    - 服务端建议：

        - 对外部依赖（向量库/工具）启用 熔断 与 隔离

        - 在可接受范围内启用 降级（如向量检索失败时降级为关键词检索/无检索回答）

    - retryable=false 的错误（如 PARAM_MISSING、AUTH_REQUIRED、CONFIG_KEY_MISSING）：

        - 调用方不应重试，应修正请求或配置

## 10.7 日志与追踪要求补充说明

- trace_id 写日志 
- trace_id 贯穿应用层→接口层→业务层→数据层 
- 成功/失败都要记录关键日志字段

# 11. Chunking 规范（索引构建与引用的统一前提）

本章节定义“文本切分（Chunking）”的统一规则。所有索引、引用、评测都依赖该规范；否则会出现“向量命中但无法定位原文/无法稳定引用”的问题。
说明：
第 11、12 章的 Chunking 与引用规范优先级高于第 6 章中的旧示例。
若旧示例与本章节冲突，以本章节为准。

## 11.1 Chunk 的标准结构（强制）

切分后的每个 chunk 必须保存以下字段（无论存入向量库还是评测导出）：

```json
{
  "doc_id": "与DocumentStore一致的doc_id",
  "chunk_id": "doc_id#c000123",
  "content": "chunk文本",
  "meta": {
    "file_name": "原文件名",
    "source": "local|s3|oss|wiki|...",
    "chunk_index": 123,
    "start_char": 4500,
    "end_char": 5120,
    "token_count_est": 420,
    "created_at": "2026-02-27T12:00:00Z"
  }
}
```

- chunk_id：必须稳定可复现；推荐格式 "{doc_id}#{chunk_index:06d}"

- start_char/end_char：用于精确定位与引用（即使内容更新，也便于排查差异）

- token_count_est：可用简单估算（字符/4）或 tokenizer（生产建议 tokenizer）

## 11.2 默认切分参数（推荐默认值）

为了兼顾检索召回与生成上下文长度，给出“默认值 + 可配置项”：

- chunk_size_tokens：400（推荐范围 300–600）

- chunk_overlap_tokens：80（推荐 60–120）

- max_chunk_size_tokens：800（硬上限，避免异常超长段落）

- min_chunk_size_tokens：80（过短 chunk 会造成噪声与误召回）

这些参数应加入全局配置（config/config.yaml）并允许不同数据域覆盖（例如代码类文档可增大 chunk）。

## 11.3 切分策略（按类型分流）

## 11.3.1 通用文本（默认）

1. 预处理：清洗（可复用 common_utils.text_clean）
2. 优先按“自然边界”切：
    - 先按标题/小节（Markdown #/##/###）
    - 再按段落空行
    - 再按句号/分号/换行（中英文均支持）
3. 若仍超 max_chunk_size_tokens：
    - 退化为滑动窗口（window=chunk_size, overlap=chunk_overlap）

### 11.3.2 表格/列表密集文本

- 尽量保持表格完整性：以表格块为单位切分

- 若表格过大：按行分块，但必须在每块 meta 中写明 table_id、row_range

### 11.3.3 代码/日志类文本（可选增强）

- 按函数/类/文件块切分（避免把一个函数切碎）

- 对超长文件：按“函数块 + 滑窗兜底”

## 11.4 Chunk 与向量写入的 metadata 约束（强制）

向量库写入的 metadata 至少包含：

```json
{
  "doc_id": "...",
  "chunk_id": "...",
  "file_name": "...",
  "chunk_index": 123
}
```

索引构建示例里 metadata 只写了 doc_id/file_name ,从“可引用/可评测”角度，必须补齐
chunk_id/chunk_index，否则后续无法做到稳定引用与精确召回定位。

## 11.5 向量ID策略（推荐）

- 推荐：vector_id = chunk_id

- 不推荐：vector_id = doc_id（一个 doc 多 chunk 会冲突或覆盖）

## 11.x 配套实现(v2.0)

- 实现位置:`data_layer/chunker_module/chunker.py`(从 `run/chunking_utils.py` 抽出)
- 公开入口:`chunk_document(doc_id, content, file_name, source, chunk_size_tokens, ...)`
- 守护测试:`data_layer/chunker_module/tests/test_chunker.py:test_all_chunks_validate_against_schema`
  把每个产出 chunk 用 `schema_module.Chunk.model_validate` 严格校验,防止字段漂移
- chunk_id 格式严格 `{doc_id}#c{6 位零填充}`,由 `chunker.py` 中
  `f"{doc_id}#c{chunk_index:06d}"` 直接构造
- 设计偏离声明见 `data_layer/chunker_module/README.md`

# 12. 检索链路规范（rewrite / retrieve / rerank / 引用格式）

本章节定义 RAG 检索链路的标准流水线，适用于：

- RAG 直接问答 type=rag

- Agent 内部工具 rag_search（协同）

## 12.1 标准链路总览（强制）

1. Query Normalize（清洗/截断/去噪）
2. Query Rewrite（可选，但推荐默认开启）
3. Retrieve（向量召回 TopK1）
4. Rerank（可选，但推荐默认开启）
5. Context Assemble（上下文拼装 + 去重 + 长度控制）
6. Generate（LLM 生成）
7. Cite（引用生成：把答案中的关键结论绑定到 chunk）

## 12.2 Rewrite 规范（Query 改写）

## 12.2.1 触发条件（推荐）

- 用户 query 太短（< 6 字）或过长（> 256 字）

- 用户包含代词/省略（“它/这个/上面那个”）

- 多轮对话：需要融合历史（若接入会话记忆）

## 12.2.2 输出格式（强制）

Rewrite 必须输出结构：

```json
{
  "rewrite_query": "用于检索的改写问题",
  "keywords": [
    "可选：关键实体/名词"
  ],
  "filters": {
    "doc_id": "...可选...",
    "source": "...可选..."
  }
}
```

- rewrite_query 必须可直接送入 embedding

- filters 用于向量库 filter

## 12.3 Retrieve 规范（向量召回）

- top_k_retrieve：默认 50（召回阶段要宽）

- 返回结构必须包含 chunk_id/doc_id/score/metadata（见 10.4）

## 12.4 Rerank 规范（重排）

### 12.4.1 输入

- query：rewrite_query

- candidates：召回结果对应的 chunk 内容列表

### 12.4.2 输出

- top_k_rerank：默认 8（进入上下文拼装的候选）

产出结构：

```json
[
  {
    "chunk_id": "...",
    "doc_id": "...",
    "score": 0.87,
    "rank": 1
  },
  ...
]
```

### 12.4.3 去重规则（强制）

- chunk_id 去重

- 对同一 doc 的多个 chunk：

- 若相邻 chunk 均入选，可合并为一个更长片段（但必须保留引用映射）

## 12.5 Context Assemble（上下文拼装与长度控制）

- 目标：拼装成模型可接受的 Prompt Context

- 默认策略：

    - 先按 rank 依次加入

    - 达到 max_context_tokens（例如 3000）就停止

    - 每个 chunk 最大截断 max_chunk_in_prompt_tokens（例如 600）

## 12.6 引用格式规范（强制）

为了实现“答案可追溯”，统一引用采用 chunk 级引用：

### 12.6.1 Answer 中的引用标记

- 文内引用：[CIT:chunk_id]

- 多个引用：[CIT:chunkA,chunkB]

  示例：

    - 本系统采用分层架构+模块化设计……[CIT:doc123#c000010]

### 12.6.2 响应结构中的引用字段（强制）

在 data 中追加 citations 字段：

```json
{
  "answer": "......[CIT:doc123#c000010]",
  "citations": [
    {
      "chunk_id": "doc123#c000010",
      "doc_id": "doc123",
      "file_name": "xxx.md",
      "start_char": 1200,
      "end_char": 1680,
      "score": 0.87
    }
  ]
}
```

- answer 可以保留引用标记（前端可渲染为脚注）

- citations 用于机器可读与 UI 展示

## 12.x 配套实现(v2.0)

| 检索链路步骤 | 实现位置 | 启用方式 |
|---|---|---|
| Query Normalize | `SimpleRAG._normalize_query` | 默认启用 |
| Query Rewrite (§12.2) | `business/rag_module/extensions/rewriter.py` — `LLMQueryRewriter` | `rag.enable_rewrite: true` |
| Retrieve (§12.3) | `SimpleRAG._embed_query` + `vector_db.query(query_vector, top_k, filters)` | 默认启用 |
| Rerank (§12.4) | `business/rag_module/extensions/reranker.py` — `LLMReranker` | `rag.enable_rerank: true` |
| Context Assemble | `SimpleRAG._assemble_context` (token 上限 `rag.max_context_tokens`) | 默认启用 |
| Generate | `SimpleRAG._call_llm_generate` 经 `llm_compat.call_llm_compat` | 默认启用 |
| Citation (§12.5) | `SimpleRAG._build_citations` + `_ensure_citation_marker` | 默认启用 |

**降级策略**(LLM 不可用时):
- `LLMQueryRewriter.rewrite` 失败 → 退化为原 query
- `LLMReranker.rerank` 失败 → 退化为原顺序截断
- 这些降级有专门单测守护(`business/rag_module/extensions/tests/test_extensions.py`)

# 13. API 规范（对外接口与内部管理接口）

## 13.1 通用约定

- Content-Type：application/json

- 所有响应统一：code/message/data

- 建议所有请求支持请求头：

    - X-Request-Id（可选，调用方传入）

    - Authorization: Bearer <token>（若启用鉴权）

## 13.2 POST /invoke（统一业务入口）

- 说明：对外唯一业务入口，支持 rag / agent / hybrid

- 请求体：沿用第 8 章请求格式

- 响应：与各模块 run/execute 输出一致（成功 SUCCESS，失败见第 10 章）

- /invoke 的业务参数校验由 request_response_module 负责

- API 服务模块只负责 HTTP 协议层校验：鉴权、请求体大小、JSON 解析、上传格式、路由分发

- /documents/upload 仅负责上传与落盘，不等同于索引完成

- 若启用自动索引，必须通过 index_service 或统一索引任务入口完成，不得在 API 层直接编排 parser/embedding/vector_db 逻辑

### 13.2.1 示例

RAG

```bash
curl -X POST /invoke -H "Content-Type: application/json" -d '{"type":"rag","query":"xxx","top_k":5}'
```

Agent

```bash
curl -X POST /invoke -H "Content-Type: application/json" -d '{"type":"agent","task":"xxx","session_id":"s001"}'
```

## 13.3 POST /index/build（索引构建）

说明：触发离线索引构建（可同步/异步）

请求体：

```json
{
  "source_type": "local_folder",
  "source_path": "data_docs",
  "chunking": {
    "chunk_size_tokens": 400,
    "chunk_overlap_tokens": 80
  }
}
```

成功响应：

```json
{
  "code": "SUCCESS",
  "message": "index build started",
  "data": {
    "job_id": "job_20260227_0001"
  }
}
```

错误码：

- FOLDER_NOT_FOUND

- DOCUMENT_PARSE_FAILED

- VECTOR_UPSERT_FAILED

## 13.4 GET /index/job/{job_id}（索引任务查询）

- 返回任务状态：PENDING/RUNNING/SUCCESS/FAILED

- 失败时返回 error_code/error_message

## 13.5 POST /documents/upload（可选：文档上传）

- 说明：若系统需要对接前端上传，提供该接口；否则可由外部完成落盘再调用 /index/build

- 请求：multipart/form-data

    - file: 上传文件

    - source: 可选来源标记

- 响应：

```json
{
  "code": "SUCCESS",
  "message": "uploaded",
  "data": {
    "file_name": "xxx.pdf",
    "stored_path": "uploads/xxx.pdf"
  }
}
```

## 13.6 GET /healthz（健康检查）

用于 k8s / LB 健康检查

返回：

```json
{
  "code": "SUCCESS",
  "message": "ok",
  "data": {
    "status": "UP"
  }
}
```

## 13.7 GET /metrics（可观测性：Prometheus）

返回 Prometheus 格式指标（见第 15 章）

## 13.8 POST /eval/run（评测触发）

说明：触发离线评测（CI 也可调用）

请求体：

```json
{
  "suite": "rag_basic",
  "dataset_path": "eval_sets/rag_basic.jsonl",
  "max_cases": 200
}
```

# 14. 部署与运维指南

## 14.1 环境准备

- 开发语言：Python
- 运行版本：最低 3.10，推荐 3.12

依赖安装：各模块根目录的requirements.txt已列出依赖，可使用以下命令统一安装：

```
pip install -r requirements.txt
```

建议使用虚拟环境（venv或conda）。

## 14.2 容器化部署

提供Dockerfile示例（置于项目根目录）：

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY . .
RUN pip install --no-cache-dir -r requirements.txt
CMD ["uvicorn", "api_service_module.core.impl:app", "--host", "0.0.0.0", "--port", "8000"]
```

并提供docker-compose.yml用于本地快速启动（包含向量库、存储卷等）：

```yaml
version: '3'
services:
  api:
    build: .
    ports:
      - "8000:8000"
    environment:
      - CONFIG_PATH=/app/config/config.yaml
    volumes:
      - ./documents:/app/documents
      - ./vector_store:/app/vector_store
```

## 14.3 外部服务依赖

系统依赖以下外部服务，需提前准备：

- 向量数据库（若使用Pinecone/Chroma/Milvus）：提供连接地址与凭证。

- LLM API（如OpenAI、本地部署模型）：提供API endpoint与Key。

- 对象存储（可选）：若文档存储需扩展，可对接S3/MinIO。

## 14.4 配置分离

支持通过环境变量覆盖配置文件中的值，规则：${ENV_VAR}或${ENV_VAR:default}。配置管理模块需实现此功能（修改get_config方法）。

## 14.5 健康检查

API服务模块需添加/health端点，返回服务状态及依赖组件状态（如向量库连通性）。示例：

```python
@app.get("/health")
def health():
    return {"status": "ok", "dependencies": {"vector_db": "up", "llm": "up"}}
```

# 15. 性能与容量规划

## 15.1 性能指标（SLO）

| 指标               | 	目标值      | 	说明              |
|------------------|-----------|------------------|
| RAG单次请求延迟（p95）   | < 2s      | 	含检索+生成，受LLM影响较大 |
| Agent单次任务延迟（p95） | 	< 5s	    | 根据步骤数浮动          |
| 并发请求数	           | ≥ 100QPS  | 需水平扩展            |
| 索引构建吞吐           | 	> 10MB/s | 	文档解析+向量化        |

## 15.2 容量估算

- 向量存储：每条向量约 维度 * 4字节 + metadata。若100万文档，维度768，FAISS索引内存约 100w * 768 * 4 ≈
  3GB，加上metadata需预留额外空间。

- 文档存储：按平均文档大小估算，例如1GB原始文档，存储为文本后约需2GB（含备份）。

- 状态存储：每个会话状态大小约几KB，按并发会话数估算。

## 15.3 扩展性设计

- 向量库：若使用FAISS本地模式，单机内存有限，可考虑分片或改用分布式向量库（Milvus、Pinecone）。

- 文档存储：可对接对象存储（如S3）以支持海量文档。

- Agent任务队列：高并发时，Agent执行可能阻塞，建议引入异步任务队列（Celery）和结果缓存。

## 15.4 指标 Metrics（Prometheus 推荐）

至少暴露以下指标（/metrics）：

- 请求层：

    - http_requests_total{path,method,code}

    - http_request_duration_seconds_bucket{path,method}

- RAG：

    - rag_retrieve_latency_seconds

    - rag_rerank_latency_seconds

    - rag_generate_latency_seconds

    - rag_context_tokens

- Agent：

    - agent_steps_total

    - agent_tool_calls_total{tool,code}

    - agent_timeout_total

# 16. 监控与告警

## 16.1 关键指标采集

- 业务指标：RAG请求量、成功率、平均延迟；Agent任务完成数、工具调用成功率。

- 系统指标：CPU、内存、磁盘、网络IO。

- 依赖指标：向量库查询延迟、LLM API调用延迟与错误率。

## 16.2 日志聚合

日志模块需支持将日志输出到JSON格式，便于采集。可集成Filebeat + Elasticsearch或Loki。

审计日志单独输出到audit.log文件。

## 16.3 告警规则示例

- RAG错误率 > 5% 持续5分钟 → 告警

- 向量库查询延迟 > 1s 持续10分钟 → 告警

- LLM API调用失败率 > 10% → 告警

## 16.4 健康检查与探针

API服务需提供就绪探针（/ready）和存活探针（/live），用于容器编排。

# 17. 数据隐私与合规

## 17.1 数据生命周期管理

- 文档数据：用户上传的文档应在指定时间后自动删除（如30天），支持通过API手动删除。

- 会话状态：Agent会话状态可配置过期时间（如24小时），过期后自动清理。

- 向量数据：删除文档时需同步删除对应向量（通过vector_db的delete接口实现）。

说明：

- 本系统允许存在“示例实现”和“生产实现”两级能力边界。
- 示例实现（如本地 Faiss Flat）可不支持 delete，并返回 VECTOR_DELETE_NOT_SUPPORTED。
- 生产实现必须支持按 vector_id 或 doc_id 级别删除，满足数据生命周期管理要求。

## 17.2 敏感内容过滤

可在RAG生成后或Agent输出前，集成敏感词过滤模块，对答案进行脱敏或拦截。

## 17.3 GDPR合规

支持用户“被遗忘权”：提供接口删除与特定用户相关的所有数据（文档、会话、向量）。

数据跨境：若使用海外LLM服务，需告知用户并获取同意。


# 附录A 当前代码仓结构映射（v1.1）
- basic_support/ → 基础支撑层
- data_layer/ → 数据层
- business/ → 核心业务层
- interface/ → 接口层
- application/ → 应用层
- doc/ → 总设计与子设计文档目录

# 附录B 设计文档冲突处理规则
1. 总设计文档 > 子模块设计文档 > README > 代码注释
2. 若总设计中的旧示例与新规范冲突，以规范章节为准
3. 若代码实现与文档不一致，以修订后的总设计为准并同步修正文档
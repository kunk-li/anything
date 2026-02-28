# 数据层-文档存储模块（document_store_module）设计说明书

# 1. 文档概述

## 1.1 文档目的

本文档为RAG与Agent系统数据层文档解析模块的专项设计说明书，遵循系统整体架构设计规范，明确模块功能、项目结构、接口定义、依赖关系及开发要求，用于指导开发人员（含初学者）进行该模块的独立开发、测试与集成，确保模块与系统整体兼容、可扩展。

## 1.2 适用人群

本团队所有开发人员（含资深开发者与初学者）、测试人员，作为文档解析模块开发、测试、维护的唯一标准依据；项目管理人员可参考本说明书进行模块开发进度管控。

## 1.3 核心需求回顾

- 模块功能：负责将原始文件（txt、pdf、docx、md、py、excel、ppt/pptx、csv、json、xml）解析为统一的标准文本结构，不涉及数据存储，仅输出解析结果供文档存储模块进一步处理。

- 开发语言：遵循系统统一要求，采用Python 3.10+版本，确保与其他模块兼容性。

- 开发模式：独立开发、互不依赖，基于本说明书即可完成模块开发，开发完成后通过统一接口与其他模块集成。

- 文档要求：详细、易懂，适配初学者，明确模块所有可提前定义的内容（接口、数据格式、项目结构等）。

- 模块约束：需包含抽象基类（ABC），确保模块一致性；代码可与系统其他模块交换，数据格式符合系统统一标准。

## 1.4 术语定义

|术语|定义|
|---|---|
|文档解析|将不同格式的原始文件（txt、pdf、docx、md、py、excel、ppt/pptx、csv、json、xml）转换为系统统一的标准文本结构，完成基础文本清洗，不涉及存储操作。|
|标准文本结构|模块统一输出的解析结果格式，包含解析后的文本内容、原文件名及文件元数据。|
|ABC|抽象基类，定义模块的核心接口与方法，强制子类实现，保障模块一致性。|
|RAGException|系统统一定义的RAG相关异常类型，用于模块解析失败等场景的异常抛出与处理。|
# 2. 模块核心设计

## 2.1 模块功能

核心功能：接收文档解析模块输出的解析后文本及原始文件相关信息，生成唯一doc_id，**按传入的原始文件类型完成文本的持久化存储**（不再固定统一存储类型），实现按doc_id的读取、更新、删除全量操作；记录文档原始文件类型、存储类型及其他核心基本信息，**新增独立文件信息记录文件（与存储文件一一对应）**，实现文档基本信息的统一管理与快速读取；提供标准化接口供上层模块调用，保障数据存储的一致性与可追溯性；新增重复文件上传校验功能，避免无效重复存储，节省存储空间；新增僵尸文件识别与清理功能，及时释放无效占用空间，保障模块存储效率。

输入输出规范：

- 输入：解析后文本相关信息（content、file_name）、原始文件类型（file_type）及完整文档结构（含doc_id）；重复文件校验需额外传入文本哈希值（content_hash）；僵尸文件清理需传入清理阈值参数（threshold_days）；强制存储重复文件需传入force_save标识。

- 输出：读取操作返回统一文档结构（含doc_id、content、file_name、file_type、storage_type、create_time、update_time、file_size、content_hash、last_access_time、info_file_path）；新增/更新/删除操作返回布尔值标识操作结果；重复文件校验返回校验结果（是否重复、重复doc_id）；僵尸文件清理返回清理结果（清理文件数量、释放空间大小、成功/失败数量）；文件信息读取操作返回文档基本信息字典（无需读取文档内容，仅含基础信息项）。

## 2.1.1 文件类型规范

明确模块接收的原始文件类型、存储采用的文件类型（与原始类型严格一致），确保输入输出标准化，保障存储兼容性与可扩展性；同步规范文件信息记录文件的格式、命名及存储规则，确保与文档存储文件的关联性。

### 2.1.1.1 接收文件类型（支持类型）

本模块不直接接收原始文件，仅接收文档解析模块解析后的相关信息，其中需包含原始文件的类型标识（file_type），支持的原始文件类型如下（可根据系统整体需求灵活扩展）：

- 文档类：.pdf（便携式文档格式）、.doc/.docx（Word文档）、.xls/.xlsx（Excel表格）、.ppt/.pptx（PPT演示文稿）

- 文本类：.txt（纯文本文件）、.md（Markdown文档）、.rtf（富文本格式）

- 其他可解析类型：.csv（逗号分隔值文件）、.json（JSON格式文件）

说明：文档解析模块需对原始文件进行前置解析与格式校验，提取文本内容（content）、文件名（file_name）、原始文件类型（file_type，以文件后缀名标识，如“pdf”“docx”，统一小写），并将上述信息及文本哈希值（content_hash）传入本模块。本模块不负责原始文件的格式校验，若文档解析模块传入的file_type不在支持列表内，本模块将拒绝接收，记录错误日志并返回操作失败标识。

### 2.1.1.2 存储文件类型（与原始类型一致）

为保障存储文件格式的兼容性，便于后续文档复用、查看及追溯，本模块将解析后的文本**按文档解析模块传入的原始文件类型（file_type）进行存储**，不做统一格式转换，严格实现“传入什么类型，存入什么类型”，具体规则如下：

- 存储规则：存储文件类型与原始文件类型（file_type）完全一致，如传入的file_type为“pdf”，则存储为.pdf格式；传入“md”，则存储为.md格式，确保解析后文本的格式特性（如Markdown排版、富文本格式、表格结构）得以完整保留。

- 特殊处理：若原始文件类型为不可直接存储文本的格式（如.xlsx、.pptx），解析后文本仍按对应类型存储（如.xlsx格式存储解析后的表格文本，.pptx格式存储解析后的演示文稿文本），确保文件类型与原始文件一致，便于关联追溯；若原始文件类型为未知（unknown），默认按.txt格式存储，同时记录警告日志，标注异常原因。

存储文件命名规则：固定为“doc_id.原始后缀”，示例：doc_id为“123e4567-e89b-12d3-a456-426614174000”，传入的file_type为“pdf”，则存储文件名为“123e4567-e89b-12d3-a456-426614174000.pdf”；传入的file_type为“md”，则存储文件名为“123e4567-e89b-12d3-a456-426614174000.md”。存储文件统一放置在配置指定的存储目录下，不允许自定义存储路径，避免路径混乱及管理隐患。

## 2.1.2 文件基本信息记录要求及文件信息记录文件设计

为确保文档全生命周期可追溯、可管理，同时提升文档基本信息的读取效率（无需解析文档内容），本模块需完整记录每篇文档的核心基本信息，**新增独立的文件信息记录文件**，与文档内容存储文件一一对应，实现信息与内容的分离管理；所有基本信息需纳入标准化文档结构，同步写入文件信息记录文件，随文档一同完成存储、读取、更新、删除操作；同步补充重复文件校验、僵尸文件识别所需的关联信息，确保功能闭环。

### 2.1.2.1 文件基本信息记录要求

|信息项|说明|获取方式/要求|
|---|---|---|
|file_name|原始文件名称（含后缀）|由文档解析模块传入，不可修改；若传入为空或包含非法字符（如/、\、:、*等），本模块将自动生成默认文件名（格式：default_时间戳.原始后缀），并记录警告日志。|
|file_type|原始文件类型（后缀名，不含“.”）|由文档解析模块传入，如“pdf”“docx”，统一转为小写；若传入为空，默认标记为“unknown”，同步记录警告日志。|
|storage_type|本模块存储文件的类型（后缀名）|与file_type完全一致，不单独配置；若file_type为“unknown”，默认按“txt”存储，统一小写；已存储文件不随原始文件类型变更而转换格式。|
|doc_id|文档唯一标识|本模块通过专属工具函数生成，采用UUID4格式，确保唯一不可重复；生成后不可修改，删除文档后doc_id不再重复使用，同步记录doc_id生命周期日志。|
|create_time|文档首次存储时间|本模块自动生成，格式为“YYYY-MM-DD HH:MM:SS”，如“2026-02-28 15:30:00”，与系统当前时间保持一致，不可修改，作为文档创建的唯一时间标识。|
|update_time|文档最后更新时间|首次存储时与create_time一致，执行更新操作时自动更新为系统当前时间，格式同上；未执行更新操作时，保持初始值不变，同步更新至文件信息记录文件。|
|file_size|存储文件的大小（单位：字节）|本模块通过工具函数计算并记录，更新操作时同步更新；若存储文件异常（如损坏、丢失），记录为0字节，触发日志告警，便于运维排查。|
|content_hash|解析后文本内容的哈希值，用于重复文件校验|可由文档解析模块传入或本模块通过工具函数计算，采用MD5/SHA256算法（默认MD5）；若文档解析模块传入，需校验算法一致性，不一致则重新计算并覆盖，确保校验准确性。|
|last_access_time|文档最后被访问（读取）的时间，用于僵尸文件识别|本模块自动记录，执行读取操作时更新为系统当前时间，格式同create_time；首次存储时与create_time一致，未被访问时保持初始值不变，为僵尸文件识别提供依据。|
|info_file_path|文件信息记录文件的存储路径|本模块自动生成，与文档存储文件同目录，命名规则固定，不可修改，用于快速关联文档存储文件与信息记录文件，便于批量管理。|
### 2.1.2.2 文件信息记录文件详细设计

文件信息记录文件为独立JSON文件，与文档存储文件一一对应，专门用于存储上述所有文档基本信息，格式统一、可读性强，无需解析文档内容即可快速获取文档基础信息，提升信息读取效率，同时便于批量管理文档信息、追溯文档生命周期。

- 格式规范：固定采用JSON格式（UTF-8编码），结构清晰、便于程序解析和人工查看；JSON字段与文档基本信息项一一对应，字段名与标准化文档结构字段名完全一致，确保信息一致性和可维护性。

- 命名规则：固定为“doc_id.info.json”，与文档存储文件同名（前缀为doc_id），后缀固定为“info.json”，便于快速关联查找。示例：doc_id为“123e4567-e89b-12d3-a456-426614174000”，文档存储文件为“123e4567-e89b-12d3-a456-426614174000.pdf”，则文件信息记录文件名为“123e4567-e89b-12d3-a456-426614174000.info.json”。

- 存储路径：与对应文档存储文件放置在同一存储目录下，不单独设置目录，便于管理和关联；存储目录由配置文件指定，与文档存储目录保持一致，避免路径混乱。

- 读写规则：

    1. 新增文档：生成文档存储文件的同时，同步生成对应的文件信息记录文件，将所有基本信息完整写入JSON文件，确保信息与文档内容同步。

    2. 更新文档：更新文档内容及存储文件后，同步更新文件信息记录文件中的对应字段（如update_time、file_size、content_hash、last_access_time），确保信息与内容一致，更新后留存操作日志。

    3. 删除文档：删除文档存储文件的同时，必须删除对应的文件信息记录文件，避免无效信息残留；若删除失败，记录错误日志并触发告警，便于及时排查问题。

    4. 读取信息：支持通过doc_id快速读取文件信息记录文件，直接解析JSON获取文档基本信息，无需读取文档存储文件内容，大幅提升读取效率；若信息记录文件丢失或损坏，尝试从文档存储文件中提取基础信息（如file_type），同时记录警告日志，并重新生成信息记录文件，确保信息可追溯。

- 示例（JSON格式）：
        `{
  "doc_id": "123e4567-e89b-12d3-a456-426614174000",
  "content": "解析后的文档文本内容...",
  "file_name": "测试文档.pdf",
  "file_type": "pdf",
  "storage_type": "pdf",
  "create_time": "2026-02-28 15:30:00",
  "update_time": "2026-02-28 15:30:00",
  "file_size": "10240",
  "content_hash": "e10adc3949ba59abbe56e057f20f883e",
  "last_access_time": "2026-02-28 15:30:00",
  "info_file_path": "documents/123e4567-e89b-12d3-a456-426614174000.info.json"
}`

- 异常处理：若文件信息记录文件写入失败，文档存储操作同步终止，返回操作失败标识并记录错误日志；若读取时发现信息记录文件损坏，尝试修复（如JSON格式修复），修复失败则重新生成，确保信息不丢失、可追溯。

## 2.1.3 重复文件上传处理

核心目标：避免相同文档重复存储，节省存储空间，同时确保文档唯一性与可追溯性；采用“哈希校验+关键信息比对”双重校验机制，兼顾校验准确性与效率；同步校验文件信息记录文件，避免重复生成无效信息文件，确保数据一致性。

### 2.1.3.1 校验规则

- 优先校验：解析后文本内容的哈希值（content_hash），若哈希值已存在于模块存储的“哈希-doc_id”关联表中，直接判定为重复文件。哈希值校验优先级最高，可快速排除绝大多数重复场景，提升校验效率。

- 补充校验：若哈希值未匹配，进一步比对原始文件名（file_name）、原始文件类型（file_type）、解析后文本长度（精确到字节），三者完全一致则判定为重复文件，避免哈希碰撞导致的误判，确保校验准确性。

- 特殊说明：允许“文件名不同但内容完全一致”的重复场景（按重复文件处理）；允许“内容相似但不完全一致”的非重复场景（正常存储，生成新doc_id）。若用户需强制存储重复文件，需在上层模块传入force_save=True标识，本模块将跳过所有校验，生成新doc_id并存储，同步生成对应的文件信息记录文件，记录警告日志标注强制存储操作。

### 2.1.3.2 处理逻辑

1. 接收文档解析模块传入的解析后文本、file_name、file_type、content_hash，同时接收上层模块传入的可选参数（force_save：是否强制存储，默认False）。

2. 查询模块内部维护的“哈希-doc_id”关联表，校验content_hash是否已存在；若force_save为True，直接跳过所有校验，进入文档创建、存储及文件信息记录文件生成流程。

3. 若content_hash已存在，返回重复校验结果（is_duplicate=True）及重复文档的doc_id，不执行存储操作，不生成文件信息记录文件，仅记录警告日志（包含重复文件的file_name、doc_id、content_hash）。

4. 若content_hash不存在，执行补充校验（比对file_name、file_type、文本长度），若三者均匹配，判定为重复，返回对应校验结果；若不匹配，正常生成doc_id、记录所有基本信息，执行文档存储操作，同步生成对应的文件信息记录文件，并更新“哈希-doc_id”关联表。

5. 维护“哈希-doc_id”关联表，新增文档时同步更新，删除文档时同步移除对应记录（避免关联表失效）；关联表采用内存+持久化双重存储方式，确保模块重启后校验功能正常，关联数据不丢失。

## 2.1.4 僵尸文件处理

核心目标：识别并清理无效存储文件及对应信息记录文件，释放存储空间，避免无效文件堆积影响存储效率；采用“主动识别+定时清理”结合的方式，确保清理准确性，避免误删有效文件；同步清理无效的文件信息记录文件，确保数据一致性。

### 2.1.4.1 僵尸文件识别规则

- 类型1：存储介质中存在的文档存储文件/文件信息记录文件，无对应doc_id记录（如手动删除doc_id关联表记录但未删除存储文件、存储异常导致关联关系丢失）。此类文件无法通过doc_id访问，属于无效文件，需清理。

- 类型2：有doc_id关联，但长期未被访问（last_access_time距当前时间超过配置阈值）且未被更新（update_time距当前时间超过配置阈值）的文档存储文件及对应文件信息记录文件。阈值可通过配置文件设置，默认30天。

- 类型3：doc_id已被标记为删除，但文档存储文件或文件信息记录文件未被实际删除的无效文件。此类文件因删除操作异常导致残留，需及时清理，避免占用存储空间。

- 类型4：文档存储文件与对应文件信息记录文件缺失其一（如仅存在存储文件，无信息记录文件；或仅存在信息记录文件，无存储文件），两类文件均判定为僵尸文件，需同步清理，确保数据一致性。

### 2.1.4.2 处理逻辑

1. 识别机制：提供手动触发识别、定时自动识别两种方式；定时识别周期可通过全局配置文件设置（默认每日凌晨执行一次），识别完成后生成识别报告，详细记录僵尸文件（含存储文件和信息记录文件）的路径、doc_id、文件大小、存在时长等信息，便于运维查看。

2. 清理机制：识别出僵尸文件后，先记录详细日志（包含文件路径、doc_id、文件大小、存在时长），再执行清理操作；支持配置“清理前备份”（可选，备份至指定目录，保留7天），避免误删有效文件。备份文件命名格式：“僵尸文件原名称_清理时间戳.后缀”；文档存储文件与对应信息记录文件需同步备份、同步清理，确保关联文件一致性。

3. 异常处理：清理过程中若遇到文件占用、权限不足等问题，记录错误日志，跳过该文件（含对应关联文件），继续清理其他僵尸文件，清理完成后返回清理结果（成功数量、失败数量、释放空间大小）；失败文件将纳入下次识别与清理范围，确保最终清理到位。

4. 防护机制：核心业务相关文档（可通过配置指定doc_id前缀）不参与僵尸文件清理，避免误删关键数据；配置项为“core_doc_prefix”，支持多个前缀，用逗号分隔；核心文档的存储文件与信息记录文件均不参与清理，同步标注核心标识。

## 2.2 核心设计原则

遵循系统整体设计原则，结合模块特性，重点遵循以下原则，确保模块可扩展、易维护、高可靠：

- 模块解耦：仅依赖基础支撑层通用能力（如配置管理、日志模块），不与其他模块产生强耦合，通过统一接口实现通信；重复文件、僵尸文件处理逻辑独立封装，不影响核心存取功能，可通过配置项单独启停；文件信息记录文件的读写逻辑独立，与文档存储逻辑解耦但同步联动，确保数据一致性。

- 接口统一：定义标准化抽象接口，具体实现可灵活替换（如本地存储、对象存储），上层模块无感知；新增重复文件校验、僵尸文件清理、文件信息读取相关接口，保持接口风格、参数与返回值格式统一，降低调用成本。

- 可扩展：支持后续替换存储方式（如从本地存储扩展为S3/MinIO对象存储），无需修改上层调用逻辑；支持扩展接收的原始文件类型，仅需修改配置即可适配；支持自定义重复校验规则、僵尸文件识别阈值；支持扩展文件信息记录文件的格式（需统一配置），提升模块适配性。

- 易维护：项目结构规范、接口清晰，适配初学者开发与维护；文件类型及信息记录规则统一，便于问题排查与日常管理；重复文件、僵尸文件、文件信息记录文件处理逻辑模块化设计，便于后续优化迭代；文件信息记录文件格式统一，便于批量管理和读取。

- 安全性：重复文件处理不覆盖已有有效文档，避免数据覆盖风险；僵尸文件清理前可配置备份，避免误删导致数据丢失；清理、删除等关键操作记录完整日志，便于追溯；核心配置需权限校验，防止误修改；文件信息记录文件与存储文件同步管理，避免信息泄露或无效残留。

# 3. 统一项目结构规范

严格遵循系统全局项目结构规范，模块根目录命名为「document_store_module」，目录结构如下（所有目录与文件必须存在，无相关内容可留空）；新增重复文件、僵尸文件处理相关工具函数与配置，新增文件信息记录文件读写相关工具函数，确保功能与结构对应：

```plain text
document_store_module/                  # 模块根目录（全小写，多单词用下划线连接）
├── __init__.py               # 模块初始化文件，暴露模块核心类/方法（不能为空）
├── core/                     # 核心逻辑目录（存放抽象基类与具体实现）
│   ├── __init__.py
│   ├── base.py               # 抽象基类（ABC）文件，定义模块核心接口（必须包含，新增重复、僵尸文件、文件信息读取相关接口）
│   └── impl.py               # 具体实现类文件，继承base.py中的抽象类（必须包含，新增对应逻辑）
├── utils/                    # 模块工具目录（存放模块专属工具函数，无则空目录）
│   ├── __init__.py
│   └── tool_functions.py     # 工具函数文件（新增哈希计算、重复校验、僵尸识别、文件信息记录文件读写辅助函数）
├── config/                   # 模块专属配置目录（无则空目录）
│   ├── __init__.py
│   └── config.py             # 配置文件（新增重复校验、僵尸清理、文件信息记录文件相关配置读取）
├── tests/                    # 测试目录（必须包含，覆盖核心功能）
│   ├── __init__.py
│   ├── test_base.py          # 抽象类测试用例（可选，初学者可简化）
│   └── test_impl.py          # 具体实现类测试用例（必须包含，新增重复、僵尸文件、文件信息记录文件相关测试）
└── README.md                 # 模块说明文档（必须包含，适配初学者，补充重复、僵尸文件、文件信息记录文件处理说明）
```

## 3.1 目录结构详细说明

### 3.1.1 根目录与__init__.py

document_store_module：模块根目录，名称固定不可修改，用于标识模块身份。根目录下所有文件与目录需严格遵循命名规范，不允许随意新增无关文件，确保目录整洁。

根目录__init__.py：必须包含，核心作用是将目录标识为Python模块，需暴露模块核心类/方法，新增重复文件、僵尸文件、文件信息读取相关类/方法暴露，确保上层模块可正常调用，示例如下：

```python
from .core.base import BaseDocumentStore
from .core.impl import LocalDocumentStore

__all__ = ["BaseDocumentStore", "LocalDocumentStore"]
```

### 3.1.2 core目录

核心逻辑存放目录，包含抽象基类与具体实现类，是模块的核心代码区，需新增文件类型处理、信息记录、重复文件校验、僵尸文件处理、文件信息记录文件读写相关逻辑，确保功能闭环。

- base.py：抽象基类（ABC），定义模块必须实现的核心接口，新增文件基本信息、重复文件校验、僵尸文件清理、文件信息读取相关参数与返回值定义，强制子类遵循，确保接口统一。不包含任何具体实现代码，仅定义接口规范，为后续扩展提供统一标准。

- impl.py：具体实现类，继承base.py中的抽象基类，实现所有抽象方法，新增存储类型（与原始类型一致）处理、文件基本信息记录与更新、重复文件校验、僵尸文件识别与清理、文件信息记录文件读写逻辑（保留基础框架，不体现具体实现细节）。可根据存储方式扩展多个实现类（如对象存储实现类），但需严格遵循抽象基类接口规范，确保接口统一。

### 3.1.3 utils目录

模块专属工具函数目录，存放不涉及核心业务逻辑、仅为模块提供辅助的工具函数，如doc_id生成、文件路径处理、文本编码转换、文件大小计算、时间生成、文件类型校验；新增哈希值计算、重复文件校验辅助、僵尸文件识别辅助、文件信息记录文件读写（JSON解析/生成）等函数，不依赖上层模块。工具函数需保证通用性，避免与核心逻辑耦合，可单独测试与复用，降低代码冗余。

### 3.1.4 config目录

模块专属配置目录，读取基础支撑层的全局配置，补充模块专属配置（如存储目录、支持的原始文件类型、重复校验算法、僵尸文件清理阈值、清理周期、文件信息记录文件格式/编码等），可通过配置管理模块加载配置。配置文件需提供合理默认值，确保模块在无额外配置时可正常启动，同时支持通过配置灵活调整功能参数。

### 3.1.5 tests目录

测试用例存放目录，必须包含test_impl.py，覆盖具体实现类的核心功能（新增、读取、更新、删除），同时新增文件类型适配（与原始类型一致）、文件基本信息记录、文件信息记录文件读写、重复文件校验、僵尸文件清理的测试用例；test_base.py可选，初学者可简化。测试用例需覆盖正常场景、异常场景（如文件不存在、权限不足、重复文件、僵尸文件识别错误、文件信息记录文件损坏等），确保模块稳定性和功能正确性。

### 3.1.6 README.md

模块说明文档，语言简洁易懂，适配初学者，需包含以下内容：模块功能、核心接口说明、使用示例、依赖项、常见问题、文件类型规范（传入与存储一致）、文件基本信息说明、文件信息记录文件设计（格式、命名、读写规则）、重复文件上传处理规则、僵尸文件清理规则。可补充开发注意事项、调试方法，帮助初学者快速上手，降低维护成本。

## 3.2 统一编码规范

严格遵循系统全局编码规范，重点注意以下几点，确保代码规范、可维护、易读：

- 编码格式：所有代码文件采用UTF-8编码，缩进采用4个空格（禁止使用Tab），每行代码长度不超过120字符，确保代码整洁易读。代码文件末尾需保留一个空行，确保格式规范；文件信息记录文件固定采用UTF-8编码，确保跨环境兼容性。

- 命名规范：类名采用大驼峰命名法（如BaseDocumentStore、LocalDocumentStore）；方法/函数名采用小驼峰命名法（如create_document、save_document、check_duplicate_file、clean_zombie_files、read_info_file、write_info_file）；变量名采用小驼峰命名法；常量名采用全大写，多单词用下划线连接（如SUPPORTED_FILE_TYPES、HASH_ALGORITHM、ZOMBIE_FILE_THRESHOLD、INFO_FILE_SUFFIX）。文件名全小写，多单词用下划线连接，与类名/函数名对应；文件信息记录文件后缀固定为“info.json”。

- 注释规范：类与方法需使用文档字符串（"""）详细说明功能、参数、返回值、异常情况；关键代码（尤其是文件类型处理、信息记录、文件信息记录文件读写、重复文件校验、僵尸文件处理相关逻辑）添加单行注释，说明代码用途。注释需简洁明了，不冗余，避免注释与代码脱节，确保后续维护可理解。

- 依赖管理：模块依赖项统一写入根目录的requirements.txt，注明依赖包名称与稳定版本，避免版本冲突；若需额外依赖哈希计算、定时任务、JSON解析相关包，需同步添加至依赖文件。依赖包优先选择系统全局依赖，减少冗余。

# 4. 模块详细设计

## 4.1 抽象基类设计（core/base.py）

定义模块核心接口，所有具体实现类必须继承该类并实现所有抽象方法，新增文件基本信息、重复文件校验、僵尸文件清理、文件信息记录文件读写相关参数、返回值定义，确保接口统一；保留基础代码框架，不体现具体方法实现细节，为后续实现类提供统一标准。

```python
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple

class BaseDocumentStore(ABC):
    """文档存储抽象基类，定义文档存取核心接口（不包含解析功能），包含文件类型、基本信息、重复文件、僵尸文件、文件信息记录文件相关处理接口"""

    @abstractmethod
    def create_document(self, content: str, file_name: str, file_type: str, content_hash: str) -> Dict[str, str]:
        """
        创建标准文档结构（生成唯一doc_id），记录文件基本信息（含content_hash、last_access_time、info_file_path），不执行存储操作
        :param content: 文本内容（已解析/清洗完成）
        :param file_name: 原始文件名（从文档解析模块传入，含后缀）
        :param file_type: 原始文件类型（从文档解析模块传入，不含“.”，小写）
        :param content_hash: 解析后文本内容的哈希值（用于重复文件校验）
        :return: 标准化文档结构，包含doc_id、content、file_name、file_type、storage_type、create_time、update_time、file_size、content_hash、last_access_time、info_file_path
        :raises: ValueError: 当content为空、file_name非法时抛出异常
        """
        pass

    @abstractmethod
    def save_document(self, document: Dict[str, str]) -> bool:
        """
        保存单个文档（解析后的文本，按原始文件类型存储）到存储介质，同步生成并写入文件信息记录文件
        :param document: 标准化文档结构（含所有基本信息）
        :return: 操作成功返回True，失败返回False
        :raises: KeyError: 当document缺少必要字段时抛出异常
        """
        pass

    @abstractmethod
    def get_document(self, doc_id: str) -> Optional[Dict[str, str]]:
        """
        根据doc_id获取文档内容及完整基本信息，同步更新last_access_time，同步更新文件信息记录文件
        :param doc_id: 文档唯一标识
        :return: 标准化文档结构（含所有基本信息），不存在则返回None
        :raises: ValueError: 当doc_id格式非法时抛出异常
        """
        pass

    @abstractmethod
    def update_document(self, doc_id: str, new_content: str, new_content_hash: str) -> bool:
        """
        根据doc_id更新文档内容（按原始文件类型存储），同步更新update_time、file_size、content_hash、last_access_time，同步更新文件信息记录文件
        :param doc_id: 文档唯一标识
        :param new_content: 新的文档内容（已解析/清洗完成）
        :param new_content_hash: 新内容的哈希值（用于重复校验）
        :return: 操作成功返回True，失败返回False
        :raises: ValueError: 当doc_id格式非法、new_content为空时抛出异常
        """
        pass

    @abstractmethod
    def delete_document(self, doc_id: str) -> bool:
        """
        根据doc_id删除文档及相关基本信息，同步删除对应的文件信息记录文件，同步更新哈希关联表
        :param doc_id: 文档唯一标识
        :return: 操作成功返回True，失败返回False
        :raises: ValueError: 当doc_id格式非法时抛出异常
        """
        pass

    @abstractmethod
    def check_duplicate_file(self, content_hash: str, file_name: str, file_type: str, content_length: int, force_save: bool = False) -> Tuple[bool, Optional[str]]:
        """
        重复文件校验接口，采用哈希校验+关键信息比对双重机制，同步校验文件信息记录文件
        :param content_hash: 解析后文本内容的哈希值
        :param file_name: 原始文件名（含后缀）
        :param file_type: 原始文件类型（不含“.”，小写）
        :param content_length: 解析后文本内容长度（字节）
        :param force_save: 是否强制存储，默认False（跳过校验）
        :return: 元组（是否重复，重复文档的doc_id；若不重复，doc_id为None）
        :raises: ValueError: 当content_hash为空、content_length小于0时抛出异常
        """
        pass

    @abstractmethod
    def identify_zombie_files(self, threshold_days: int) -> list:
        """
        僵尸文件识别接口，识别符合条件的僵尸文件（含存储文件和文件信息记录文件）
        :param threshold_days: 僵尸文件判定阈值（天数），超过该天数未访问/更新则判定为僵尸文件
        :return: 僵尸文件列表，包含文件路径、doc_id、文件大小、last_access_time等信息
        :raises: ValueError: 当threshold_days小于0时抛出异常
        """
        pass

    @abstractmethod
    def clean_zombie_files(self, threshold_days: int, backup: bool = False) -> Dict[str, int]:
        """
        僵尸文件清理接口，清理识别出的僵尸文件（含存储文件和文件信息记录文件），同步备份
        :param threshold_days: 僵尸文件判定阈值（天数）
        :param backup: 是否备份清理的文件，默认不备份
        :return: 清理结果字典，包含total（识别总数）、success（清理成功数）、fail（清理失败数）、release_size（释放空间，字节）
        :raises: ValueError: 当threshold_days小于0时抛出异常
        :raises: OSError: 当备份目录无法访问、存储目录权限不足时抛出异常
        """
        pass

    @abstractmethod
    def read_info_file(self, doc_id: str) -> Optional[Dict[str, str]]:
        """
        根据doc_id读取对应的文件信息记录文件，快速获取文档基本信息（无需读取文档内容）
        :param doc_id: 文档唯一标识
        :return: 文档基本信息字典，文件信息记录文件不存在/损坏则返回None
        :raises: ValueError: 当doc_id格式非法时抛出异常
        :raises: OSError: 当文件无法读取时抛出异常
        """
        pass

    @abstractmethod
    def write_info_file(self, document: Dict[str, str]) -> bool:
        """
        根据标准化文档结构，生成并写入文件信息记录文件（JSON格式）
        :param document: 标准化文档结构（含所有基本信息）
        :return: 操作成功返回True，失败返回False
        :raises: KeyError: 当document缺少必要字段时抛出异常
        :raises: OSError: 当文件无法写入时抛出异常
        """
        pass
```

## 4.2 具体实现类基础设计（core/impl.py）

提供本地存储默认实现（便于初学者落地部署），继承BaseDocumentStore抽象类，实现所有抽象方法，新增存储类型（与原始类型一致）处理、文件基本信息记录与更新、重复文件校验、僵尸文件识别与清理、文件信息记录文件读写逻辑；保留基础代码框架，不体现具体实现细节，确保代码简洁、可扩展。

```python
import os
import time
import json
import uuid
import hashlib
from typing import Dict, Optional, Tuple
from .base import BaseDocumentStore
from config_module.core.impl import ConfigManager
from log_module.core.impl import SystemLogger
from document_store_module.utils.tool_functions import generate_doc_id, get_document_path, get_info_file_path, get_file_size, calculate_content_hash, is_duplicate_辅助, is_zombie_file, backup_file, json_dump, json_load

class LocalDocumentStore(BaseDocumentStore):
    """本地文档存储实现类：将解析后的文本按原始文件类型持久化存储，实现核心接口，包含重复文件校验、僵尸文件清理、文件信息记录文件读写功能"""

    def __init__(self):
        # 初始化配置管理器、日志器，依赖基础支撑层模块
        self.config_manager = ConfigManager()
        self.logger = SystemLogger(module_name="LocalDocumentStore")
        # 加载模块专属配置，无配置时使用默认值
        self.storage_dir = self.config_manager.get("document_store.storage_dir", default="./documents")
        self.supported_file_types = self.config_manager.get("document_store.supported_file_types", 
                                                           default=["pdf", "docx", "doc", "xlsx", "xls", "pptx", "ppt", "txt", "md", "rtf", "csv", "json"])
        self.hash_algorithm = self.config_manager.get("document_store.hash_algorithm", default="md5")
        self.zombie_threshold = self.config_manager.get("document_store.zombie_threshold_days", default=30)
        self.core_doc_prefix = self.config_manager.get("document_store.core_doc_prefix", default=[])
        self.backup_dir = self.config_manager.get("document_store.backup_dir", default="./backup/zombie_files")
        # 初始化哈希关联表（内存+持久化），确保模块重启后校验功能正常
        self.hash_doc_map = self._load_hash_map()
        # 初始化存储目录、备份目录（若不存在则创建）
        self._init_storage_dir()

    def _init_storage_dir(self):
        """初始化存储目录、备份目录，确保目录存在，避免存储异常"""
        pass

    def _load_hash_map(self):
        """加载哈希-doc_id关联表（持久化文件），若不存在则创建空字典，保障校验功能连续性"""
        pass

    def _save_hash_map(self):
        """持久化哈希-doc_id关联表，避免模块重启后关联数据丢失"""
        pass

    def create_document(self, content: str, file_name: str, file_type: str, content_hash: str) -> Dict[str, str]:
        """实现抽象方法：创建标准化文档结构，生成唯一doc_id，完整记录所有基本信息"""
        pass

    def save_document(self, document: Dict[str, str]) -> bool:
        """实现抽象方法：将文档按原始文件类型保存到本地，同步生成并写入文件信息记录文件"""
        pass

    def get_document(self, doc_id: str) -> Optional[Dict[str, str]]:
        """实现抽象方法：根据doc_id获取文档内容及完整基本信息，同步更新last_access_time及信息记录文件"""
        pass

    def update_document(self, doc_id: str, new_content: str, new_content_hash: str) -> bool:
        """实现抽象方法：根据doc_id更新文档内容，同步更新相关信息及文件信息记录文件，确保数据一致"""
        pass

    def delete_document(self, doc_id: str) -> bool:
        """实现抽象方法：根据doc_id删除文档及对应文件信息记录文件，同步更新哈希关联表"""
        pass

    def check_duplicate_file(self, content_hash: str, file_name: str, file_type: str, content_length: int, force_save: bool = False) -> Tuple[bool, Optional[str]]:
        """实现抽象方法：执行重复文件校验，采用双重校验机制，返回校验结果"""
        pass

    def identify_zombie_files(self, threshold_days: int) -> list:
        """实现抽象方法：识别符合条件的僵尸文件（含存储文件和信息记录文件），返回详细列表"""
        pass

    def clean_zombie_files(self, threshold_days: int, backup: bool = False) -> Dict[str, int]:
        """实现抽象方法：清理僵尸文件，支持备份功能，返回清理结果"""
        pass

    def read_info_file(self, doc_id: str) -> Optional[Dict[str, str]]:
        """实现抽象方法：读取文件信息记录文件，快速返回文档基本信息，无需解析文档内容"""
        pass

    def write_info_file(self, document: Dict[str, str]) -> bool:
        """实现抽象方法：根据标准化文档结构，生成并写入文件信息记录文件（JSON格式）"""
        pass
```

## 4.3 模块工具函数补充（utils/tool_functions.py）

工具函数为模块核心功能提供辅助支撑，独立封装、可复用，涵盖doc_id生成、路径计算、哈希计算、文件操作、JSON处理等场景，确保核心代码简洁，降低耦合度；所有工具函数均添加异常捕获和日志记录，保障稳定性。

```python
import os
import time
import json
import uuid
import hashlib
from typing import Optional, Dict, List
from log_module.core.impl import SystemLogger

# 初始化日志器
logger = SystemLogger(module_name="tool_functions")

def generate_doc_id() -> str:
    """
    生成文档唯一标识，采用UUID4格式，确保全局唯一
    :return: 字符串格式的doc_id（UUID4）
    """
    pass

def get_document_path(storage_dir: str, doc_id: str, file_type: str) -> str:
    """
    计算文档存储文件的完整路径，遵循命名规则
    :param storage_dir: 文档存储根目录
    :param doc_id: 文档唯一标识
    :param file_type: 存储文件类型（后缀名，不含“.”）
    :return: 文档存储文件完整路径（字符串）
    """
    pass

def get_info_file_path(storage_dir: str, doc_id: str) -> str:
    """
    计算文件信息记录文件的完整路径，遵循命名规则
    :param storage_dir: 文档存储根目录（与文档存储文件同目录）
    :param doc_id: 文档唯一标识
    :return: 文件信息记录文件完整路径（字符串）
    """
    pass

def get_file_size(file_path: str) -> int:
    """
    计算文件大小（单位：字节），若文件不存在或异常，返回0并记录日志
    :param file_path: 文件完整路径
    :return: 文件大小（字节），异常时返回0
    """
    pass

def calculate_content_hash(content: str, algorithm: str = "md5") -> str:
    """
    计算文本内容的哈希值，用于重复文件校验
    :param content: 解析后的文本内容
    :param algorithm: 哈希算法，支持md5、sha256，默认md5
    :return: 哈希值字符串（小写）
    :raises: ValueError: 当算法不支持、content为空时抛出异常
    """
    pass

def is_duplicate_辅助(existing_docs: List[Dict[str, str]], file_name: str, file_type: str, content_length: int) -> Optional[str]:
    """
    辅助校验重复文件，比对文件名、文件类型、文本长度，用于哈希校验后的补充校验
    :param existing_docs: 已存储文档的基本信息列表（仅含必要字段）
    :param file_name: 待校验文件名（含后缀）
    :param file_type: 待校验文件类型（不含“.”）
    :param content_length: 待校验文本长度（字节）
    :return: 重复文档的doc_id，无重复则返回None
    """
    pass

def is_zombie_file(doc_info: Dict[str, str], threshold_days: int) -> bool:
    """
    判断文档是否为僵尸文件（类型2），根据最后访问时间和更新时间判定
    :param doc_info: 文档基本信息字典（含last_access_time、update_time）
    :param threshold_days: 判定阈值（天数）
    :return: True-是僵尸文件，False-不是僵尸文件
    :raises: ValueError: 当时间格式异常、threshold_days小于0时抛出异常
    """
    pass

def backup_file(file_path: str, backup_dir: str) -> bool:
    """
    备份文件到指定目录，备份文件命名格式：原文件名_时间戳.后缀
    :param file_path: 待备份文件完整路径
    :param backup_dir: 备份目录
    :return: 备份成功返回True，失败返回False
    """
    pass
```

# 5. 接口调用示例

以本地存储实现类LocalDocumentStore为例，提供核心接口的调用示例，覆盖文档新增、读取、更新、删除、重复文件校验、僵尸文件处理、文件信息读取/写入全场景，确保调用逻辑清晰、参数规范，便于开发人员参考使用。

## 5.1 初始化实例

```python
from document_store_module.core.impl import LocalDocumentStore

# 初始化本地文档存储实例（自动加载配置、初始化目录、加载哈希关联表）
doc_store = LocalDocumentStore()
```

## 5.2 核心接口调用示例

### 5.2.1 文档新增（创建+保存）

```python
# 模拟文档解析模块传入的参数
content = "这是解析后的测试文档内容，用于接口调用示例。"
file_name = "测试文档.md"
file_type = "md"  # 统一小写，与原始文件后缀一致
content_hash = "e10adc3949ba59abbe56e057f20f883e"  # 模拟MD5哈希值

# 1. 创建标准化文档结构（生成doc_id，记录所有基本信息）
document = doc_store.create_document(content, file_name, file_type, content_hash)
print("创建的文档结构：", document)

# 2. 保存文档（同步生成文件信息记录文件）
save_result = doc_store.save_document(document)
if save_result:
    print(f"文档保存成功，doc_id: {document['doc_id']}")
else:
    print("文档保存失败")
```

### 5.2.2 文档读取（含信息更新）

```python
# 传入已存在的doc_id（实际使用时替换为真实doc_id）
doc_id = "123e4567-e89b-12d3-a456-426614174000"

# 读取文档（同步更新last_access_time及文件信息记录文件）
document = doc_store.get_document(doc_id)
if document:
    print(f"文档读取成功，文件名：{document['file_name']}，内容：{document['content']}")
else:
    print(f"文档不存在，doc_id: {doc_id}")
```

### 5.2.3 文档更新

```python
# 传入已存在的doc_id及更新参数
doc_id = "123e4567-e89b-12d3-a456-426614174000"
new_content = "这是更新后的测试文档内容，替换原有内容。"
new_content_hash = "c33367701511b4f6020ec61ded352059"  # 新内容的MD5哈希值

# 执行更新（同步更新update_time、file_size、content_hash及文件信息记录文件）
update_result = doc_store.update_document(doc_id, new_content, new_content_hash)
if update_result:
    print(f"文档更新成功，doc_id: {doc_id}")
else:
    print(f"文档更新失败，doc_id: {doc_id}")
```

### 5.2.4 文档删除

```python
# 传入已存在的doc_id
doc_id = "123e4567-e89b-12d3-a456-426614174000"

# 执行删除（同步删除文件信息记录文件、更新哈希关联表）
delete_result = doc_store.delete_document(doc_id)
if delete_result:
    print(f"文档删除成功，doc_id: {doc_id}")
else:
    print(f"文档删除失败，doc_id: {doc_id}")
```

### 5.2.5 重复文件校验

```python
# 模拟待校验文件参数
content_hash = "e10adc3949ba59abbe56e057f20f883e"
file_name = "测试文档.md"
file_type = "md"
content_length = len("这是解析后的测试文档内容，用于接口调用示例。".encode("utf-8"))  # 文本长度（字节）
force_save = False  # 不强制存储

# 执行重复校验
is_duplicate, duplicate_doc_id = doc_store.check_duplicate_file(content_hash, file_name, file_type, content_length, force_save)
if is_duplicate:
    print(f"存在重复文件，重复doc_id: {duplicate_doc_id}")
else:
    print("无重复文件，可正常存储")
```

### 5.2.6 僵尸文件识别与清理

```python
# 僵尸文件判定阈值（30天，与默认配置一致）
threshold_days = 30

# 1. 识别僵尸文件
zombie_files = doc_store.identify_zombie_files(threshold_days)
print(f"识别到的僵尸文件数量：{len(zombie_files)}")
for zombie in zombie_files:
    print(f"僵尸文件信息：{zombie}")

# 2. 清理僵尸文件（不备份）
clean_result = doc_store.clean_zombie_files(threshold_days, backup=False)
print("僵尸文件清理结果：", clean_result)
```

### 5.2.7 文件信息记录文件读写

```python
# 传入已存在的doc_id
doc_id = "123e4567-e89b-12d3-a456-426614174000"

# 1. 读取文件信息记录文件（快速获取基本信息，无需读取文档内容）
info = doc_store.read_info_file(doc_id)
if info:
    print("文件信息读取成功：", info)
else:
    print(f"文件信息记录文件不存在或损坏，doc_id: {doc_id}")

# 2. （补充）模拟更新后写入文件信息记录文件
# 先获取文档结构，修改相关字段后重新写入
document = doc_store.get_document(doc_id)
if document:
    # 模拟更新字段（如last_access_time）
    document["last_access_time"] = "2026-02-28 16:00:00"
    write_result = doc_store.write_info_file(document)
    if write_result:
        print("文件信息记录文件更新写入成功")
    else:
        print("文件信息记录文件写入失败")
```

# 6. 测试用例基础构建

测试用例围绕模块核心功能、异常场景、边界条件设计，确保覆盖所有接口及工具函数，验证模块功能正确性、稳定性和兼容性，为开发测试、回归验证提供标准化依据，测试用例需与接口实现、异常处理规范完全匹配。

## 6.1 测试用例设计原则

- 全面性：覆盖抽象基类所有接口、具体实现类（LocalDocumentStore）所有方法、工具函数，涵盖正常场景、异常场景、边界场景。

- 针对性：每个测试用例对应一个具体功能点或异常场景，明确测试目的、输入参数、预期结果，便于定位问题。

- 可执行性：测试用例输入参数可模拟、预期结果可验证，无需依赖未实现的外部模块（可通过Mock模拟依赖）。

- 规范性：测试用例统一命名、统一格式，包含用例ID、测试模块、测试场景、输入、预期输出、实际输出、测试结果等字段。

## 6.2 核心测试用例分类及示例

### 6.2.1 正常场景测试用例

|用例ID|测试模块|测试场景|输入参数|预期输出|
|---|---|---|---|---|
|TC-001|LocalDocumentStore.create_document|正常创建标准化文档结构|content="测试内容"，file_name="test.txt"，file_type="txt"，content_hash="e10adc3949ba59abbe56e057f20f883e"|返回包含doc_id（UUID4）、所有基本信息的字典，无异常抛出|
|TC-002|LocalDocumentStore.save_document|正常保存文档及文件信息记录文件|create_document返回的标准化文档字典|返回True，存储目录生成对应文档文件和.info.json文件|
|TC-003|LocalDocumentStore.check_duplicate_file|哈希值匹配，判定重复文件|content_hash="e10adc3949ba59abbe56e057f20f883e"（已存在），其他参数匹配|返回（True，对应doc_id），不执行存储|
### 6.2.2 异常场景测试用例

|用例ID|测试模块|测试场景|输入参数|预期输出|
|---|---|---|---|---|
|TC-101|LocalDocumentStore.create_document|content为空，创建文档失败|content=""，file_name="test.txt"，file_type="txt"，content_hash="e10adc3949ba59abbe56e057f20f883e"|抛出ValueError异常，日志记录错误信息|
|TC-102|LocalDocumentStore.get_document|doc_id格式非法，读取失败|doc_id="invalid-doc-id"（非UUID4格式）|抛出ValueError异常，日志记录错误信息|
|TC-103|tool_functions.calculate_content_hash|哈希算法不支持，计算失败|content="测试内容"，algorithm="sha512"（未配置支持）|抛出ValueError异常，日志记录错误信息|
### 6.2.3 边界场景测试用例

- TC-201：文件名称包含非法字符（/、\、:），create_document自动生成默认文件名，无异常。

- TC-202：文本内容极长（100MB），save_document正常存储，file_size记录准确。

- TC-203：僵尸文件阈值为0天，identify_zombie_files识别所有未即时访问的文档。

- TC-204：force_save=True，跳过重复校验，强制存储重复文件，生成新doc_id。

## 6.3 测试执行要求

- 测试环境：与开发环境一致，配置文件使用默认配置，存储目录、备份目录权限正常。

- 依赖处理：使用Mock模拟ConfigManager、SystemLogger等外部依赖，避免外部模块影响测试结果。

- 结果记录：每个测试用例执行后，记录实际输出与预期输出的一致性，失败用例需标注失败原因、复现步骤。

- 回归测试：模块修改后，需重新执行所有相关测试用例，确保原有功能不受影响。

# 7. 开发与交付规范

为确保模块开发过程规范、代码可维护、交付物完整，统一开发标准、代码规范、交付要求，适配团队协作及后续迭代，所有开发、交付操作需严格遵循本规范。

## 7.1 开发规范

### 7.1.1 代码规范

- 命名规范：类名采用PascalCase（如LocalDocumentStore），方法名、变量名采用snake_case（如create_document、hash_doc_map），常量名采用全大写+下划线（如SUPPORTED_FILE_TYPES），命名需清晰、贴合功能，避免歧义。

- 注释规范：类、方法必须添加文档字符串（docstring），说明功能、参数、返回值、异常类型；关键代码段添加单行注释，解释逻辑思路；工具函数、私有方法需明确用途，便于后续维护。

- 代码格式：遵循PEP8规范，缩进使用4个空格，每行代码长度不超过120字符，导入模块按“标准库→第三方库→自定义库”顺序排列，避免冗余导入。

- 模块化设计：核心逻辑、辅助功能分离，抽象基类与具体实现类分离，工具函数独立封装，避免代码冗余，降低耦合度；新增功能需遵循“单一职责”原则，不修改原有稳定代码。

### 7.1.2 开发流程规范

1. 需求确认：开发前明确模块需求、接口定义、异常处理场景，确认与基础支撑层（配置、日志模块）的交互方式。

2. 编码开发：按抽象基类→具体实现类→工具函数的顺序开发，每完成一个功能点，编写对应的测试用例，确保功能正确性。

3. 代码评审：开发完成后，提交代码评审，重点检查代码规范、逻辑正确性、异常处理完整性、测试用例覆盖度，评审通过后方可进入测试阶段。

4. 测试修复：根据测试用例执行结果，修复代码中的Bug，修复后重新执行相关测试用例，确保Bug彻底解决，不引入新问题。

## 7.2 交付规范

### 7.2.1 模块交付物清单（必须）

模块开发完成后，需提交以下交付物，确保符合系统整体交付标准：

- core/base.py：抽象基类文件，包含模块核心接口定义，更新支持的文件类型说明；

- core/impl.py：具体实现类文件，继承抽象基类并实现所有接口，包含md、py、excel、ppt/pptx、csv、json、xml文件的解析逻辑；

- utils/tool_functions.py：模块专属工具函数文件（无则保留空文件），更新文件类型校验逻辑；

- config/config.py：模块配置文件（无专属配置则保留空文件），可添加新增文件类型的相关配置；

- tests/test_impl.py：核心测试用例文件，覆盖核心功能与异常场景，包含md、py、excel、ppt/pptx、csv、json、xml文件的测试用例；

- README.md：模块说明文档，详细说明模块功能、接口、使用方法、依赖项，补充新增文件类型的解析说明；

- requirements.txt：模块依赖包清单，注明包名称与版本，新增md、excel、ppt/pptx、xml解析所需依赖包。

### 7.2.2 交付要求

- 代码质量：无语法错误、无逻辑错误，测试用例通过率100%，无未修复Bug；代码覆盖率不低于80%，核心接口、异常处理场景全覆盖。

- 文档完整性：所有交付文档内容完整、格式规范，接口说明、测试用例、配置说明清晰，便于使用方、运维方理解和操作。

- 兼容性：模块与基础支撑层模块（配置、日志）兼容，支持配置文件中所有可配置项，无依赖冲突；具体实现类可灵活替换，不影响上层调用。

- 交付验收：提交交付物后，配合验收方完成功能验证、代码评审、文档检查，及时处理验收过程中提出的问题。

# 8. 异常处理规范

模块异常处理遵循“提前预防、及时捕获、清晰记录、友好反馈”的原则，统一异常类型、日志输出规范、异常处理逻辑，确保模块在异常场景下稳定运行，便于问题定位和排查，避免因异常未处理导致模块崩溃、数据丢失。

## 8.1 异常分类及处理原则

### 8.1.1 业务异常

定义：因业务逻辑不满足、输入参数非法导致的异常（如content为空、doc_id格式非法、文件类型不支持），属于可预测异常，需提前校验、友好处理。

处理原则：

- 输入校验：所有接口、工具函数在执行核心逻辑前，先校验输入参数的合法性（如content非空、doc_id为UUID4格式、file_type在支持列表内）。

- 异常抛出：校验失败时，抛出对应类型的异常（优先使用ValueError），异常信息清晰，明确指出错误原因（如“content不能为空”“doc_id格式非法，需为UUID4”）。

- 日志记录：抛出异常时，同步记录警告或错误日志，包含异常类型、错误信息、输入参数、调用栈信息，便于定位问题。

### 8.1.2 系统异常

定义：因系统环境、存储介质、外部依赖导致的异常（如存储目录无权限、文件损坏、配置模块调用失败），属于不可预测异常，需捕获并妥善处理。

处理原则：

- 异常捕获：对可能出现系统异常的代码段（如文件读写、目录创建、外部模块调用），使用try-except捕获异常，避免模块崩溃。

- 降级处理：捕获异常后，执行降级逻辑（如文件写入失败时，返回False，记录错误日志，不影响其他操作；配置加载失败时，使用默认配置）。

- 日志记录：记录错误日志，包含异常类型、错误信息、调用栈、系统环境信息（如存储目录路径、权限信息），便于运维排查。

## 8.2 异常日志规范

- 日志级别：业务异常（输入非法）记录WARNING级别，系统异常（文件读写失败、权限不足）记录ERROR级别，核心操作异常（数据丢失、模块崩溃）记录CRITICAL级别。

- 日志格式：统一格式为“时间戳 | 模块名 | 日志级别 | 异常信息 | 输入参数 | 调用栈”，确保日志信息完整、可追溯。

- 日志输出：日志输出到指定目录，按日期分割，保留30天日志，便于后续排查；核心异常（如数据丢失）需同步触发告警，通知运维人员。

## 8.3 异常处理示例

```python
def create_document(self, content: str, file_name: str, file_type: str, content_hash: str) -> Dict[str, str]:
    # 业务异常校验与处理
    if not content:
        self.logger.warning(f"create_document failed: content is empty, file_name={file_name}")
        raise ValueError("content不能为空，无法创建文档")
    if file_type not in self.supported_file_types:
        self.logger.warning(f"create_document failed: unsupported file type {file_type}, file_name={file_name}")
        raise ValueError(f"不支持的文件类型：{file_type}，支持的类型为：{self.supported_file_types}")
    # 系统异常捕获与处理
    try:
        doc_id = generate_doc_id()
        # 核心逻辑...
    except Exception as e:
        self.logger.error(f"create_document system error: {str(e)}, content_hash={content_hash}", exc_info=True)
        raise  # 重新抛出异常，让上层模块感知，同时记录日志
```

# 9. 扩展说明

本模块设计遵循可扩展原则，支持后续功能扩展、存储方式扩展、场景适配扩展，以下为扩展方向、扩展规范及注意事项，便于后续开发人员进行扩展开发，确保扩展后模块的兼容性、稳定性。

## 9.1 扩展方向

### 9.1.1 存储方式扩展

当前模块实现本地存储（LocalDocumentStore），后续可扩展其他存储方式，如S3对象存储、MinIO对象存储、数据库存储等，扩展时需遵循以下规范：

- 继承抽象基类：新的存储实现类（如S3DocumentStore）必须继承BaseDocumentStore，实现所有抽象方法，确保接口统一。

- 配置适配：新增存储方式的专属配置（如S3的access_key、secret_key、bucket_name），加入模块配置模板，支持通过配置文件切换存储方式。

- 功能对齐：新存储实现类的功能的需与LocalDocumentStore完全对齐，包括文档存储、文件信息记录、重复文件校验、僵尸文件处理等，确保上层调用无感知。

### 9.1.2 功能扩展

根据业务需求，可扩展以下功能，扩展时需遵循模块化设计原则，不破坏原有代码逻辑：

- 文档加密存储：扩展文档保存、读取逻辑，支持对文档内容、文件信息记录文件进行加密（如AES加密），新增加密相关配置（加密密钥、加密算法）。

- 批量操作接口：扩展批量创建、批量保存、批量删除、批量读取接口，提升批量处理效率，适配大量文档场景。

- 文档版本管理：新增文档版本记录功能，每次更新文档时保留历史版本，支持版本回滚，新增版本相关基本信息（version、version_create_time）。

- 自定义重复校验规则：支持通过配置文件自定义重复校验规则（如仅校验哈希值、仅校验文件名），提升场景适配性。

### 9.1.3 场景适配扩展

针对不同业务场景，可进行场景适配扩展，如：

- 大文件场景：扩展大文件分片存储、断点续传功能，适配GB级以上文档存储，优化存储效率和稳定性。

- 高并发场景：优化哈希关联表、文件读写逻辑，支持并发操作，避免文件锁冲突，提升模块并发处理能力。

- 跨平台场景：适配Windows、Linux、MacOS等不同操作系统，统一存储目录格式、文件权限处理逻辑。

## 9.2 扩展开发规范

- 向后兼容：扩展功能、新增存储方式时，必须保证与原有接口、配置、数据格式兼容，不影响原有功能的正常运行，不修改原有核心代码。

- 模块化封装：新增功能、新存储实现类需独立封装，与原有代码解耦，新增工具函数放入utils目录，避免代码冗余。

- 测试覆盖：扩展开发完成后，编写对应的测试用例，覆盖新增功能、新存储方式的所有场景，确保扩展功能正确、稳定。

- 文档更新：扩展后需同步更新模块设计说明书、接口说明文档、测试用例文档、配置模板，确保文档与代码一致。

## 9.3 扩展注意事项

- 依赖管理：扩展时若引入新的第三方依赖（如S3 SDK），需在配置文件中添加依赖说明，明确依赖版本，避免依赖冲突。

- 性能优化：扩展功能（如大文件存储、批量操作）时，需考虑性能影响，优化代码逻辑，避免出现性能瓶颈（如频繁IO操作、内存占用过高）。

- 数据迁移：若扩展存储方式（如从本地存储迁移到S3存储），需提供数据迁移工具和迁移方案，确保原有数据不丢失、可正常访问。

- 兼容性测试：扩展后需进行兼容性测试，验证与上层模块、基础支撑层模块的兼容性，确保整个系统稳定运行。
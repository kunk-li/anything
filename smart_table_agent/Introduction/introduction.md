整个系统的后端架构可以分为 五大核心层：API 接入层、业务微服务层、AI Agent 层、数据存储层、异步任务层。它们共同支持前端完成：文件上传、文件管理、智能解析、提问与结果展示。

1️⃣ API 接入层（API Gateway / BFF）

前端所有请求都会经过这一层，它的职责包括：

前端统一入口

用户鉴权（JWT / OAuth2）

跨域处理（CORS）

请求参数校验

将请求转发到后端各个微服务

聚合请求返回（如一个请求需要查询多个服务）

技术选型可为：

Node.js（Express / NestJS）

或 Python FastAPI BFF

示例处理的 API：

/upload 文件上传

/agent/analyze 向 Agent 发起问题解析

/agent/result 查询异步任务结果

/files/list 获取文件列表

2️⃣ 业务微服务层（Business Microservices）

这一层是核心业务逻辑所在，包括三个主要服务：

✔ File Service（文件服务）

负责：

接收文件上传

调用对象存储（S3/MinIO）保存文件

写入文件元信息到数据库

提供文件列表、文件删除接口

前端显示“我的文件”功能就依赖这一服务。

✔ Auth Service（鉴权服务）

负责：

用户认证/登录

签发 JWT

多租户（可选）

用户会话管理

✔ Agent Service（Agent 网关服务）

负责：

接收前端对文件的提问

将任务投递到队列

查询任务执行结果

返回给前端

它并不执行 AI 分析本身，而是把复杂解析任务委托给后面的异步 Agent Worker。

3️⃣ 智能 AI Agent 层（Agent Workers / LLM Service）

这是进行文件分析的核心计算层，用于执行以下任务：

文件解析（文本、PDF、Excel）

内容抽取（关键信息、关键字、风险段落）

调用 LLM 对文本进行问答、总结、结构化 JSON 等

写入最终分析结果

任务一般是 耗时更长 的，所以采用 异步任务队列 + Worker 的形式运行。

技术选型：

Python

Celery、RQ、FastAPI Worker

调用 LLM（OpenAI API / 自建 LLM / LangChain）

4️⃣ 数据存储层（Storage Layer）

包含两种存储：

✔ 文件对象存储（S3 / MinIO）

保存：

上传的原始文件

转换后的临时文件（如 PDF 转 Text）

解析结果文件（如 JSON、结构化输出）

✔ 数据库（Postgres / MongoDB）

保存：

文件元信息（文件名、大小、上传用户、时间）

用户账号信息

Agent 分析结果（JSON）

异步任务状态（可选）

数据库结构相对简单，围绕文件与分析结果即可。

5️⃣ 异步任务层（Queue Layer）

处理耗时任务：解析文件、调用 LLM、生成结果。

技术选型：

RabbitMQ / Kafka / Redis Queue

Worker：Celery / rq / custom Python worker

任务流程：

前端提问 → API → Agent Service

Agent Service 将任务投递到队列

Worker 异步执行：

下载文件

内容解析

调用 LLM

写入结果到数据库

前端轮询 /result/{task_id} 获取结果

📌 全链路流程总结（从前端到后端）
① 上传文件

前端
→ /upload
→ API Gateway
→ File Service
→ 存储 S3
→ 记录数据库

② 向文件提问

前端
→ /agent/analyze
→ Agent Service
→ Queue
→ Worker（读取文件 + AI 解析）
→ 结果写入数据库

③ 查询解析结果

前端
→ /agent/result/{task_id}
→ Agent Service
→ DB 查询
→ 前端显示分析内容

📐 架构风格特点

前后端分离

微服务架构（可小规模也可扩展）

支持异步任务

能支撑大文件解析和 AI 算法计算

支持后续增加更多“文件操作类型”（OCR、摘要、可视化等）
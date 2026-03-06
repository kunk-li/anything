# llm_adapter_module（数据层-大模型对接模块）

本模块为 RAG/Agent 系统的数据层核心模块之一：提供向量模型（Embedding）、聊天大模型（LLM）、多模态大模型（MLLM）的**统一调用入口**与**适配器扩展机制**，并支持从文档解析模块产出的 `FileContent` 进行输入适配。

## 目录结构

```
llm_adapter_module/
├── core/        # 抽象接口 + 具体实现（适配器 & LLMService）
├── model/       # 统一请求/响应/文件内容数据模型
├── utils/       # 文本分段、向量归一化、媒体校验与转换
├── config/      # 读取全局 llm 配置（通过 config_module）
└── tests/       # 单元测试（mock 外部 HTTP）
```

## 依赖

- Python 3.10+
- 基础支撑层：
  - `config_module`（配置）
  - `log_module`（日志）
  - `exception_module`（异常）
- 第三方：
  - `requests`（HTTP 调用 OpenAI REST 接口）

## 配置（全局 config.yaml 中 llm 节点）

参考设计说明书中的结构（支持多厂商扩展），示例：

```yaml
llm:
  default_vector_model: "text-embedding-ada-002"
  default_chat_model: "gpt-3.5-turbo"
  default_multimodal_model: "gpt-4-vision-preview"
  openai:
    text-embedding-ada-002:
      api_key: "${OPENAI_EMBED_API_KEY}"
      api_base: "https://api.openai.com/v1"
      request_type: "VECTOR"
      adapter_class: "OpenAIVectorAdapter"
    gpt-3.5-turbo:
      api_key: "${OPENAI_CHAT_API_KEY}"
      api_base: "https://api.openai.com/v1"
      request_type: "CHAT"
      adapter_class: "OpenAIChatAdapter"
    gpt-4-vision-preview:
      api_key: "${OPENAI_MULTIMODAL_API_KEY}"
      api_base: "https://api.openai.com/v1"
      request_type: "MULTIMODAL"
      adapter_class: "OpenAIMultimodalAdapter"
      support_media: ["image", "audio"]
      max_media_size: 20
      # 可选：音频转写模型（用于 audio -> text 降级）
      transcription_model: "whisper-1"
  common:
    max_retry: 3
    timeout: 30
    batch_size: 32
    normalize_vector: true
    media_temp_dir: "temp/media"
```

## 使用方式

```python
from llm_adapter_module import LLMService
from llm_adapter_module.model.data_model import LLMRequest, LLMParam

llm = LLMService()

# 1) 向量化
resp = llm.call_llm(LLMRequest(
    request_type="VECTOR",
    input_text="hello",
    model_name="text-embedding-ada-002",
    model_param=LLMParam(normalize=True),
))
print(resp.vector_result)

# 2) 聊天
resp = llm.call_llm(LLMRequest(
    request_type="CHAT",
    input_text="请规划一个RAG系统开发流程",
    model_name="gpt-3.5-turbo",
    model_param=LLMParam(temperature=0.5, max_tokens=800),
))
print(resp.chat_result)
```

## 扩展新厂商/新模型

1. 在 `core/impl.py` 中新增适配器类（继承对应 ABC）。
2. 将类名注册到 `_ADAPTER_CLASS_REGISTRY`。
3. 在全局配置 `llm.<vendor>.<model_name>` 添加节点，填写 `adapter_class` 与必要鉴权信息。
4. 补充 tests 中的 mock 用例。

## 说明

- 本实现采用 REST 直接调用 OpenAI 兼容接口，避免对特定 SDK 版本强依赖。
- 多模态目前对 **image** 走 chat/completions 的多段 content；对 **audio** 默认走“转写降级”（需要配置 `transcription_model`）。

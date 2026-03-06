# llm_adapter_module（数据层-大模型对接模块）

本模块用于对接向量模型（Embedding）、聊天大模型（LLM）、多模态大模型（MLLM），对外提供统一调用入口 `LLMService`，并通过适配器模式屏蔽不同厂商接口差异。

> 说明：本仓库实现遵循《数据层-大模型对接模块（llm_adapter_module）设计说明书》中的结构与接口。  
> 其中 OpenAI 相关适配器提供 **HTTP示例实现 + 无API Key时的可测试降级实现**（返回伪向量/固定回复），便于离线单测。

## 目录结构

```
llm_adapter_module/
├── core/        # 抽象接口与具体实现（适配器 + 统一服务）
├── model/       # 统一数据模型（请求/响应/文件内容/多模态内容）
├── utils/       # 工具函数（向量归一化、文本分段、base64等）
├── config/      # 配置读取封装（从config_module读取全局llm配置）
└── tests/       # 单元测试
```

## 快速使用

```python
from llm_adapter_module import LLMService
from llm_adapter_module.model.data_model import LLMRequest, LLMParam

svc = LLMService()

resp = svc.call_llm(LLMRequest(
    request_type="CHAT",
    input_text="请规划一个RAG系统开发流程",
    model_name="default",
    model_param=LLMParam(temperature=0.3, max_tokens=800)
))
print(resp.code, resp.chat_result)
```

## 配置要求（全局config.yaml）

模块通过 `config_module` 读取 `llm.*` 配置（示例见设计说明书第7章）。至少需要：
- `llm.default_vector_model / default_chat_model / default_multimodal_model`
- 每个模型节点：`api_key / api_base / request_type / adapter_class`
- `llm.common.max_retry / timeout`

## 扩展新模型/厂商

1. 新增适配器类：在 `core/impl.py` 中新增类并继承对应抽象基类  
2. 在 `LLMService._build_adapter()` 的 mapping 中注册该适配器类名  
3. 在全局配置的 `llm.<vendor>.<model_name>` 节点中配置 `adapter_class` 为该类名  
4. 补充 tests 覆盖

## 错误码约定

- 参数校验失败：`PARAM_INVALID`
- 未注册模型：`MODEL_NOT_FOUND`
- 其他异常：通过 `exception_module.ExceptionHandler` 统一封装（如 `UNKNOWN_ERROR` 等）

## 依赖

- requests（HTTP调用示例）
- 基础支撑层模块：config_module、log_module、exception_module（外部已实现）

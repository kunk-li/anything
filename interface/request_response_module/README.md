# 请求响应处理模块 (request_response_module)

## 1. 模块功能
本模块实现系统接口层请求响应统一处理，包括：
- 请求参数校验（type、query、task、top_k 等）
- 请求标准化（默认值填充、session_id 生成）
- 调用协同调度模块执行核心业务
- 响应格式化（统一响应结构、trace_id、retryable）
- 异常捕获与转换（标准化错误响应）

## 2. 项目结构
遵循系统统一规范，包含 core, model, utils, config, tests 目录。

## 3. 核心接口
- `BaseRequestHandler`: 抽象基类
- `RequestHandler`: 默认实现类
- `validate_request(request)`: 请求参数校验
- `handle(request)`: 处理请求全流程
- `format_response(...)`: 格式化响应

## 4. 使用示例

### 4.1 基础调用
```python
from request_response_module.core.impl import RequestHandler
from orchestrator_module.core.impl import SimpleOrchestrator

# 初始化协同调度模块
# orchestrator = SimpleOrchestrator(rag_runner=rag, agent_runner=agent)

# 初始化请求处理器（注入调度模块）
handler = RequestHandler(orchestrator=orchestrator)

# 处理 RAG 请求
request = {
    "type": "rag",
    "query": "RAG 系统架构是什么？",
    "top_k": 5
}
response = handler.handle(request)
print(f"响应码：{response['code']}, 追踪 ID: {response['trace_id']}")
```

### 4.2 异常处理
```python
# 模拟非法请求（缺少必填参数）
invalid_request = {
    "type": "rag",
    # 缺少 query 参数
}

response = handler.handle(invalid_request)
# 响应示例：
# {
#   "code": "PARAM_MISSING",
#   "message": "RAG 模式必须提供 query 参数",
#   "data": null,
#   "trace_id": "xxx",
#   "retryable": false,
#   "details": {"field": "query", "expected": "string"}
# }
```

## 5. 依赖项
见 requirements.txt

## 6. 配置说明
在系统全局 config.yaml 中配置 request_response 节点：
```yaml
request_response:
  default_type: "rag"
  enable_trace: true
  max_request_size: 1048576
  timeout: 60
  validate_strict: true
```

## 7. 常见问题
- 请求校验失败：检查 type 是否为 rag/agent/hybrid，rag 模式需 query，agent 模式需 task
- trace_id 为空：检查配置中 enable_trace 是否为 true
- 响应格式不统一：确保所有异常都通过 handle_exception 方法处理



## ✅ 实现说明

| 特性 | 说明 |
| :--- | :--- |
| **遵循设计文档** | 代码结构、类名、方法名、参数定义完全匹配设计说明书 |
| **依赖管理** | 导入了设计文档中指定的所有依赖模块 |
| **异常处理** | 统一使用 ExceptionHandler，错误码遵循全局错误码表 |
| **可替换性** | 通过 `BaseRequestHandler` 抽象接口实现模块解耦 |
| **数据模型** | 使用 `dataclass` 定义请求/响应模型，确保类型安全 |
| **测试覆盖** | 提供基础单元测试框架，覆盖正常流程与异常场景 |
| **请求校验** | 支持 type、query、task、top_k 等参数校验 |
| **响应格式** | 统一响应结构（code/message/data/trace_id/retryable/details） |
| **链路追踪** | 所有响应包含 trace_id，便于问题排查 |

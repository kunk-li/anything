# 协同调度模块 (orchestrator_module)

## 1. 模块功能
本模块实现系统核心业务入口调度，包括：
- 根据请求类型路由至 RAG 或 Agent 模块
- 支持 Hybrid 协同模式
- 统一异常处理与响应封装
- 动态注册/注销业务模块

## 2. 项目结构
遵循系统统一规范，包含 core, model, utils, config, tests 目录。

## 3. 核心接口
- `BaseOrchestrator`: 抽象基类
- `SimpleOrchestrator`: 默认实现类
- `route(request)`: 路由决策与执行
- `call_orchestrator(request)`: 标准化接口调用

## 4. 使用示例

### 4.1 基础调用
```python
from orchestrator_module.core.impl import SimpleOrchestrator
from orchestrator_module.model.data_model import OrchestratorRequest

# 初始化 RAG 与 Agent 实例
# rag = SimpleRAG(llm_client=...)
# agent = SimpleAgent(tools=...)

# 初始化调度器（注入依赖）
orchestrator = SimpleOrchestrator(rag_runner=rag, agent_runner=agent)

# 调用（RAG 模式）
request = OrchestratorRequest(type="rag", query="问题内容", top_k=5)
response = orchestrator.call_orchestrator(request)
```

### 4.2 动态注册模块
```python
orchestrator = SimpleOrchestrator()
orchestrator.register_module("rag", rag_instance)
orchestrator.register_module("agent", agent_instance)
```
## 5. 依赖项
见 requirements.txt
## 6. 配置说明
在系统全局 config.yaml 中配置 orchestrator 节点：
```yaml
orchestrator:
  default_type: "rag"
  timeout: 60
  enable_intelligent_route: false
```
## 7. 常见问题
- 路由类型不支持：检查 type 参数是否为 rag/agent/hybrid
- 模块未注册：确保初始化时传入了 rag_runner 或 agent_runner 实例
- Hybrid 模式：由 Agent 内部通过工具调用 RAG 实现协同

---

## ✅ 实现说明

| 特性 | 说明 |
| :--- | :--- |
| **遵循设计文档** | 代码结构、类名、方法名、参数定义完全匹配设计说明书 |
| **依赖管理** | 导入了设计文档中指定的所有依赖模块 |
| **异常处理** | 统一使用 `OrchestratorException`，错误码遵循全局错误码表 |
| **可替换性** | 通过 `BaseOrchestrator` 抽象接口实现模块解耦 |
| **数据模型** | 使用 `dataclass` 定义请求/响应模型，确保类型安全 |
| **测试覆盖** | 提供基础单元测试框架，覆盖正常流程与异常场景 |
| **模块注册** | 支持动态注册 RAG/Agent 模块，扩展性强 |
| **路由策略** | 支持 rag/agent/hybrid 三种路由类型 |

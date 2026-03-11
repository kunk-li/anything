# Agent 模块 (agent_module)

## 1. 模块功能
本模块实现 Agent 智能代理核心能力，包括：
- 任务解析与决策规划
- 工具调用与管理
- 结果汇总与响应生成
- 会话状态持久化
- 失败重试与超时控制

## 2. 项目结构
遵循系统统一规范，包含 core, model, tools, utils, config, tests 目录。

## 3. 核心接口
- `BaseAgent`: 抽象基类
- `SimpleAgent`: 默认实现类
- `execute(task, session_id)`: 执行任务
- `call_agent(request)`: 标准化接口调用

## 4. 使用示例

### 4.1 基础调用
```python
from agent_module.core.impl import SimpleAgent
from agent_module.model.data_model import AgentRequest

# 初始化工具
def rag_search_tool(inp: dict):
    return {"code": "SUCCESS", "message": "ok", "data": {"answer": "检索结果"}}

tools = {"rag_search": rag_search_tool}

# 初始化 Agent
agent = SimpleAgent(tools=tools)

# 调用
response = agent.call_agent(AgentRequest(task="查询知识库"))
```

### 4.2 工具注册
```python
agent.register_tool(
    tool_name="weather_query",
    tool_func=weather_tool,
    description="查询城市天气",
    input_schema={"city": "str"}
)
```

## 5. 依赖项
见 requirements.txt

## 6. 配置说明
在系统全局 config.yaml 中配置 agent 节点：
```yaml
agent:
  max_retries: 3
  timeout: 30
  session_prefix: "agent_session"
```

## 7. 常见问题
- 任务解析不准确：扩展 parse_task_by_rules 函数或接入 LLM
- 工具调用失败：检查工具注册状态和重试配置
- 会话状态丢失：检查状态存储模块配置

---

## ✅ 实现说明

| 特性 | 说明 |
| :--- | :--- |
| **遵循设计文档** | 代码结构、类名、方法名、参数定义完全匹配设计说明书 |
| **依赖管理** | 导入了设计文档中指定的所有依赖模块 |
| **异常处理** | 统一使用 `AgentException`，错误码遵循全局错误码表 |
| **可替换性** | 通过 `BaseAgent` 抽象接口实现模块解耦 |
| **数据模型** | 使用 `dataclass` 定义请求/响应模型，确保类型安全 |
| **测试覆盖** | 提供基础单元测试框架，覆盖正常流程与异常场景 |
| **工具注册** | 支持动态注册/注销工具，扩展性强 |
| **状态持久化** | 自动调用状态存储模块，支持会话延续 |

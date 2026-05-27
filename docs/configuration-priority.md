# 配置优先级与单一入口

> 适用范围: anything 项目所有运行时配置读取
> 状态: 2026-05-27 起生效
> 关联代码: `basic_support/config_module/core/impl.py`

## 1. 为什么需要这份文档

系统当前存在 **5 个配置源**,各自影响某些字段。以前没文档化时,新人想改一个值要试 5 处才能确认哪个真正生效;改动后某个字段的实际表现变得"不可解释"。本文档明确:

- 5 个配置源**分别是什么**
- 它们的**优先级**(谁覆盖谁)
- 推荐的**单一入口**(`ConfigManager.get_effective_value`)
- **不要做什么**(常见反模式)

## 2. 5 个配置源 + 优先级

按优先级从高到低排列。**上层覆盖下层**。

| 层级 | 来源 | 示例 | 适用场景 |
|---|---|---|---|
| **L1** | 环境变量 | `ANYTHING_DEV_MODE=1`<br>`OPENAI_API_KEY=sk-...` | 运行时覆盖、敏感凭证 |
| **L2** | yaml `${...}` 占位 | `api_key: "${OPENAI_API_KEY}"` | yaml 引用环境变量(本质仍是 L1 的另一种写法) |
| **L3** | 模块局部 yaml | `business/rag_module/config/config.yaml` | 模块特有的默认值 |
| **L4** | 全局 yaml | `basic_support/config_module/config/config.yaml` | 系统级默认值 |
| **L5** | impl `__init__` 默认参数 | `def __init__(self, timeout=60):` | 代码兜底,最后防线 |

### 实际生效路径

```
用户传给 SimpleRAG.__init__ 的 timeout 参数
        ↓ 优先用用户传入
ConfigManager.get_config("rag.llm_timeout")  ← 全局 yaml,可被环境变量替换
        ↓ 缺失时用
__init__ 的默认值 (代码兜底)
```

## 3. 推荐入口: `get_effective_value`

`ConfigManager.get_effective_value(key, env_var=None, default=None)` 是显式优先级合并的统一入口。

```python
# 推荐
timeout = self.config.get_effective_value(
    key="rag.llm_timeout",
    env_var="ANYTHING_RAG_TIMEOUT",
    default=30,
)

# 仍然支持的旧风格 (没有环境变量层)
timeout = self.config.get_config("rag.llm_timeout", default=30)
```

### 为什么不直接读 `os.environ`?

- **集中**:让所有"允许环境变量覆盖"的字段都通过 ConfigManager,审计与日志可以集中
- **测试可控**:单测中只 mock ConfigManager 即可,不必 mock 全局 os.environ
- **文档驱动**:`env_var=` 参数本身就是一种"这个字段允许环境变量覆盖"的文档

## 4. 当前已生效的配置字段一览

### 4.1 dev / startup 系列

| 字段 | 默认 | 环境变量 | 影响 |
|---|---|---|---|
| `ANYTHING_DEV_MODE` | 未设 | (本身就是 env) | bootstrap 是否允许静默回退(DummyLLM 等) |

### 4.2 LLM 系列(yaml 配置 + 环境变量占位)

| 字段 | 默认 | 环境变量占位 |
|---|---|---|
| `llm.openai.text-embedding-3-small.api_key` | `${OPENAI_API_KEY}` | `OPENAI_API_KEY` |
| `llm.openai.qwen3.5-flash.api_key` | `${DASHSCOPE_API_KEY}` | `DASHSCOPE_API_KEY` |
| `llm.openai.gpt-4o-mini.api_key` | `${OPENAI_API_KEY}` | `OPENAI_API_KEY` |
| `llm.common.timeout` | 15 | — |
| `llm.common.max_retry` | 1 | — |

### 4.3 RAG 检索系列(yaml + impl 默认参数)

| 字段 | yaml 默认 | impl 默认参数 |
|---|---|---|
| `rag.top_k_retrieve` | 50 | 50 |
| `rag.top_k_rerank` | 8 | 8 |
| `rag.max_context_tokens` | 3000 | 3000 |
| `rag.max_chunk_in_prompt_tokens` | 600 | 600 |
| `rag.enable_rerank` | False | False |
| `rag.enable_rewrite` | False | False |

### 4.4 Agent 系列

| 字段 | yaml 默认 | impl 默认参数 |
|---|---|---|
| `agent.timeout` | 30 | 60 |
| `agent.max_retries` | 3 | 2 |
| `agent.use_llm_planner` | True | True |
| `agent.max_planner_steps` | 3 | 3 |
| `agent.execution_strategy` | "single_shot" | "single_shot" |
| `agent.max_react_iterations` | 5 | 5 |

注意 `agent.timeout` 在 yaml(30)与 impl 默认(60)不同 — yaml 优先生效。这就是为什么 `get_effective_value` 比直接读 `__init__` 默认值更可控。

### 4.5 安全系列

| 字段 | yaml 默认 | 环境变量占位 |
|---|---|---|
| `security.auth_enabled` | True | — |
| `security.api_keys` | `["${API_KEY_1}"]` | `API_KEY_1` |
| `security.jwt_secret` | `${JWT_SECRET}` | `JWT_SECRET` |

## 5. 反模式(不要这样做)

### 5.1 ❌ 散落的 `os.environ.get`

```python
# Bad: 绕过 ConfigManager 直接读环境变量
timeout = int(os.environ.get("MY_TIMEOUT", "30"))
```

问题:这个字段不会出现在配置审计 / 日志中,新人不知道存在。

### 5.2 ❌ 在 `__init__` 写死实际值

```python
# Bad: 把生产值写在代码里,而非配置里
def __init__(self, timeout=300):  # 生产实际用 300, 但只有代码作者知道
    ...
```

问题:运维改这个值时无法通过配置,必须改代码。

### 5.3 ❌ 多次读取同一字段(可能拿到不同值)

```python
# Bad: 热加载场景下两次读可能不一致
def some_method(self):
    if self.config.get_config("rag.top_k") > 10:
        ...
    return self.config.get_config("rag.top_k")  # 中间可能 hot_reload
```

问题:配置热加载时两次读取之间可能值变了,逻辑不一致。建议:`__init__` 时一次性读取并存为 self.xxx。

### 5.4 ❌ 字段名跨模块不一致

```python
# Bad: 同一含义的字段在不同模块用不同名
# config_module: timeout
# rag_module:    llm_timeout
# agent_module:  exec_timeout
```

问题:运维改一个不一定改全。**推荐**:使用层级命名 `rag.llm_timeout` / `agent.execution_timeout`。

## 6. 添加新配置项的清单

1. 在 `basic_support/config_module/config/config.yaml` 加默认值(命名走层级 `module.key`)
2. impl 中通过 `self.config.get_effective_value(...)` 读取
3. 如果允许环境变量覆盖,加 `env_var="ANYTHING_XXX_YYY"`(前缀 `ANYTHING_` 防冲突)
4. 在本文档 §4 对应小节追加字段说明
5. 跑一次 smoke test 确认实际生效

## 7. 调试技巧

### 7.1 看实际生效值

```python
# 临时调试:打印某字段的实际生效值
from config_module import ConfigManager
cfg = ConfigManager()
cfg.load_config()
print("rag.top_k_retrieve =", cfg.get_effective_value("rag.top_k_retrieve", "ANYTHING_TOP_K", 50))
```

### 7.2 看所有配置树

`cfg.get_config("")` 返回全部配置 dict(注意可能含敏感信息,不要在生产日志中输出)。

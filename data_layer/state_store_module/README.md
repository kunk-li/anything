# state_store_module（数据层-状态存储模块）

本模块用于持久化 Agent 运行过程中的会话状态（如会话记忆、任务步骤、工具调用记录等），对外提供 4 个核心接口：

- `save_state(session_id, state) -> bool`
- `get_state(session_id) -> Optional[Dict]`
- `append_event(session_id, event) -> bool`
- `clear_state(session_id) -> bool`

默认实现：`LocalStateStore`，基于本地 JSON 文件存储（开发/测试环境推荐）。

## 目录结构

```
state_store_module/
├── __init__.py
├── core/
│   ├── base.py
│   └── impl.py
├── utils/
│   └── tool_functions.py
├── config/
│   ├── config.py
│   └── config.yaml
└── tests/
    └── test_impl.py
```

## 依赖模块

- `config_module`：读取全局配置（state_store.*）
- `log_module`：记录关键操作日志
- `exception_module`：统一异常基类 `SystemBaseException`

> 外部基础支撑代码已按文档实现时，本模块可直接集成使用。

## 配置项（通过 ConfigManager 读取）

- `state_store.dir`：状态文件存储目录（默认 `state_store`）
- `state_store.expire_hours`：过期清理阈值（小时，默认 24；<=0 或 None 表示不清理）
- `state_store.max_size`：目录容量上限（字节，默认 1GB；None 表示不限制）

## 使用示例

```python
from state_store_module import LocalStateStore

store = LocalStateStore()
sid = "agent_session_001"

store.save_state(sid, {"task": "整理知识库要点", "steps": [], "events": []})
store.append_event(sid, {"type": "task_parse", "data": {"plan": []}})
print(store.get_state(sid))
store.clear_state(sid)
```

## 异常说明

模块内部会抛出 `StateStoreException(SystemBaseException)`，code 建议使用 `STATE_STORE_*` 系列。

**建议补充：**
在全局错误码表与 `exception_module` 中增加 `STATE_STORE_*` 错误码（如 `STATE_STORE_SAVE_FAILED`、`STATE_STORE_APPEND_FAILED` 等），以完全满足“与错误码表严格对应”的规范要求。

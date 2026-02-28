# config_module（配置管理模块）

本模块用于统一管理系统配置（YAML），提供加载、读取、更新（可持久化）、校验、备份/恢复、敏感配置加密、远程配置加载与合并等能力。

> 目录结构与接口规范来自《基础支撑层 - 配置管理模块（config_module）设计文档》。

## 1. 安装依赖

```bash
pip install -r requirements.txt
```

## 2. 快速使用

```python
from config_module import ConfigManager

cm = ConfigManager()
cm.load_config()  # 默认读取 config/config.yaml

env = cm.get_config("global.env")
llm_cfg = cm.get_config("llm.")         # 前缀批量读取
timeout = cm.get_config("agent.timeout", 30)

cm.update_config("llm.temperature", 0.5)          # 仅内存更新
cm.update_config("agent.timeout", 60, persist=True)  # 持久化写入

backup = cm.backup_config()
cm.restore_config(backup)

# 加密敏感配置（会写回文件，文件内以 ENC:: 前缀存储）
cm.encrypt_sensitive_config(
    keys=["vector_db.api_key", "llm.api_key", "security.jwt_secret"],
    secret_key="your_secret"
)

# 远程配置加载并合并（remote 覆盖 local）
cm.load_remote_config("http://your-config-center/config.yaml", config_type="yaml")
```

## 3. 关键特性说明

### 3.1 环境变量注入
配置中形如 `${ENV_NAME}` 的字符串会在加载时用环境变量替换。若环境变量不存在，则保留原字符串。

### 3.2 热加载（无后台线程）
当 `global.hot_reload=true` 时，`get_config` / `update_config` 会按 `global.hot_reload_interval` 秒检测配置文件修改时间，
如有变化则自动重新加载。

### 3.3 校验规则
默认读取 `validate_rules` 进行校验，支持：
- `type`: str/int/float/bool
- `required`: 是否必填
- `range`: 数值范围（[min, max]）或字符串枚举（["a","b"]）

### 3.4 更新日志
每次 `update_config` 会在配置文件同目录下追加 `update.log`，记录更新时间、键、新旧值。

## 4. 运行测试

```bash
python -m unittest -q
```

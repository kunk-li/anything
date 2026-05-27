# Secrets 管理

> 适用范围: 本项目所有运行时 secrets(API keys / JWT / 加密密钥)
> 状态: 2026-05-27 起生效
> 关联代码: `basic_support/deps_module/deps.py:_load_dotenv_if_exists` + `ConfigManager.check_required_secrets`

## 1. Secrets 清单

| 变量 | 必需性 | 作用 | 获取 |
|---|---|---|---|
| `OPENAI_API_KEY` | 生产/staging | OpenAI / OpenAI 兼容 LLM 调用(text-embedding-3-small / gpt-4o-mini) | https://platform.openai.com/api-keys |
| `DASHSCOPE_API_KEY` | 生产/staging | 阿里 DashScope qwen 系列(OpenAI 兼容协议) | https://dashscope.console.aliyun.com/apiKey |
| `VECTOR_DB_API_KEY` | 仅云向量库时 | 切换到 Pinecone/Weaviate/Qdrant Cloud 时填;默认 FAISS 本地不需要 | provider 自有控制台 |
| `JWT_SECRET` | 生产/staging | API 鉴权 JWT 签发/校验密钥 | `openssl rand -hex 32` |
| `API_KEY_1` | 生产/staging | API Key 鉴权(`security.auth_type=apikey` 时)| 自行生成长随机串 |
| `SENSITIVE_CONFIG_SECRET` | 生产 | `ConfigManager.encrypt_sensitive_config` 对称加密 yaml 字段时用 | `openssl rand -base64 32` |

**dev 环境**:所有 secrets 都不强制必需 — `ANYTHING_DEV_MODE=1` 时,任一未设的 secrets 都只是 WARN,LLM 失败自动回退 DummyLLMClient。

## 2. 启动期校验

`bootstrap.build_basic_deps()` 启动时自动扫描 yaml 中所有 `${XXX}` 占位符,检测是否对应环境变量已设。

- **dev 模式**(`ANYTHING_DEV_MODE=1`):未设的 secrets 列出 WARN,继续启动
- **生产模式**(默认):未设即抛 `StartupError`,系统拒绝启动

```
[bootstrap][DEV_MODE] 以下 secrets 未在环境变量中提供
(yaml 占位符 ${XXX} 未被替换):
DASHSCOPE_API_KEY, JWT_SECRET, OPENAI_API_KEY
```

## 3. 本地开发

```bash
# 1) 复制模板
cp .env.example .env

# 2) 填入真实值
vim .env

# 3) 启动 — bootstrap 自动加载 .env
cd run
PYTHONPATH=... python run_smoke_test.py
```

**重要**:
- `.env` 已在 `.gitignore`,不会被提交
- 现有环境变量优先于 `.env`(env > .env)
- 一切失败都静默忽略 `.env`(它是可选机制)

## 4. GitHub Actions

### 4.1 配置 secrets

`Repo Settings` → `Secrets and variables` → `Actions` → `New repository secret`

逐个添加上表中需要的变量。

### 4.2 在 workflow 中引用

```yaml
# .github/workflows/your-workflow.yml
jobs:
  some-job:
    runs-on: ubuntu-latest
    env:
      OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
      DASHSCOPE_API_KEY: ${{ secrets.DASHSCOPE_API_KEY }}
      JWT_SECRET: ${{ secrets.JWT_SECRET }}
      # 生产模式不能开 dev_mode
      # ANYTHING_DEV_MODE 故意不设
    steps:
      - ...
```

### 4.3 当前 `ci.yml` 的策略

主 CI 设置了 `ANYTHING_DEV_MODE: "1"`,所以**不需要任何 secrets** 也能跑通(LLM 走 DummyLLMClient,vector_db FAISS 本地无凭证)。

**业务质量评测 nightly workflow**(见 Task #31) 才需要配 secrets。

## 5. 多环境切换

推荐 `ANYTHING_ENV` 环境变量区分:

| ANYTHING_ENV | DEV_MODE | LLM | 用途 |
|---|---|---|---|
| `dev` (默认) | 1 | Dummy 兜底 OK | 本地开发 / 单测 / CI 主线 |
| `staging` | 0 | 真实 API key 必需 | 预发布验证 / nightly 评测 |
| `prod` | 0 | 真实 API key 必需 | 生产服务 |

当前实现仅识别 `ANYTHING_DEV_MODE`;`ANYTHING_ENV` 是规划中的扩展(可通过 `ConfigManager.get_effective_value` 路由不同 yaml)。

## 6. 反模式

### 6.1 ❌ 提交 .env 文件

`.gitignore` 已挡,但仍要注意:不要在 commit message / log / 注释里贴 secrets。

### 6.2 ❌ 在代码里写死 secrets

```python
# Bad
api_key = "sk-xxx..."
```

任何 hardcoded secrets 都是泄漏隐患。统一走 `os.environ.get` 或 `ConfigManager.get_effective_value(env_var=...)`。

### 6.3 ❌ 日志打印 secrets

`SystemLogger` 默认不脱敏,业务代码不要把 `api_key` / `jwt` 整体 log。

```python
# Bad
self.logger.info(f"using api_key={api_key}")

# OK
self.logger.info(f"using api_key=***{api_key[-4:]} (last 4)")
```

### 6.4 ❌ ConfigManager.get_config 直接读 secrets 字段并日志输出

读出来的值如果是 `${OPENAI_API_KEY}` 形态(未替换),代表 env 没设;
打印这种值会暴露你的 yaml 配置结构(虽然不暴露真实 key)。

## 7. 故障排查

### 7.1 启动报 `StartupError: secrets`

```
[startup] secrets 初始化失败:
以下 secrets 未在环境变量中提供 ...
```

检查:
1. `.env` 是否存在且有对应字段
2. `printenv | grep OPENAI` 看真实 env 是否设了
3. 如果只是本地调试,可临时 `export ANYTHING_DEV_MODE=1`

### 7.2 LLM 调用仍 401(secrets 已设)

可能原因:
- key 本身错(去 provider 控制台验证)
- key 缺权限(某些 key 限单一模型)
- 配置的 `api_base` 跟 key 不匹配(如 DashScope key 用了 OpenAI api_base)

### 7.3 不知道哪些字段是 secrets

```bash
# 扫描所有 ${...} 占位
grep -rEo '\$\{[A-Z0-9_]+\}' basic_support/config_module/config/config.yaml | sort -u
```

## 8. 后续改进(规划中)

- Task #31:nightly workflow 配 secrets 跑业务质量评测
- 集成 HashiCorp Vault / AWS Secrets Manager(替代 .env)
- Secret rotation 自动化(API key 过期前自动轮换)
- `ConfigManager.encrypt_sensitive_config` 在 yaml 内 inline 加密(已存在但未广泛使用)

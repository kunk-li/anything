# 生产上线 Checklist

把散在 `.env.example` 注释 / `docs/` 里的"上线前必做"汇总成一页可勾选清单。
**本地试用**不用看这页 — 直接按 [README 快速开始](README.md#快速开始)（dev sentinel 模式零配置开箱即用）。
**对外部署**请逐项过下面的清单。

---

## 1. 前置

- [ ] Python 3.12+，`pip install -r requirements.txt`（或用 Docker，见 §5）
- [ ] 至少一个 LLM provider key：DashScope / OpenAI / Anthropic，或本地 Ollama（免 key）

## 2. 安全配置（生产必做 — 不做会暴露风险）

| 环境变量 | dev 默认值 | 生产必须 |
|---|---|---|
| `ANYTHING_DEV_MODE` | `1`（依赖失败回退 + 自动关 auth） | **unset 或 `0`** — 否则 fail-fast 关闭，鉴权可能被绕过 |
| `API_KEY_1` | `dev_api_key_1_change_in_prod`（sentinel） | **改成真实强随机值**：`openssl rand -hex 32` |
| `JWT_SECRET` | `dev_jwt_secret_change_in_prod` | `openssl rand -hex 32` |
| `SENSITIVE_CONFIG_SECRET` | `dev_sensitive_secret_change_in_prod` | `openssl rand -base64 32` |

> ⚠️ **最易踩的坑**：dev sentinel 的 `API_KEY_1` 会**自动关闭鉴权**便于本地试用。
> 只要生产环境的 `API_KEY_1` 还是 sentinel 值，任何人都能无鉴权调用你的 API。上线前务必改掉。

## 3. 网关 / 反向代理（强烈建议）

- [ ] 屏蔽或额外鉴权保护 `/config/*`（运行期注册模型 / key，敏感）
- [ ] 屏蔽或额外鉴权保护 `/admin/*`（运维状态）
- [ ] HTTPS / TLS 终止
- [ ] 限制请求体大小（防大文件上传 OOM）

## 4. 多租户 / 配额（对外多用户场景）

- [ ] 配 per-tenant USD 配额 + rate limit（见 [docs/multi-tenancy-design.md](docs/multi-tenancy-design.md)）
- [ ] 确认 `state_store` 后端：默认 SQLite（单机够用）；多实例 / 高并发换 Redis（跨进程 Backend 抽象已就位）

## 5. 启动

```bash
# 方式 A：Docker（推荐）
docker-compose up -d

# 方式 B：直接启动（README 同款入口，生产记得带上安全 env）
ANYTHING_DEV_MODE=0 \
API_KEY_1="$(openssl rand -hex 32)" \
DASHSCOPE_API_KEY=sk-xxx \
python -m application.api_service_module.bootstrap
```

## 6. 上线后验证（冒烟）

- [ ] `curl https://<域名>/health` → `{"status":"UP"}`，且 `dependencies` 全 UP
- [ ] 配了真 key 后，前端发一条 RAG / Agent 消息能出**真实答案**（非 `[stub]` 占位）
- [ ] RAG 模式问知识库里**没有**的内容 → 应答"没有找到相关内容"（不编造，A1 行为）
- [ ] 用真实 `API_KEY_1` 调用需带 `X-API-Key` 头；不带应 401

## 7. 数据持久化 / 备份

- [ ] 挂持久化卷：向量索引、`document_store`、`state_store`、审计日志（JSONL append-only）
- [ ] 定期备份上述数据目录

---

**相关文档**：[secrets-management](docs/secrets-management.md) ·
[configuration-priority](docs/configuration-priority.md) ·
[multi-tenancy-design](docs/multi-tenancy-design.md) ·
[模块设计说明书 doc/](doc/)

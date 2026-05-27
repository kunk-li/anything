# 多租户隔离设计文档

> 文档版本:**v0.2**(基于 v0.1 评审意见修订)
> 最后更新:2026-05-27
> 关联 Task:#33
> 状态:**待二次评审**

## 0. 文档目的

把"多租户隔离"从模糊概念落地为可执行设计,在动代码之前明确:

- 我们解决什么问题、不解决什么问题
- 数据/请求/认证/计费 4 个维度的隔离策略
- 跟现有单租户系统的兼容路径
- 实施分步路线 + 风险点

读者:架构 reviewer、运维、安全、产品。

## 0.1 修订记录

| 版本 | 日期 | 修订点 | 评审来源 |
|---|---|---|---|
| v0.2 | 2026-05-27 | C1: api_keys 配置反转为 tenant→keys 形式;C2: 新增 §4.3 tenant_id 冲突处理;M1: §10 补写路径策略;M2: §10 补停机/原子迁移/回滚;M3: 错误码 CROSS_TENANT_FORBIDDEN 合并到 404;M4: QUOTA 错误码细分;M5: §7.2 cardinality 上限;M6: §13.1.4 FAISS 实例生命周期;M7: PR3/PR4 各拆 2 个共 6 个 PR;Minor 1-8 全部落地 | v0.1 评审报告 |
| v0.1 | 2026-05-27 | 初版设计草案 | — |

---

## 1. 目标与非目标

### 1.1 目标

- **应用层数据隔离**:进程内任何数据访问路径(检索/上传/state)都必须显式带 tenant_id;不依赖 OS/容器级隔离(由部署层另外保证)
- **请求级 tenant_id 显式传递**:贯穿 ApiService → RequestHandler → Orchestrator → RAG/Agent → 数据层,**有显式默认值 `default`** 而非"无声落入某个租户"
- **认证驱动租户绑定**:API key / JWT 解析后自动绑定到 `tenant_id`,**认证产物优先于请求体声明**(详见 §4.3)
- **审计与可观察性**:每条日志 / metric / OTel span 都携带 tenant_id 标签,但 metrics cardinality 受控(详见 §7.2)
- **API 与 yaml 配置向后兼容**:
  - 老请求不传 tenant_id → 落入 `default` 租户(读写都走 default 子目录)
  - 老 list 形式 `api_keys` 配置 → 自动映射到 default,启动期 WARN 一次
  - **数据迁移需要一次性运维操作**(详见 §10)

### 1.2 非目标(本期不做)

- **跨租户共享数据**(如租户 A 把文档分享给租户 B)— 留给后续 ACL 设计
- **租户级别的精细计费/账单** — 本期只做配额硬限,不出账单
- **租户级别的模型路由**(租户 A 用 GPT-4 / 租户 B 用 Qwen)— 留给后续 model gateway 设计;**注**:本期 yaml `api_keys` 结构虽不直接支持,但易扩展(§5.2)
- **跨地域数据驻留**(GDPR 合规)— 留给后续 region routing
- **租户管理控制台 UI**
- **OS / 容器级别强隔离**(假设由 K8s namespace / cgroup 在部署层保证)

---

## 2. 租户模型

### 2.1 标识与命名

```
tenant_id: str
  - 长度 3-32 字符
  - 字符集: [a-z0-9_-]  (严格 ASCII,防 path traversal,见 §9.2)
  - 全局唯一, 不可重命名
  - 保留前缀 "_system_" 用于内部巡检 / 审计任务(用户不可用)
  - 保留 ID "default" 仅用于向后兼容老请求(新租户不应主动用此 ID)
```

#### 2.1.1 alias 映射(给"客户名 != tenant_id"的场景)

客户名 "Acme Corp" 直接当 tenant_id 不合法。alias 映射放在认证层:

```yaml
security:
  tenant_aliases:
    "Acme Corp":      "acme-corp"
    "客户A":          "client-a-cn"
    "acme_corp":      "acme-corp"   # 大写折叠 + 下划线规范化
```

ApiService 在认证后查 `tenant_aliases` 表,把客户友好名映射为合法 tenant_id。映射表唯一性由运维保证。

#### 2.1.2 分配机制

- **本期手工分配**:运维通过更新 `security.api_keys` yaml + secrets 入驻新租户
- **未来**:可在 ApiService 加 `POST /admin/tenants` 端点,自动生成 nanoid(8 字符)作为 tenant_id

### 2.2 租户层级:扁平,不嵌套

**决策:扁平**,不支持嵌套(子租户 / 部门)。

理由:嵌套会让 ACL 复杂度爆炸,且当前没有具体业务需求驱动。

#### 2.2.1 扁平模型的已知限制 + 临时解决方案

| 痛点场景 | 临时方案 |
|---|---|
| 集团 + 子公司想数据共享 | 显式建 `shared-<group>` 租户,业务代码主动用该 tenant_id 写共享文档 |
| 多部门想统一计费但数据隔离 | 业务层自己做"部门 → tenant_id"维度表,**本系统不感知** |
| 系统级公共知识库(所有租户能查) | 写到 `_system_public` 租户,RAG 检索时显式查两个租户(自当前 + `_system_public`)— 需要在 SimpleRAG 加 `secondary_tenants` 参数(本期不实施,留作扩展点) |

后续若有强需求,可在 `tenant_id` 上加 `parent_tenant_id` 演进为树形;本期不预留字段。

---

## 3. 数据隔离方案

### 3.1 三个数据存储 + 隔离策略

| 存储 | 当前实现 | 隔离方案 | 备注 |
|---|---|---|---|
| 向量库 `FaissVectorDB` | 单 `vector_store/` 目录 | **目录分区**:`vector_store/<tenant_id>/` | FAISS 后置 filter 会损失召回率(top_k 召回后过滤可能剩 0),目录分区更稳 |
| 文档库 `LocalDocumentStore` | 单 `documents/` 目录 | **目录分区**:`documents/<tenant_id>/` | 跟向量库一致 |
| 状态存储 `LocalStateStore` | 单 `state_store/` + `<session_id>.json` | **路径分区**:`state_store/<tenant_id>/<session_id>.json` | session_id 仍全局唯一(由接口层补齐) |

### 3.2 为什么选目录分区而非 metadata filter?

- **召回率**:FAISS IndexFlatIP top_k 召回后做 metadata 过滤,可能剩 0 个匹配 — 必须 oversample 极多倍才能保住实际召回数,反而更慢
- **故障域**:单租户硬件故障 / 数据损坏不污染其他租户
- **删除合规**:`rm -rf vector_store/<tenant_id>` 即满足 GDPR "right to be forgotten"
- **内存可预测**:单租户独立 IndexFlatIP,内存上限可独立约束

**代价**:跨租户共享数据需显式建 `shared` 租户(§2.2.1)。

### 3.3 切换到云向量库时的策略

Pinecone / Weaviate / Qdrant Cloud 都原生支持 namespace / collection 分区:
- 本设计 `tenant_id` 透传链路不变
- 仅 `vector_db.upsert/query` 内部把 tenant_id 翻译为对应 namespace

### 3.4 未来扩展点(本期不做)

| 扩展 | 方案预留 |
|---|---|
| BM25 全文索引 | 跟向量库一样按 tenant_id 切目录,加 `text_index_module` |
| Embedding 缓存(同 query 不重算) | 缓存 key 含 tenant_id,跨租户不共享 |
| 跨租户查询 | SimpleRAG 加 `secondary_tenants: List[str]` 参数 |

---

## 4. 请求路径中的 tenant_id 透传

### 4.1 数据流

```
[HTTP Request]
   ↓ X-API-Key / Authorization: Bearer <jwt>
[ApiService middleware]                ← 解析认证,得 auth_tenant_id;设到 request.state.tenant_id
   ↓ (默认主路径) 把 auth_tenant_id 注入 body
[RequestHandler._standardize_request]  ← 单层补齐;若上游既给又冲突,见 §4.3
   ↓ {tenant_id, type, query/task, ...}
[SimpleOrchestrator.route]
   ↓ 透传
[SimpleRAG.retrieve / SimpleAgent.execute]
   ↓ 只读不生成,缺失记 ERROR (contract violation)
[FaissVectorDB(tenant_id=...).query]   ← 数据层根据 tenant_id 切目录
```

### 4.2 RequestEnvelope schema 扩展

`basic_support/schema_module/schema.py` 中 `RequestEnvelope` 增加字段:

```python
class RequestEnvelope(BaseModel):
    type: Literal["rag", "agent", "hybrid"] = "rag"
    query: Optional[str] = None
    task: Optional[str] = None
    session_id: Optional[str] = None
    tenant_id: Optional[str] = None  # ← 新增, 默认走 default
    top_k: int = Field(default=5, ge=1, le=50)
    trace_id: Optional[str] = None
    extra_params: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("tenant_id")
    @classmethod
    def _validate_tenant_id(cls, v):
        if v is None:
            return None
        if not re.match(r"^[a-z0-9_-]{3,32}$", v):
            raise ValueError("tenant_id 必须是 3-32 位 [a-z0-9_-]")
        return v
```

`extra: ignore` 已经配,**老调用方不传 tenant_id 完全无感**。

### 4.3 ⚠️ tenant_id 冲突处理(从 v0.2 新增)

ApiService 处理请求时,可能同时遇到两个 tenant_id 来源:

1. 认证产物(从 API key 或 JWT claim 推导出来的 `auth_tenant_id`)
2. 请求体声明(`body["tenant_id"]`)

**冲突规则**(强制):

| 情况 | 决议 | 副作用 |
|---|---|---|
| 仅 auth_tenant_id 存在 | 用 auth_tenant_id | 无 |
| 仅 body tenant_id 存在(认证未携带) | **仅当请求来源 IP 在 internal_whitelist 中**才采纳 body | 否则视为 `TENANT_REQUIRED` 401 |
| 两者一致 | 用 auth_tenant_id(或 body,等价) | 无 |
| **两者不一致** | **认证产物赢**,记 ERROR 日志 + metrics counter 计数 | 视为疑似越权,触发监控告警 |

```python
# 伪代码
auth_tid = self._resolve_tenant_from_auth(request)   # 可能 None
body_tid = body.get("tenant_id")                      # 可能 None

if auth_tid:
    final = auth_tid
    if body_tid and body_tid != auth_tid:
        self.logger.error(
            f"[security] tenant_id mismatch: auth={auth_tid} body={body_tid} "
            f"trace_id={trace_id} -- 疑似越权尝试,已使用 auth_tid"
        )
        # metric: anything_tenant_mismatch_total{auth=auth_tid,body=body_tid} +1
elif body_tid and self._is_internal_ip(request):
    final = body_tid  # 仅内部服务可这样
else:
    return 401 TENANT_REQUIRED
```

**单测必须覆盖**:冲突时 final 永远等于 auth_tid,且 ERROR 日志真正打出。

### 4.4 单层补齐契约

`RequestHandler._standardize_request` 是 tenant_id 的**单一补齐点**:
- 上游(ApiService)已认证 → body 必带 tenant_id,handler 直通
- 直接调 RequestHandler(不走 ApiService,如 smoke test)→ handler 补 `default`

下游(Orchestrator/RAG/Agent/数据层)只读、不生成、不兜底,缺失立刻 `[contract violation]` ERROR(跟 trace_id / session_id 相同的契约模式,见 Task #15)。

---

## 5. 认证驱动租户绑定

### 5.1 三种认证方式 → tenant_id 来源

| 认证方式 | tenant_id 来源 | 备注 |
|---|---|---|
| API Key(主路径) | 查 `api_keys` 配置反向映射 | 见 §5.2 |
| JWT | 解析 JWT 的 `tenant_id` claim | claim 名可配 |
| 显式 X-Tenant-Id header | 仅 internal_whitelist IP 允许 | 见 §4.3 |

### 5.2 配置 schema(C1 修订:tenant → keys 形式)

```yaml
security:
  auth_enabled: true
  auth_type: "apikey"

  # 新格式: tenant_id -> list of api_keys
  # 反向映射 key -> tenant_id 在 ApiService 启动时构造内存索引
  # ${} 占位在 list value 内, substitute_env 能正确替换 (避免 v0.1 的 dict key 替换陷阱)
  api_keys:
    default:
      - "${API_KEY_INTERNAL}"
    tenant-a:
      - "${API_KEY_TENANT_A_PRIMARY}"
      - "${API_KEY_TENANT_A_BACKUP}"
    tenant-b:
      - "${API_KEY_TENANT_B}"

  # 老 list 格式仍兼容: 全部映射到 default
  # api_keys:
  #   - "${API_KEY_1}"
  #   - "${API_KEY_2}"

  jwt:
    secret: "${JWT_SECRET}"
    tenant_id_claim: "tenant_id"

  internal_whitelist:
    - "10.0.0.0/8"
    - "127.0.0.1"

  tenant_aliases:                  # 见 §2.1.1
    "Acme Corp": "acme-corp"
```

### 5.3 旧版 list 格式兼容 + WARN 日志

ApiService 启动时检测 `api_keys` 是 list 还是 dict:

```python
if isinstance(api_keys_config, list):
    # 老格式: 全部映射 default
    self.logger.warning(
        "[security] detected legacy api_keys list format; "
        f"all {len(api_keys_config)} keys mapped to tenant='default'. "
        "Multi-tenancy disabled. Migrate to tenant->keys dict to enable."
    )
    self._key_to_tenant = {k: "default" for k in api_keys_config}
elif isinstance(api_keys_config, dict):
    self._key_to_tenant = {
        key: tid for tid, keys in api_keys_config.items() for key in keys
    }
```

**仅启动期 WARN 一次**,不在请求路径反复输出。

---

## 6. 配额与限流(本期最小可用)

### 6.1 配额维度

| 维度 | 单位 | 配置位置 | 错误码 |
|---|---|---|---|
| 向量库文档数 | 个 | `quotas.<tenant_id>.max_documents` | `QUOTA_DOC_EXCEEDED` 429 |
| 向量库存储 | MB | `quotas.<tenant_id>.max_vector_store_mb` | `QUOTA_STORAGE_EXCEEDED` 429 |
| API QPS | req/s | `quotas.<tenant_id>.max_qps` | `API_RATE_LIMITED` 429(沿用现有) |
| 单次 top_k | int | RequestEnvelope `top_k` 字段(已 ≤50) | `PARAM_INVALID` |

**注**:`QUOTA_EXCEEDED` 一字总称已废除(v0.1 评审 M4),按维度细分。

### 6.2 默认值策略

**无显式 quota 配置的租户拒绝创建**(运维必须显式分配):

```yaml
quotas:
  # 必须显式列出每个租户; 找不到 -> 启动期 fail
  default:
    max_documents: 100000
    max_vector_store_mb: 8192
    max_qps: 50
  tenant-a:
    max_documents: 10000
    max_vector_store_mb: 1024
    max_qps: 10
```

启动期 `ConfigManager` 校验:`api_keys` 中所有 tenant_id 都必须在 `quotas` 配置中,否则 StartupError。

### 6.3 实施位置

- **文档数 / 存储空间**:`LocalDocumentStore.save_document` / `FaissVectorDB.upsert_vectors` 写入前检查(读 metadata 计数器,**假设单 process 内并发安全**;多 worker 部署需引入 Redis 计数器,留作 §13 风险)
- **QPS**:ApiService middleware 滑动窗口(参考 `slowapi` 库或自实现);多 worker 部署效果会变形(详见 §13.1.5)
- **超限响应**:HTTP 429 + `Retry-After` header + 对应业务错误码

---

## 7. 审计与可观察性

### 7.1 日志

`SystemLogger` 格式增加 `tenant_id` 字段:

```
2026-05-27 12:34:56 INFO [trace=t1 tenant=tenant-a session=s1] RAG 检索开始
```

具体由 `logger_name` 模板或 `extra` 字典传入,本期不强制改 SystemLogger 内部格式 — 各 impl 在 log message 里显式带 tenant_id 字符串即可。

### 7.2 Metrics + cardinality 守护(M5 修订)

`ApiService._record_metrics` 增加 tenant 维度,但**有上限**:

```
anything_requests_total{type="rag",tenant="tenant-a"} 42
anything_requests_total{type="rag",tenant="other"} 17   # 超 top_n 后聚合
```

配置项 `observability.metrics_tenant_label_top_n`(默认 500):
- 启动期统计 `api_keys` 中所有合法租户数
- 超过 top_n → 实际 metrics label 只用 top_n 最活跃租户,其余标 `tenant="other"`
- 启动期 INFO 日志 "tenant cardinality=N, threshold=500, using top-N strategy"

避免 Prometheus / VictoriaMetrics 内存爆炸(10K 租户 × 10 种 type = 100K 时间序列)。

### 7.3 OpenTelemetry(扩展 Task #34)

`trace_span` 的 attributes 自动带 `tenant_id`,通过 `contextvars.ContextVar`:

#### 7.3.1 ContextVar 生命周期细节(Minor 7 修订)

```python
# basic_support/observability_module/tracing.py
import contextvars
_current_tenant: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "anything_tenant_id", default=None
)

def set_current_tenant(tenant_id: str) -> contextvars.Token:
    return _current_tenant.set(tenant_id)

def reset_current_tenant(token: contextvars.Token) -> None:
    _current_tenant.reset(token)

# trace_span 内部
@contextmanager
def trace_span(name, attributes=None):
    attrs = dict(attributes or {})
    tid = _current_tenant.get()
    if tid:
        attrs.setdefault("anything.tenant_id", tid)
    ...
```

**使用约束**:
- **set 必须在 ApiService middleware 认证后**,把 token 存到 `request.state.tenant_token`
- **reset 必须在 response 返回前**(无论成功失败),配合 `try/finally`
- **不要跨请求复用** — `contextvars.ContextVar` 在 ASGI 多请求场景下每个请求是独立 Context,但若手工传 task 到 thread pool 需注意

单测必须覆盖:
- 请求 A set tenant_a → 请求 B 不应见到 tenant_a
- 异常路径下 reset 仍被执行

---

## 8. 错误码(M3 + M4 修订)

新增 3 个错误码(`CROSS_TENANT_FORBIDDEN` 合并到 §9.3 404):

| code | HTTP | retryable | 场景 |
|---|---|---|---|
| `TENANT_REQUIRED` | 401 | no | 认证失败 / 没传 tenant_id 且非内部 IP |
| `TENANT_NOT_FOUND` | 404 | no | tenant_id 格式合法但不在配置 |
| `QUOTA_DOC_EXCEEDED` | 429 | yes | 文档数超限 |
| `QUOTA_STORAGE_EXCEEDED` | 429 | yes | 存储空间超限 |

**沿用现有 `API_RATE_LIMITED` 429** 表达 QPS 限流(不再单独加 QPS_EXCEEDED)。

**跨租户访问保护**:走 `DOCUMENT_NOT_FOUND` 404 而非单独错误码(§9.3 详述,**统一不区分"存在但无权"和"不存在"**,防租户枚举)。

---

## 9. 安全考虑

### 9.1 防越权读取(扩展到 chunk 级)

- `BaseDocumentStore.get_document(doc_id, tenant_id)`:签名扩展,tenant 不匹配返回 `DOCUMENT_NOT_FOUND` 404
- **chunk 级别**:`SimpleRAG._try_resolve_content_from_doc_store` 等任何用 doc_id 反查 content 的路径,都必须带 tenant_id 校验
- RAG 检索的 vector_db 已按目录分区,理论上不会返回他租户的 chunk;但作为防御,chunk metadata 必须包含 tenant_id,SimpleRAG 在 `_normalize_retrieved_item` 时**断言**:若 chunk.tenant_id ≠ 请求 tenant_id,丢弃该 chunk + ERROR 日志(防分区 bug 漏防御)

### 9.2 防 path traversal

`tenant_id` 在拼接到文件路径前强制白名单:

```python
import re
_TENANT_ID_PATTERN = re.compile(r"^[a-z0-9_-]{3,32}$")

def _validate_and_join(base: Path, tenant_id: str) -> Path:
    if not _TENANT_ID_PATTERN.match(tenant_id):
        raise ValueError(f"invalid tenant_id: {tenant_id!r}")
    return base / tenant_id  # 安全:已通过正则白名单
```

**ASCII-only** 字符集,拒绝 unicode `\w`(防 Punycode / Homoglyph 路径攻击)。

### 9.3 防租户枚举(统一 404)

任何"租户不存在"或"租户存在但当前请求无权访问"的场景,**统一返回 `DOCUMENT_NOT_FOUND` 404** 或 `TENANT_NOT_FOUND` 404,details 字段不暴露存在性:

```json
// 不要这样:
{ "code": "CROSS_TENANT_FORBIDDEN", "details": { "exists": true, "owner": "tenant-b" } }

// 要这样:
{ "code": "DOCUMENT_NOT_FOUND", "details": { "doc_id": "..." } }
```

### 9.4 租户生命周期管理(Minor 4 新增)

| 操作 | 在线请求处理 |
|---|---|
| 创建新 tenant | 配置 reload 后立即生效;无并发问题 |
| 修改 tenant quota | 配置 reload;in-flight 请求按修改前 quota,新请求按修改后 |
| 删除 tenant | **两步走**:① 标记 deactivated(新请求 401 `TENANT_REQUIRED`);② 24h 后真删数据(等 in-flight 自然结束);**禁止热删**(可能 segfault) |

实施在 ConfigManager 加 `tenant_status` 字段:`active / deactivated`。

---

## 10. 迁移路径(M1 + M2 修订)

### 10.1 现状

`run/vector_store/`、`run/documents/`、`run/state_store/` 都是扁平目录。

### 10.2 升级步骤

#### Step 1: 代码上线(本期实施)

数据层 impl 改造后:
- **写**:永远写到 `<base_dir>/<tenant_id>/...` 子目录 — **不再写老扁平路径**
- **读**:双重路径
  1. 先查 `<base_dir>/<tenant_id>/...`
  2. 找不到 fallback `<base_dir>/...`(老扁平,**仅 tenant=default 时才 fallback**,其他租户禁止)
- 上线后即可处理新写入到子目录,读老数据走 fallback

**关键约束**:Step 1 上线后,**任何代码不允许再写老扁平路径**(防止"老路径文件比子目录新"的脏数据)。

#### Step 2: 运维一次性数据迁移(需停机或 blue-green)

⚠️ **不可在线 mv**:vector_store 由 `faiss.index` + `embeddings.npy` + `meta.json` 三件套组成,在线 mv 会让请求读到"半新半旧"中间态。

提供 `scripts/migrate_to_tenant_dirs.sh`:

```bash
#!/usr/bin/env bash
# 用法:
#   1. 停 ApiService / Console (uvicorn 进程)
#   2. bash scripts/migrate_to_tenant_dirs.sh
#   3. 验证 ls run/vector_store/default/ 有 faiss.index 等三件套
#   4. 重启服务
set -euo pipefail

BASE="${1:-run}"
TARGET_TENANT="${2:-default}"

for sub in vector_store documents state_store; do
    src="$BASE/$sub"
    dst="$src/$TARGET_TENANT"
    [ ! -d "$src" ] && continue
    mkdir -p "$dst"
    # 用 cp -a + rm 而非 mv, 防止跨设备 fail; 单租户场景文件量不大可接受
    find "$src" -maxdepth 1 -mindepth 1 ! -name "$TARGET_TENANT" \
        -exec cp -a {} "$dst/" \;
    find "$src" -maxdepth 1 -mindepth 1 ! -name "$TARGET_TENANT" \
        -exec rm -rf {} \;
    echo "migrated $sub -> $dst"
done
```

#### Step 3: 回滚步骤(Step 2 完成后 24h 内)

如果迁移后发现 bug,回滚:

```bash
# scripts/rollback_tenant_dirs.sh
set -euo pipefail
BASE="${1:-run}"
SRC_TENANT="${2:-default}"

for sub in vector_store documents state_store; do
    src="$BASE/$sub/$SRC_TENANT"
    dst="$BASE/$sub"
    [ ! -d "$src" ] && continue
    find "$src" -maxdepth 1 -mindepth 1 -exec cp -a {} "$dst/" \;
    rm -rf "$src"
    echo "rolled back $sub"
done
```

#### Step 4: 移除 fallback 逻辑(下个版本,Step 2 验证 1 周后)

数据层 impl 移除"fallback 老路径"分支,强制 tenant_id 隔离。**只有 tenant=default 才使用过 fallback,删除前需扫描确认 `<base_dir>/` 根目录已无残留文件**。

---

## 11. 实施分步路线(M7 修订:4 PR → 6 PR)

按风险递增,每个 PR 都可独立 review/merge/rollback:

### PR 1: 字段透传(零行为变化)
- `RequestEnvelope` 加 `tenant_id` 字段(默认 `None` → 由 RequestHandler 补 `default`)
- `RequestHandler._standardize_request` 补齐
- `extra: ignore` 已保证老请求不被拒
- **单测**:不传 tenant_id 走 default;传非法格式触发 PARAM_INVALID
- **数据层**:暂不分目录,所有租户共享 `default` 物理空间

### PR 2: 认证 → 租户绑定
- `ApiService._check_auth` 扩展,`tenant → keys` dict 形式 → 反向映射 key→tenant
- 老 list 形式自动映射到 default + 启动 WARN
- ApiService middleware 处理 §4.3 冲突规则(认证产物优先)
- 单测:dict 形式正常解析、list 形式兼容、冲突时记 ERROR

### PR 3a: 数据层 impl 改造(接口扩展,行为不变)
- `FaissVectorDB(tenant_id=...)` 实例化时绑定 tenant
- `LocalDocumentStore(tenant_id=...)` 同理
- `LocalStateStore(tenant_id=...)` 同理
- **实际目录仍走单一路径**(为 PR 3b 做准备)
- 单测:接口签名变化不破坏现有测试

### PR 3b: 数据层目录分区 + 数据迁移脚本
- 实际 ops 走 `<base_dir>/<tenant_id>/` 子目录
- 双重读路径(default 租户读 fallback 老路径)
- bootstrap 按 tenant 缓存数据层实例(参考 §13.1.4)
- `scripts/migrate_to_tenant_dirs.sh` + `rollback_tenant_dirs.sh`
- 集成测试:tenant A 写 → tenant B 读应 0 命中

### PR 4a: 审计(日志 + Metrics + OTel 标签)
- SystemLogger 在关键日志里带 tenant_id 字符串
- `_record_metrics` 加 tenant 标签 + top_n cardinality 守护
- `trace_span` 加 ContextVar tenant 自动注入 attributes(§7.3.1)
- 单测:ContextVar set/reset 跨请求隔离

### PR 4b: 配额硬限 + 新错误码
- 4 个错误码:`TENANT_REQUIRED` / `TENANT_NOT_FOUND` / `QUOTA_DOC_EXCEEDED` / `QUOTA_STORAGE_EXCEEDED`
- `LocalDocumentStore.save_document` 写入前查 `quotas.<tid>.max_documents`
- `FaissVectorDB.upsert_vectors` 写入前查 max_vector_store_mb
- ApiService middleware QPS 滑动窗口
- 单测:每个配额维度独立的"超限触发错误码"用例

**总计 6 PR ~10 人天**,可分 2 个 sprint。

---

## 12. 测试策略

### 12.1 单元测试

每个 PR 自带的单测见 §11。重点单测列表:

| 单测 | 防御对象 |
|---|---|
| `test_tenant_id_validation` | §4.2 schema 字符集 |
| `test_tenant_id_conflict_auth_wins` | §4.3 认证产物优先 |
| `test_apikey_dict_format_parses` | §5.2 新配置格式 |
| `test_apikey_list_format_backward_compat` | §5.3 老格式兼容 |
| `test_data_isolation_cross_tenant_zero_hit` | §3.1 物理目录分区 |
| `test_chunk_tenant_check_in_normalize` | §9.1 防御 chunk 越权 |
| `test_path_traversal_blocked` | §9.2 字符集白名单 |
| `test_quota_doc_exceeded_returns_429` | §6 配额错误码 |
| `test_contextvar_tenant_isolated_across_requests` | §7.3.1 ContextVar 隔离 |
| `test_metrics_cardinality_top_n_aggregation` | §7.2 cardinality 守护 |

### 12.2 集成测试

新增 `tests/integration/test_multi_tenancy.py`:

- Tenant A 上传文档 → Tenant B 检索应 0 命中
- Tenant A 创建 session → Tenant B 用同 session_id 查询应 404
- 跨租户 doc_id 直接访问应返回 404(不是 403,防枚举)
- 删除 tenant A → in-flight 请求继续,新请求 401

### 12.3 并发测试

`tests/concurrent/test_multi_tenant_concurrent.py`:
- 多 tenant 并发写入同一 vector_store base — 子目录隔离 + 配额计数不串扰
- ContextVar 在 asyncio.gather 多协程下的行为正确性

### 12.4 评测扩展

`evaluation/datasets/multi_tenancy.jsonl`:多 case 显式带不同 `tenant_id`,验证检索结果不串扰。Nightly eval 把它纳入回归。

---

## 13. 风险与开放问题

### 13.1 已识别风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| **13.1.1** FAISS 多目录每租户独立 IndexFlatIP,内存随租户数线性增长 | 100 租户 × 500MB = 50GB;OOM | 见 §13.1.4 实例生命周期 + 硬件 sizing 表 §13.1.6 |
| **13.1.2** 老 API key 静默映射到 default,管理员可能忘记区分 | 多租户上线后 mode 实际是单租户 | 启动 WARN 日志 + nightly metrics 告警"detected legacy_apikey_format=true" |
| **13.1.3** tenant_id 命名规则严格,客户友好名(如 "Acme Corp")不合规 | 入驻流程多一步 | §2.1.1 alias 映射 |
| **13.1.4** 数据层实例缓存淘汰时的 in-flight 请求 | 段错误 / 读半新状态 | 见下小节 |
| **13.1.5** 多 worker uvicorn 部署时 in-memory QPS 计数器分片 | 实际 QPS = worker_count × configured | 本期文档化"单 worker 假设";未来加 Redis-based 限流 |
| **13.1.6** 缺硬件 sizing 表 | 部署期不知道该买多大机器 | 见下小节 |

#### 13.1.4 FAISS 实例生命周期(M6 落地)

bootstrap 在 build_data_layer 时按 tenant_id 维护 `Dict[str, FaissVectorDB]` 实例缓存:

```python
class TenantedVectorDBPool:
    def __init__(self, max_instances=100, deps=None):
        self._cache: Dict[str, FaissVectorDB] = {}
        self._in_use: Dict[str, int] = defaultdict(int)  # ref count
        self._max = max_instances
        self._deps = deps

    @contextmanager
    def acquire(self, tenant_id: str):
        if tenant_id not in self._cache:
            if len(self._cache) >= self._max:
                self._evict_idle()  # 淘汰 in_use==0 的最久未用
            self._cache[tenant_id] = FaissVectorDB(deps=self._deps, tenant_id=tenant_id)
        self._in_use[tenant_id] += 1
        try:
            yield self._cache[tenant_id]
        finally:
            self._in_use[tenant_id] -= 1

    def _evict_idle(self):
        # 只淘汰 in_use==0 的, 若所有都在用 -> raise StartupError 阶段已限制
        candidates = [tid for tid, n in self._in_use.items() if n == 0]
        if not candidates:
            self.logger.warning("[pool] cache full, no idle instance to evict")
            return  # 不强淘汰防 segfault
        oldest = candidates[0]  # 可改 LRU 顺序记录
        self._cache.pop(oldest, None)
        self._in_use.pop(oldest, None)
```

**约束**:本期不强淘汰(in_use>0 时跳过),避免活动请求段错误;若 cache 满且都在用,新租户请求直接 503 `SERVICE_UNAVAILABLE` 而非冒险淘汰。

#### 13.1.6 硬件 sizing 表

| 租户数 | FAISS 内存(每租户 500MB) | 建议机型 |
|---|---|---|
| ≤ 10 | 5 GB | 8 vCPU / 16 GB RAM |
| ≤ 50 | 25 GB | 16 vCPU / 64 GB RAM |
| ≤ 100 | 50 GB | 32 vCPU / 128 GB RAM |
| > 100 | 触发实例池上限,需切云向量库 | 不再支持本地 FAISS |

实际值跟 `vector_db.vector_dimension` 和文档量挂钩;本表假设 384 维 × 50 万 chunk。

### 13.2 开放问题(评审时讨论)

1. **是否需要 `_system_` 内部租户**用于审计 / 巡检 / 跨租户操作?
   - 倾向:需要,但严格限 internal_whitelist IP + 单独的 `system_api_key` secrets
2. **删除租户时 in-flight 状态如何处理?**
   - 倾向:§9.4 两步走(标记 deactivated → 24h 后真删)
3. **同一 API key 是否允许绑多个 tenant_id?**
   - 倾向:不允许(KISS);若需要,管理员发多个 key
4. **多 worker 部署的配额计数?**
   - 倾向:本期文档化"单 worker 假设",未来 PR 加 Redis-based 计数

---

## 14. 评审清单(v0.2)

对比 v0.1 评审报告的 9 个 ❌/⚠️ 检查项:

| Check 点 | v0.1 状态 | v0.2 状态 | 处理 |
|---|---|---|---|
| 目标 §1.1 列得齐全 | ✅ | ✅ | "数据强隔离" → "应用层数据隔离" |
| 非目标 §1.2 没遗漏 | ✅ | ✅ | 补"OS/容器级强隔离不在范围" |
| tenant_id 命名规范 | ⚠️ | ✅ | §2.1.1 alias 映射 |
| 数据隔离方案选择目录分区合理 | ⚠️ | ✅ | §3.4 未来扩展点 |
| 错误码跟现有风格一致 | ❌ | ✅ | M3/M4 已合并/细分 |
| 迁移路径足够向后兼容 | ❌ | ✅ | M1/M2:写路径明确 + 停机要求 + 回滚 |
| PR 拆分颗粒度合适 | ❌ | ✅ | M7:4 PR → 6 PR,每个 < 500 行 |
| 风险有缓解措施 | ⚠️ | ✅ | §13.1.4 + 硬件 sizing 表 |
| 开放问题已决议 | ⚠️ | ⚠️ | 4 个开放问题仍待评审,但已有倾向性建议 |

**v0.2 通过率**:8/9 ✅ + 1/9 ⚠️(开放问题待评审决议)。

---

## 15. v0.2 待评审项

请评审人就以下 4 个开放问题给出决议:

1. ☐ 是否启用 `_system_` 内部租户?
2. ☐ 删除租户的 grace period 是 24h 还是其他?
3. ☐ 同一 key 绑多个 tenant 是否开放?
4. ☐ 多 worker 配额计数本期是否上 Redis?

**决议后**,Task #33 进入实施阶段,按 §11 分 6 个 PR 推进。

---

## 16. 参考

- [现有 trace_id / session_id 单层补齐契约(Task #15)](development-setup.md)
- [配置优先级(Task #22)](configuration-priority.md)
- [Secrets 管理(Task #30)](secrets-management.md)
- [observability metrics(Task #27 / #34)](../basic_support/observability_module/__init__.py)
- [架构设计说明书 v2.0](../doc/RAG与Agent系统架构设计说明书.md)

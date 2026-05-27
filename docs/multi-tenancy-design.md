# 多租户隔离设计文档

> 文档版本:v0.1(需求 + 设计草案,尚未实施)
> 创建日期:2026-05-27
> 关联 Task:#33
> 状态:**待评审 / 实施前需对齐**

## 0. 文档目的

把"多租户隔离"从模糊概念落地为可执行设计,在动代码之前明确:

- 我们解决什么问题、不解决什么问题
- 数据/请求/认证/计费 4 个维度的隔离策略
- 跟现有单租户系统的兼容路径
- 实施分步路线 + 风险点

读者:架构 reviewer、运维、安全、产品。

---

## 1. 目标与非目标

### 1.1 目标

- **数据强隔离**:租户 A 不能在任何路径(检索/上传/state)看到租户 B 的数据
- **请求级 tenant_id 显式传递**:贯穿 ApiService → RequestHandler → Orchestrator → RAG/Agent → 数据层
- **认证驱动租户绑定**:API key / JWT 解析后自动绑定到 `tenant_id`,业务代码不直接接触原始凭证
- **审计与可观察性**:每条日志 / metric / OTel span 都携带 tenant_id 标签
- **向后兼容**:不传 `tenant_id` 的旧请求落入 `default` 租户,**已部署的 .yml/config 不需要立即改**

### 1.2 非目标(本期不做)

- **跨租户共享数据**(如租户 A 把文档分享给租户 B)— 留给后续 ACL 设计
- **租户级别的精细计费/账单**(本期只做配额硬限,不出账单)
- **租户级别的模型路由**(租户 A 用 GPT-4 / 租户 B 用 Qwen)— 留给后续 model gateway 设计
- **跨地域数据驻留**(GDPR 合规)— 留给后续 region routing 设计
- **租户管理控制台 UI**

---

## 2. 租户模型

### 2.1 标识与命名

```
tenant_id: str
  - 长度 3-32 字符
  - 字符集: [a-z0-9_-]
  - 全局唯一, 不可重命名 (改名等同于建新租户 + 数据迁移)
  - 保留 ID: "default" (向后兼容老请求), "system" (内部巡检)
```

推荐用 ULID 或 nanoid 而非 UUID — 短、可读、保持顺序。

### 2.2 租户层级

**本期决策:扁平**,不支持嵌套(子租户 / 部门)。

理由:嵌套会让 ACL 复杂度爆炸,且当前没有具体业务需求驱动。后续若需要,可在 `tenant_id` 上引入 `parent_tenant_id`,但本期不做。

---

## 3. 数据隔离方案

### 3.1 三个数据存储 + 隔离策略

| 存储 | 当前实现 | 隔离方案 | 备注 |
|---|---|---|---|
| 向量库 `FaissVectorDB` | 单 `vector_store/` 目录 | **目录分区**:`vector_store/<tenant_id>/` | FAISS 本地不支持 metadata filter 高效查询,只能切目录 |
| 文档库 `LocalDocumentStore` | 单 `documents/` 目录 | **目录分区**:`documents/<tenant_id>/` | 跟向量库一致,简单 |
| 状态存储 `LocalStateStore` | 单 `state_store/` + `<session_id>.json` | **路径分区**:`state_store/<tenant_id>/<session_id>.json` | session_id 仍全局唯一(由接口层补齐),但路径含 tenant_id |

### 3.2 为什么选目录分区而非 metadata filter?

- FAISS IndexFlatIP 不原生支持 filter,后置过滤会做无效计算
- 一个租户的索引规模可独立伸缩,内存占用清晰
- 单租户硬件故障 / 数据损坏不影响其他租户
- 删除某租户的数据只需 `rm -rf vector_store/<tenant_id>`,符合 GDPR "right to be forgotten"

**代价**:跨租户共享数据(如系统级公共知识库)需要显式建 `shared` 租户 + 显式调用。

### 3.3 切换到云向量库时的策略

如果未来从 FAISS 换到 Pinecone / Weaviate / Qdrant Cloud:
- 上述 vendors 都原生支持 namespace / collection 分区,直接用
- 本设计的 `tenant_id` 字段透传链路不变,仅 `vector_db.upsert/query` 内部把 tenant_id 翻译为对应 namespace 即可

---

## 4. 请求路径中的 tenant_id 透传

### 4.1 数据流

```
[HTTP Request]
   ↓ X-Tenant-Id header / JWT claim
[ApiService middleware]                ← 从 header / JWT 解析,设到 request.state.tenant_id
   ↓ trace_id + tenant_id 一并塞 body
[RequestHandler._standardize_request]  ← 补齐 tenant_id 字段(若上游缺失)
   ↓ {tenant_id, type, query/task, ...}
[SimpleOrchestrator.route]
   ↓ 透传
[SimpleRAG.retrieve / SimpleAgent.execute]
   ↓ 跟原 trace_id 一样, 只读不生成
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
    tenant_id: Optional[str] = None  # ← 新增
    top_k: int = Field(default=5, ge=1, le=50)
    trace_id: Optional[str] = None
    extra_params: Dict[str, Any] = Field(default_factory=dict)
```

校验规则:
- `tenant_id` 长度 3-32 / 字符集 `[a-z0-9_-]`
- 不传 → 默认 `"default"`(向后兼容)

### 4.3 单层补齐(类比 trace_id / session_id)

`RequestHandler._standardize_request` 是 tenant_id 的**单一补齐点**。下游(Orchestrator/RAG/Agent/数据层)只读、不生成、不兜底,缺失立刻 `[contract violation]` ERROR(跟 session_id 一样的契约模式)。

---

## 5. 认证驱动租户绑定

### 5.1 三种认证方式 → tenant_id 来源

| 认证方式 | tenant_id 来源 | 复杂度 |
|---|---|---|
| API Key 列表(当前) | 从 `security.api_keys` 配置里映射 `key → tenant_id`(扩展为 dict) | 低 |
| JWT | 解析 JWT 的 `tenant_id` claim | 中 |
| 显式 X-Tenant-Id header(仅内部服务调用) | header 值 + 白名单 IP 双校验 | 低 |

### 5.2 配置 schema 扩展(yaml)

```yaml
security:
  auth_enabled: true
  auth_type: "apikey"

  # 老格式(向后兼容): api_keys 是 list, 全部映射到 default
  # 新格式: api_keys 是 dict, 显式声明每个 key 的 tenant_id
  api_keys:
    "${API_KEY_TENANT_A}": "tenant-a"
    "${API_KEY_TENANT_B}": "tenant-b"
    "${API_KEY_INTERNAL}": "default"

  jwt:
    secret: "${JWT_SECRET}"
    tenant_id_claim: "tenant_id"   # JWT payload 里取哪个字段
```

### 5.3 旧版 API key 兼容

- 老的 `api_keys: ["key1", "key2"]` 格式继续支持,所有这种格式的 key 自动绑定到 `default`
- 新代码统一走 `tenant_id`,默认值即 `default`,**老调用方完全无感**

---

## 6. 配额与限流(本期最小可用)

### 6.1 配额维度

| 维度 | 单位 | 默认上限 | 配置位置 |
|---|---|---|---|
| 向量库文档数 | 个 | 10000 | yaml `quotas.<tenant_id>.max_documents` |
| 向量库存储空间 | MB | 1024 | yaml `quotas.<tenant_id>.max_vector_store_mb` |
| API QPS | req/s | 10 | yaml `quotas.<tenant_id>.max_qps` |
| 单次 RAG 检索 top_k | int | 50 | RequestEnvelope.top_k 已限制 |

### 6.2 实施位置

- 文档数 / 存储空间:在 `LocalDocumentStore.save_document` / `FaissVectorDB.upsert_vectors` 写入前检查
- QPS:`ApiService` 中间件计数器(本期简单实现:per-tenant in-memory 滑动窗口)
- 超限响应:HTTP 429 `QUOTA_EXCEEDED`(新增错误码)+ `Retry-After` header

### 6.3 默认值兜底

无显式配置的租户走 `quotas.default` 配置,避免新建租户时未配额导致无限增长。

---

## 7. 审计与可观察性

### 7.1 日志

`SystemLogger` 格式增加 `tenant_id` 字段:

```
2026-05-27 12:34:56 INFO [trace=t1 tenant=tenant-a session=s1] RAG 检索开始
```

### 7.2 Metrics(Task #27 扩展)

`ApiService._record_metrics` 增加 tenant 维度:

```
anything_requests_total{type="rag",tenant="tenant-a"} 42
anything_errors_total{code="QUOTA_EXCEEDED",tenant="tenant-b"} 3
```

### 7.3 OpenTelemetry(Task #34 扩展)

`trace_span` 的 attributes 自动带 `tenant_id`,实现路径:
- `trace_span` 内部从 `contextvars.ContextVar` 取当前 tenant
- ApiService middleware 在认证后 set context

---

## 8. 错误码

新增 4 个错误码(在 §10 错误码表追加):

| code | HTTP | retryable | 场景 |
|---|---|---|---|
| `TENANT_REQUIRED` | 401 | no | 未认证或认证未携带 tenant_id |
| `TENANT_NOT_FOUND` | 404 | no | tenant_id 不在配置的合法列表 |
| `QUOTA_EXCEEDED` | 429 | yes | 文档数 / 存储 / QPS 超限 |
| `CROSS_TENANT_FORBIDDEN` | 403 | no | 尝试访问其他租户的资源(如直接传 `doc_id`) |

---

## 9. 安全考虑

### 9.1 防越权读取

- 所有从 `doc_id` / `chunk_id` 反查 content 的路径,必须先校验该资源属于当前 tenant
- `BaseDocumentStore.get_document(doc_id, tenant_id)` 签名扩展,tenant_id 不匹配返回 404 而非 403(不泄漏存在性)

### 9.2 防 path traversal

`tenant_id` 在拼接到文件路径前,**字符集白名单严格校验**(`re.match(r"^[a-z0-9_-]{3,32}$", tenant_id)`),拒绝 `../` `~` 等。

### 9.3 防租户枚举

错误响应不区分 "tenant 不存在" 和 "tenant 存在但无权访问",统一返回 `404 TENANT_NOT_FOUND`。

---

## 10. 迁移路径(已有单租户数据如何升级)

### 10.1 现状

当前 `run/vector_store/`、`run/documents/`、`run/state_store/` 都是扁平目录。

### 10.2 升级步骤(向后兼容)

**Step 1: 代码上线**(本期实施)
- RequestEnvelope 增加 `tenant_id` 字段,默认 `"default"`
- 数据层 impl 接受 `tenant_id` 参数
- **新建文件**走 `<base_dir>/<tenant_id>/...` 路径
- **读老文件**走双重路径:先查 `<base_dir>/<tenant_id>/...`,fallback `<base_dir>/...`(老路径)

**Step 2: 数据迁移**(运维一次性操作)
```bash
# 把扁平目录的旧数据移到 default 子目录
mkdir -p run/vector_store/default
mv run/vector_store/*.index run/vector_store/*.json run/vector_store/*.npy \
   run/vector_store/default/
# 文档库 / 状态存储类似
```

**Step 3: 移除 fallback 逻辑**(下个版本)
- 验证迁移完成后,数据层 impl 移除 "fallback 老路径" 分支
- 强制 tenant_id 隔离

---

## 11. 实施分步路线

按风险递增,共 4 个 PR 提交,每个都可独立 merge:

### PR 1: 字段透传(零行为变化)
- `RequestEnvelope` 加 `tenant_id` 字段(默认 default)
- `RequestHandler._standardize_request` 补齐
- 单测覆盖新字段
- **行为不变**:数据层暂不分目录,所有租户共享 `default` 物理空间

### PR 2: 认证 → 租户绑定
- `ApiService._check_auth` 扩展,API key dict 形式 → tenant_id
- ApiService middleware 把 tenant_id 注入 request body
- 单测:配置 dict 形式 api_keys 验证不同 key 解析到不同 tenant_id

### PR 3: 数据层目录分区
- `FaissVectorDB(tenant_id=...)`:实例化时绑定 tenant,所有 ops 在该子目录
- `LocalDocumentStore` / `LocalStateStore` 同理
- bootstrap 改为按 tenant 缓存数据层实例(避免每次请求创建)
- 数据迁移脚本 `scripts/migrate_to_tenant_dirs.sh`

### PR 4: 配额 + 审计 + Metrics 扩展
- 配额检查接入 ApiService middleware
- SystemLogger 格式加 tenant_id
- _record_metrics 加 tenant 标签
- OpenTelemetry span attributes 加 tenant_id
- 新增 4 个错误码

---

## 12. 测试策略

### 12.1 单元测试

- `RequestEnvelope` schema 校验:tenant_id 字符集 / 长度 / 默认值
- `_check_auth` dict 形式 api_keys 解析正确性
- `FaissVectorDB(tenant_id="a")` 写入数据 → `FaissVectorDB(tenant_id="b")` 查询应返回 0
- 配额超限触发 `QUOTA_EXCEEDED` 错误码

### 12.2 集成测试

新增 `tests/integration/test_multi_tenancy.py`:

- Tenant A 上传文档 → Tenant B 检索应 0 命中
- Tenant A 创建 session → Tenant B 用同 session_id 查询应 404
- 跨租户 doc_id 直接访问应返回 `CROSS_TENANT_FORBIDDEN`

### 12.3 评测扩展

`evaluation/datasets/` 新增 `multi_tenancy.jsonl`:
- 多个 case 显式带不同 `tenant_id`,验证检索结果不串扰

---

## 13. 风险与开放问题

### 13.1 已识别风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| FAISS 多目录实例化开销大(每租户独立 IndexFlatIP) | 高频切换租户时内存可能爆 | bootstrap 实例缓存 + LRU 淘汰;后续可换支持 multi-index 的向量库 |
| 老 API key 格式静默映射到 default | 多租户上线后管理员可能忘记区分 | 启动期 WARN 日志:"detected legacy api_keys format, all mapped to 'default'" |
| tenant_id 命名规则严格,客户名可能不合规 | 入驻流程需要"客户名 → tenant_id"映射表 | 在认证层加 alias 映射 |

### 13.2 开放问题(评审时讨论)

1. **是否需要 `system` 内部租户**用于审计 / 巡检 / 跨租户操作?
   - 倾向:需要,但严格限制只有 internal IP 白名单能用
2. **删除租户的数据时,session_id 关联状态如何处理?**
   - 倾向:同步删除 `state_store/<tenant_id>/`,但保留 7 天审计日志
3. **同一 API key 是否允许绑多个 tenant_id?**
   - 倾向:不允许(KISS 原则);如果需要,管理员发多个 key

---

## 14. 评审清单

实施前请评审人员逐项确认:

- [ ] 目标 §1.1 列得齐全,优先级合理
- [ ] 非目标 §1.2 没有遗漏(用户实际不需要的事情没被悄悄塞进去)
- [ ] tenant_id 命名规范 §2.1 符合现有客户命名习惯
- [ ] 数据隔离方案 §3 选择目录分区合理(不是 metadata filter)
- [ ] 5 个错误码 §8 跟现有错误码表风格一致
- [ ] 迁移路径 §10 已足够向后兼容,**不需要 stop the world**
- [ ] 4 个 PR 拆分 §11 颗粒度合适,每个 PR 都能独立 review/rollback
- [ ] 风险 §13.1 有缓解措施
- [ ] 开放问题 §13.2 已有产品/架构决策

**评审通过后**,Task #33 进入实施阶段,按 §11 分 4 个 PR 推进。

---

## 15. 参考

- [现有 trace_id / session_id 单层补齐契约](../docs/development-setup.md)
- [配置优先级](configuration-priority.md)
- [Secrets 管理](secrets-management.md)
- [架构设计说明书 v2.0](../doc/RAG与Agent系统架构设计说明书.md)

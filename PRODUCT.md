# Anything — Product Strategy v1

> 一份产品定位决策, 让后续 feature 有判断标尺.
> 没有这份文件, 我们会继续 "什么都做但都比对手差一点".

## 1. 我们为谁做产品

**主用户**: 中小团队 (5-50 人) 的**非技术员工** + 部署运维者 1 人.

不是:
- ❌ 个人极客 (有 ChatGPT / Claude / Cursor)
- ❌ 大企业 (需要 SSO/SAML/SOC2/HIPAA, 不是我们能拼的)
- ❌ AI 开发者 (有 LangChain / LlamaIndex, 我们 24 模块也不能跟它比生态)

是:
- ✅ 公司里有人维护 (运维 / 全栈 / IT manager 都行), 但**用的人** 90% 是 PM / 销售 / 客服 / 产品 / 法务
- ✅ 团队有内部文档 (产品手册 / 政策 / 案例库) 想让员工自助查询
- ✅ 重复任务多 (写邮件 / 抄数据 / 转换格式), 想让 Agent 帮忙跑

## 2. 我们解决的 3 个核心 Job (按优先级)

### Job 1: "我想把团队文档变成可问答的知识库"
- 上传 PDF / Word / Markdown → 索引 → 同事 chat 时引用
- **不是** ChatGPT 通用问答, 是**基于我们内部文档**的精准回答
- 引用必须能点回原文档具体页

### Job 2: "我想让 Agent 帮我跨系统跑重复任务"
- "查上周 country=CN 的订单, 整理成表格"
- "把这个 PDF 翻译成英文写到 .docx"
- "看看 X 网站的最新公告, 总结发邮件给团队"

### Job 3: "团队需要共享 chat 记录 / 模板 / 知识库"
- 一个员工调好的 prompt, 其他人能复用 (workflow 模板)
- 一段精彩 chat 可以发给同事看 (share link)
- 一个知识库可以全团队访问

## 3. 我们故意不做的事

| 不做 | 理由 |
|------|------|
| SaaS 多租户大平台 | 没团队能跟 Notion AI / Glean 卷 |
| 端用户付费 | 没获客渠道 |
| 开源换流量 / 厂商绑定 | 不靠 LLM 厂商扶持 |
| 移动 App | Web 响应式够用, 团队工具不是高频手机场景 |
| 100% 开箱即跑的"全局 chat 助手" | 那是 ChatGPT 该做的 |
| 跟 n8n 比 workflow 复杂度 | 我们 workflow = "保存的 prompt", 不是自动化引擎 |

## 4. 取舍原则 (决策时拿这把尺子)

当你不确定加不加某个 feature 时, 问:

1. **它服务上面 3 个 Job 之一吗?**  — 不服务就不做
2. **它能让"非技术员工 5 分钟上手"吗?** — 必须 yes
3. **它跟现有 25 工具 / RAG / Session 这 3 个核心**是加强还是分散?
4. **它能在 3 周内做到行业前 3?** — 不能的话不做, 砍掉别的

## 5. 12 个月路线 (按 3 个 quarter)

### Q1 (现在 → 3 个月): 让 Job 1 (知识库) 做到极致
- ✅ Knowledge Base 概念 (PM-5)
- ✅ Per-KB chat (不再"在一锅粥里搜")
- ✅ Doc auto-summary (上传时 LLM 生成)
- ✅ Citation 点开能跳原文档段
- ✅ KB 分享 (团队成员都能用)
- ✅ 反馈回路 (PM-2 👍/👎)
- ✅ First-run wizard (PM-3 把上手门槛拉到 5 分钟内)

### Q2 (3-6 个月): 让 Job 2 (Agent) 真好用
- 工具结果卡片化 (SQL → 表格, PDF → 分页, web_search → link cards)
- Workflow 多步 (现在只是单 prompt 保存; 升级成 step-1 → step-2 chain)
- Slash commands 让 25 工具可发现 (PM-4)
- Multi-model side-by-side (PM-7-7) — 差异化王牌
- 工具安全审计 (file_write / shell_exec / sql 的人工审批扩展)

### Q3 (6-12 个月): Job 3 (团队协作)
- Session share link (PM-7-5)
- Team workspace (用现有 multi-tenant 升级)
- 简化 RBAC: admin / member 二级
- Slack / 钉钉 bot 集成 (主要是 push 用)
- 评论 / @ 提及 在 session 内

## 6. 砍掉 / 退化为隐藏的功能

明确说不做主推, 但保留代码 (向后兼容):

| 功能 | 原因 | 处理 |
|------|------|------|
| Reflection (critique → revise) | 用户不懂 / 不用 | 隐藏到 advanced toggle, 默 off |
| Plan mode 审批 | 高级用法, 普通用户怕 | 同上 |
| Multi-tenant 前端切换 | 90% 用 default | header chip 删除 / 移 settings |
| Hooks / Skills 前端面板 | 0 用户能配 | 删前端, 保 backend API |
| Subagent spawn 工具 | 罕用 | 不在欢迎页推荐 |
| Reranker / Query Rewrite toggle | 高级 | 移 settings 高级区 |

## 7. 不变的承诺

不管功能加多少, 永远保留:

- **Open source** (Apache 2 / MIT, license 待定)
- **自部署** — 数据不出团队机器
- **本地 LLM 一等公民** (Ollama / llama.cpp 跟云 LLM 同优先级)
- **不绑定厂商** — DashScope / OpenAI / Anthropic / Ollama 切换无感
- **可审计** — 每个 LLM 调用 / tool 调用都能查 trace

## 8. 何时重读这份文件

- 加 feature 前 — 用 §4 取舍原则
- 季度复盘 — 检查 §5 路线是否在轨
- 收到大新闻 (竞品融资 / GPT-5 发布 / 等) — 评估是否需要 pivot
- 每半年 — 重写一次, 把决策更新进来

---

**Version**: v1 (2026-06-01)
**Author**: PM 视角复盘第一版, 待 founder 确认 & 调整

# 技能(Skill)系统 + superpowers 集成

anything 的技能系统 (`basic_support/skills_module`) 让 agent 像 Claude Code / superpowers
一样, 用一组可复用的 `.md` 技能扩展能力与方法论。技能有 **三个来源**:

1. **人写技能** — 在技能目录放 `.md` (YAML frontmatter: `name` / `description` / `triggers` /
   `tools` / `priority` + markdown body)。
2. **自动沉淀** (借鉴 Hermes Agent 学习闭环) — 开 `agent.enable_skill_distill` 后, agent 成功
   完成 **复杂任务** (≥ `agent.skill_distill_min_tools` 个工具) 会**后台**把做法提炼成
   `_auto_*.md` 技能, 下次同类任务自动复用。去重 + 全程 fail-open + 不阻塞响应。
3. **superpowers / 第三方技能库** — 把任何 Claude Code 格式的技能库 clone 进技能目录即可用。

## 技能目录
- 默认 `skills/` (相对启动 cwd), 或 env `ANYTHING_SKILLS_DIR` 指定。
- **递归扫描**子目录 → 支持 superpowers 的嵌套布局 (`skills/<域>/<技能>/SKILL.md`)。

## 安装 superpowers (obra/superpowers)
```bash
# 把 superpowers 的技能放进 anything 的技能目录 (递归扫描会自动加载它的 skills/)
git clone https://github.com/obra/superpowers.git /data/skills/superpowers
export ANYTHING_SKILLS_DIR=/data/skills    # 或直接放默认 skills/ 下
# 重启服务即生效
```
> superpowers 是一套面向编码 agent 的方法论技能库 (TDD / 系统化调试 / 头脑风暴 / 写计划 /
> 子代理开发 / 代码审查 / git worktree 等)。它的技能是 `description` 驱动的, anything 通过下面
> 的"技能目录 + use_skill"机制使用它们。

## agent 怎么用技能 (两条路, 互补)
1. **trigger 自动注入** — 技能 frontmatter 的 `triggers` 子串命中用户问题时, 自动把该技能 body
   拼进 prompt (零额外调用, 适合关键词型技能)。
2. **技能目录 + `use_skill` 主动加载** (superpowers 式) — prompt 的稳定前缀里注入"技能目录"
   (全部技能名 + 描述); agent 遇到相关任务时调 `use_skill(name)` 工具, 把该技能的**完整指南**
   拉进上下文再动手。适合**描述型 / 无 trigger 的方法论技能**。这就是 superpowers 的
   "任务前先查相关技能、把技能当工作流"机制。

## 相关配置 / 工具
- `agent.enable_skill_distill` (默认 false) / `agent.skill_distill_min_tools` (默认 2) — 自动沉淀。
- `use_skill` 工具: 只读、无副作用 → 不进审批白名单, 默认注册。
- `/admin/status` 与 skills info 可看已加载技能数与来源 (人写 / `_auto_` 沉淀 / superpowers)。

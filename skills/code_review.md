---
name: code_review
description: 代码审查 / Code review guidance
triggers:
  - 代码审查
  - 审查代码
  - 审一下
  - code review
  - review this code
priority: 10
---

# 代码审查指南

按以下顺序审视代码:

1. **安全性**
   - SQL 注入: 参数化查询?
   - XSS: 用户输入是否转义?
   - 命令注入: 是否避免 shell=True / unsafe exec?
   - 凭据泄露: hardcoded api_key / password?

2. **错误处理**
   - 关键路径有 try/except 还是裸抛?
   - 异常被静默吞掉没有日志?
   - 错误信封是否含 trace_id?

3. **资源管理**
   - 文件 / 数据库连接是否走 context manager?
   - 是否有 leak (背景 thread 没 join, ws 没 close)?

4. **可读性**
   - 命名是否表达意图?
   - 函数 > 50 行该拆吗?
   - 注释解释 "why" 还是 "what"?

5. **性能**
   - O(n²) 的循环嵌套?
   - 不必要的 LLM 调用 / 网络往返?

回答风格: 给出具体的行号 + 改进建议, 不只是泛泛的 "可以更好".

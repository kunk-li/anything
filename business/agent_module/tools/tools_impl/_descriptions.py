# -*- coding: utf-8 -*-
"""
TOOL_DESCRIPTIONS — 给 Agent LLM 用的工具描述表 (Task MM #73).

原本在 builtin_tools.py 底部, 现独立成文件方便添加新工具时找位置.
"""

from typing import Dict

TOOL_DESCRIPTIONS: Dict[str, str] = {
    "calculator": (
        "安全数学计算, 不联网。"
        ' input: {"expression": str}. 支持 + - * / // % ** sqrt pow log exp 三角函数 + 常量 pi/e。'
        " 适合: 算术、统计、单位换算。"
    ),
    "datetime": (
        "时间工具, 不联网。"
        ' input: {"op": "now"|"add"|"diff", ...}.'
        ' op=now (可选 tz_offset_hours), op=add (iso + days/hours/...), op=diff (iso_start + iso_end).'
        " 适合: 当前时间、日期算术、距某天还有几天。"
    ),
    "wikipedia": (
        "Wikipedia 摘要查询, 需联网。"
        ' input: {"query": str, "lang": "zh"|"en", "max_chars": int}.'
        " 适合: 查询人物/概念/历史事件的权威信息。无网时会失败, 应降级到 llm_generate。"
    ),
    "document_read": (
        "按 doc_id 拉本地文档的全文 (从 document_store)。"
        ' input: {"doc_id": str, "tenant_id": str = "default", "max_chars": int = 2000}.'
        " 适合: rag_search 命中后想看完整原文, 或 citations 钻取。"
    ),
    "regex_extract": (
        "正则抽取, 不联网。"
        ' input: {"text": str, "pattern": str, "flags": "ims" 或 ["i","m","s"], "max_matches": int, "return_groups": bool}.'
        " 返回所有匹配的 match / groups / span。"
        " 适合: 从文本里提取邮箱、URL、电话、数字、日期等结构化片段。"
    ),
    "text_stats": (
        "文本统计, 不联网。"
        ' input: {"text": str}.'
        " 返回字符数 / 词数 / 行数 / 中日韩字符数 / ASCII / 数字字符数。"
        " 适合: 写作分析、内容审计、判断文档复杂度。"
    ),
    "json_query": (
        "简化 JSONPath 查询, 不联网。"
        ' input: {"data": Any 或 "json_text": str, "path": "user.name" / "items.[0].title" / "results.*.score"}.'
        " path 支持点分键、[n] 数组下标、* 通配符。"
        " 适合: 从 http_get 等工具返回的 JSON 里提取特定字段。"
    ),
    "http_get": (
        "受控 HTTP GET, 联网。带 SSRF 防御 (拒绝私网/环回 IP, 禁跟跳转, 上限 10MB)。"
        ' input: {"url": str, "max_bytes": int = 1MB, "timeout": int = 8s}.'
        " 仅支持 http/https。"
        " 适合: 抓取公开网页 API / 静态资源。私网/内部服务严禁,失败请走 llm_generate 兜底。"
    ),
    "text_summarize": (
        "调 LLM 把长文本压缩为 N 句摘要。"
        ' input: {"text": str, "max_sentences": int = 3, "lang": "zh"|"en"}.'
        " 适合: 把 rag_search / document_read / http_get 拿到的大段文本浓缩进上下文。"
    ),
    "code_lint": (
        "代码语法检查 (静态, 不执行), 不联网。"
        ' input: {"code": str, "language": "python"|"json"|"yaml"|"sql"}.'
        " 返回 valid + 行列号 + 错误描述。"
        " 适合: 用户/LLM 输出代码前做合法性校验。"
    ),
    "email_send": (
        "通过 SMTP 发邮件。需要后端注入 smtp 配置 (host/port/user/password/from_addr)。"
        ' input: {"to": str|list, "subject": str, "body": str, "cc": list, "is_html": bool}.'
        " 缺配置时返回 SERVICE_UNAVAILABLE。适合: 通知 / 报告分发场景。"
    ),
    "image_describe": (
        "多模态 LLM 描述图片。"
        ' input: {"image_path": 本地路径 或 "image_base64": str, "prompt": "请描述", "model_name": "default"}.'
        " 需要 OpenAIMultimodalAdapter 或类似配置的多模态模型。"
        " 适合: 看图回答 / 图像内容理解。"
    ),
    "weather": (
        "天气查询, 用 Open-Meteo (公网免 key)。"
        ' input: {"location": "北京" 或 "latitude": 39.9, "longitude": 116.4}.'
        " 返回 location 元信息 + 当前温度/风速/天气码。无网时失败。"
    ),
    "currency_convert": (
        "汇率换算, 用 ECB Frankfurter API (公网免 key)。"
        ' input: {"from_currency": "USD", "to_currency": "CNY", "amount": 100, "date": "latest"}.'
        " 货币代码 ISO 4217 3 字母。返回 converted + rate + date。"
    ),
    "python_sandbox": (
        "Python 受限执行, AST 严格白名单 + 协作式超时。"
        ' input: {"code": str, "timeout_seconds": 2.0}.'
        " 禁: import / attribute / __ 双下划线 / def / class / open / exec / eval。"
        " 允许: 数字 + 字符串 + 列表/字典 + for/if/while + 列表推导 + 内置 abs/len/sum 等。"
        " 适合: 比 calculator 更复杂的小段算法验证 (排序、统计聚合、列表变换)。"
        " ⚠️ 不能跑不可信代码, sandbox 是公认难题, 生产请隔离进程 / Docker。"
    ),
    "spawn_subagent": (
        "任务分解: 把一个独立子任务交给受限子 Agent 处理 (Claude Code Task tool 风格)。"
        ' input: {"role": str 可选, "task": str, "allowed_tools": [str] 可选, "max_iterations": int 可选}.'
        " 子 agent 只能用 allowed_tools 中的工具 (默认继承父全部工具但去掉 spawn_subagent 防递归);"
        " 跑完 ReAct 后只回报 final answer + 概要, 子 history 不污染父 react_history。"
        " 适合: 多步骤任务先分解再合并 (例: 子1 查资料 / 子2 算指标 / 主 agent 汇总)。"
    ),
}

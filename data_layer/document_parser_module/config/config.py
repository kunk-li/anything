from __future__ import annotations

# 模块默认配置

SUPPORTED_FILE_TYPES = [
    ".txt",
    ".pdf",
    ".docx",
    ".md",
    ".py",
    ".xlsx",
    ".xls",
    ".ppt",
    ".pptx",
    ".csv",
    ".json",
    ".xml",
    ".html",
    ".htm",
]

DEFAULT_ENCODING = "utf-8"

# 文本清洗策略（极简默认；如系统提供 CommonUtils.clean_text，将优先使用）
NORMALIZE_WHITESPACE = True

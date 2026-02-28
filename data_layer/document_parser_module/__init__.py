"""document_parser_module

数据层文档解析模块：将多种格式文件解析为统一标准文本结构。

对外暴露：LocalDocumentParser
"""

from .core.impl import LocalDocumentParser

__all__ = ["LocalDocumentParser"]

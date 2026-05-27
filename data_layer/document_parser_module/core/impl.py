from __future__ import annotations

import json
import os
import re
from html.parser import HTMLParser
from typing import Dict, List

import xmltodict

# 第三方解析依赖
from PyPDF2 import PdfReader
from docx import Document
import pandas as pd
from pptx import Presentation

from .base import BaseDocumentParser
from ..config.config import DEFAULT_ENCODING, SUPPORTED_FILE_TYPES
from ..utils.tool_functions import check_file_type, get_file_extension


# ------------------------
# 依赖模块的“兼容导入”
# ------------------------
try:
    from common_utils_module.core.impl import CommonUtils  # type: ignore
except Exception:  # pragma: no cover

    class CommonUtils:  # noqa: D401
        """兼容实现：当系统通用工具模块不可用时，提供最小可用的 clean_text。"""

        @staticmethod
        def clean_text(text: str) -> str:
            text = text.replace("\u00a0", " ")
            text = re.sub(r"\r\n?", "\n", text)
            # 仅折叠连续空格，保留 TAB（用于 csv/excel 表格分隔）
            text = re.sub(r"[ ]+", " ", text)
            text = re.sub(r"\n{3,}", "\n\n", text)
            return text.strip()


try:
    from log_module.core.impl import SystemLogger  # type: ignore
except Exception:  # pragma: no cover
    import logging

    class SystemLogger:  # noqa: D401
        """兼容实现：当系统日志模块不可用时，使用标准 logging。"""

        def __init__(self):
            self._logger = logging.getLogger("document_parser_module")
            if not self._logger.handlers:
                handler = logging.StreamHandler()
                formatter = logging.Formatter(
                    fmt="%(asctime)s %(levelname)s %(name)s - %(message)s"
                )
                handler.setFormatter(formatter)
                self._logger.addHandler(handler)
                self._logger.setLevel(logging.INFO)

        def info(self, msg: str):
            self._logger.info(msg)

        def warning(self, msg: str):
            self._logger.warning(msg)

        def error(self, msg: str):
            self._logger.error(msg)


try:
    from exception_module.core.impl import RAGException  # type: ignore
except Exception:  # pragma: no cover

    class RAGException(Exception):
        """兼容实现：当系统异常模块不可用时，提供最小可用的 RAGException。"""

        def __init__(self, code: str, message: str):
            super().__init__(f"{code}: {message}")
            self.code = code
            self.message = message


# ------------------------
# 内部辅助：HTML -> 纯文本
# ------------------------
class _HTMLTextExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._chunks: List[str] = []

    def handle_data(self, data: str) -> None:
        if data:
            self._chunks.append(data)

    def get_text(self) -> str:
        return "".join(self._chunks)


class LocalDocumentParser(BaseDocumentParser):
    """本地文档解析实现类。

    负责解析 txt/pdf/docx/md/py/excel/ppt/pptx/csv/json/xml 为文本，不做存储，返回统一标准结构。
    """

    def __init__(self, deps=None):
        # deps: 可选 BasicDeps,未注入时构造一份(向后兼容)
        if deps is not None:
            self.utils = deps.utils
            self.logger = deps.logger
        else:
            try:
                from deps_module import build_basic_deps
                _deps = build_basic_deps()
                self.utils = _deps.utils
                self.logger = _deps.logger
            except Exception:
                # 极端情况下 deps_module 不可用,沿用兼容 import 的实现
                self.utils = CommonUtils()
                self.logger = SystemLogger()
        self.supported_file_types = list(SUPPORTED_FILE_TYPES)

    # ------------------------
    # 各文件类型解析
    # ------------------------
    def _parse_txt(self, file_path: str) -> str:
        with open(file_path, "r", encoding=DEFAULT_ENCODING, errors="ignore") as f:
            return f.read()

    def _parse_pdf(self, file_path: str) -> str:
        reader = PdfReader(file_path)
        texts: List[str] = []
        for i, page in enumerate(reader.pages, start=1):
            try:
                page_text = page.extract_text() or ""
            except Exception:
                page_text = ""
            if page_text.strip():
                texts.append(f"[Page {i}]\n{page_text}")
        return "\n\n".join(texts)

    def _parse_docx(self, file_path: str) -> str:
        doc = Document(file_path)
        paras = [p.text for p in doc.paragraphs if p.text and p.text.strip()]
        return "\n".join(paras)

    def _parse_md(self, file_path: str) -> str:
        with open(file_path, "r", encoding=DEFAULT_ENCODING, errors="ignore") as f:
            md_text = f.read()
        # 优先使用 markdown 库；如未安装则降级为“轻量去标记”
        try:
            import markdown  # type: ignore

            html = markdown.markdown(md_text, extensions=["extra", "tables", "fenced_code"])  # type: ignore
            parser = _HTMLTextExtractor()
            parser.feed(html)
            return parser.get_text()
        except Exception:
            # 轻量降级：删除常见 markdown 标记，保留可读文本
            text = md_text
            # code fences
            text = re.sub(r"```[\s\S]*?```", lambda m: m.group(0).strip("`") , text)
            # inline code/backticks
            text = re.sub(r"`([^`]+)`", r"\1", text)
            # headings
            text = re.sub(r"^#{1,6}\s*", "", text, flags=re.MULTILINE)
            # emphasis/bold
            text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
            text = re.sub(r"\*([^*]+)\*", r"\1", text)
            text = re.sub(r"_([^_]+)_", r"\1", text)
            # links [text](url) -> text
            text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
            return text

    def _parse_py(self, file_path: str) -> str:
        # 保留代码结构与注释：直接读取文本
        with open(file_path, "r", encoding=DEFAULT_ENCODING, errors="ignore") as f:
            return f.read()

    def _parse_excel(self, file_path: str) -> str:
        # 以工作表组织文本：SheetName + 表格内容
        try:
            sheets = pd.read_excel(file_path, sheet_name=None, dtype=str)  # type: ignore
        except Exception as e:
            raise RAGException("EXCEL_PARSE_ERROR", f"excel文件解析异常：{file_path}，原因：{e}")

        parts: List[str] = []
        for sheet_name, df in sheets.items():
            df = df.fillna("")
            table_text = df.to_csv(sep="\t", index=False)
            parts.append(f"[Sheet: {sheet_name}]\n{table_text}")
        return "\n\n".join(parts)

    def _parse_ppt(self, file_path: str) -> str:
        try:
            pres = Presentation(file_path)
        except Exception as e:
            raise RAGException("PPT_PARSE_ERROR", f"ppt文件解析异常：{file_path}，原因：{e}")

        parts: List[str] = []
        for idx, slide in enumerate(pres.slides, start=1):
            slide_texts: List[str] = []
            for shape in slide.shapes:
                if hasattr(shape, "text"):
                    t = (shape.text or "").strip()
                    if t:
                        slide_texts.append(t)
            if slide_texts:
                parts.append(f"[Slide {idx}]\n" + "\n".join(slide_texts))
            else:
                parts.append(f"[Slide {idx}]\n")
        return "\n\n".join(parts)

    def _parse_csv(self, file_path: str) -> str:
        try:
            df = pd.read_csv(file_path, dtype=str)
        except Exception as e:
            raise RAGException("DOCUMENT_PARSE_FAILED", f"csv文件解析异常：{file_path}，原因：{e}")
        df = df.fillna("")
        return df.to_csv(sep="\t", index=False)

    def _parse_json(self, file_path: str) -> str:
        try:
            with open(file_path, "r", encoding=DEFAULT_ENCODING, errors="ignore") as f:
                obj = json.load(f)
        except Exception as e:
            raise RAGException("JSON_PARSE_ERROR", f"json文件解析异常：{file_path}，原因：{e}")
        return json.dumps(obj, ensure_ascii=False, indent=2)

    def _parse_xml(self, file_path: str) -> str:
        try:
            with open(file_path, "r", encoding=DEFAULT_ENCODING, errors="ignore") as f:
                xml_text = f.read()
            obj = xmltodict.parse(xml_text)
        except Exception as e:
            raise RAGException("XML_PARSE_ERROR", f"xml文件解析异常：{file_path}，原因：{e}")
        # 统一输出为“可读文本”：使用 JSON 形式展示结构
        return json.dumps(obj, ensure_ascii=False, indent=2)

    # ------------------------
    # 抽象方法实现
    # ------------------------
    def parse_file(self, file_path: str) -> Dict:
        if not os.path.isfile(file_path):
            raise RAGException("DOCUMENT_NOT_FOUND", f"文档文件不存在：{file_path}")

        if not check_file_type(file_path, self.supported_file_types):
            ext = get_file_extension(file_path)
            raise RAGException(
                "UNSUPPORTED_FILE_TYPE",
                f"不支持的文件类型：{ext}（文件：{file_path}）",
            )

        ext = get_file_extension(file_path)
        file_name = os.path.basename(file_path)

        try:
            if ext == ".txt":
                content = self._parse_txt(file_path)
            elif ext == ".pdf":
                content = self._parse_pdf(file_path)
            elif ext == ".docx":
                content = self._parse_docx(file_path)
            elif ext == ".md":
                content = self._parse_md(file_path)
            elif ext == ".py":
                content = self._parse_py(file_path)
            elif ext in (".xlsx", ".xls"):
                content = self._parse_excel(file_path)
            elif ext in (".ppt", ".pptx"):
                content = self._parse_ppt(file_path)
            elif ext == ".csv":
                content = self._parse_csv(file_path)
            elif ext == ".json":
                content = self._parse_json(file_path)
            elif ext == ".xml":
                content = self._parse_xml(file_path)
            else:
                # 理论上不会到这里（已校验 supported_file_types）
                raise RAGException("UNSUPPORTED_FILE_TYPE", f"不支持的文件类型：{ext}")

            # 基础清洗（优先使用系统 CommonUtils.clean_text）
            if hasattr(self.utils, "clean_text"):
                content = self.utils.clean_text(content)
            else:  # pragma: no cover
                content = str(content).strip()

            self.logger.info(f"解析成功：{file_path}")
            return {"content": content, "file_name": file_name, "meta": {"ext": ext}}

        except RAGException:
            self.logger.error(f"解析失败：{file_path}")
            raise
        except Exception as e:
            self.logger.error(f"解析失败：{file_path}，原因：{e}")
            raise RAGException("DOCUMENT_PARSE_FAILED", f"文档解析失败：{file_path}，原因：{e}")

    def parse_folder(self, folder_path: str) -> List[Dict]:
        if not os.path.isdir(folder_path):
            raise RAGException("FOLDER_NOT_FOUND", f"文件夹不存在：{folder_path}")

        results: List[Dict] = []
        for root, _, files in os.walk(folder_path):
            for name in files:
                fp = os.path.join(root, name)
                if not check_file_type(fp, self.supported_file_types):
                    self.logger.warning(f"跳过不支持的文件：{fp}")
                    continue
                results.append(self.parse_file(fp))

        self.logger.info(f"批量解析完成：{folder_path}，共 {len(results)} 个文件")
        return results

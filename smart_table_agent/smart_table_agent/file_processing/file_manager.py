import os
import pandas as pd
from typing import Union, List, Dict, Any

# LangChain 1.x 文档加载器来自 langchain_community
from langchain_community.document_loaders import (
    PyPDFLoader,
    UnstructuredWordDocumentLoader,
    UnstructuredMarkdownLoader,
    TextLoader,
)


class FileManager:
    """智能文件管理器：支持单文件读取 & 文件夹递归读取"""

    TABLE_EXT = {".csv", ".xls", ".xlsx"}
    TEXT_EXT = {".txt", ".md"}
    DOC_EXT = {".pdf", ".docx"}

    def __init__(self):
        pass

    # 识别文件类型
    @staticmethod
    def detect_type(file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()

        if ext in FileManager.TABLE_EXT:
            return "table"
        elif ext in FileManager.TEXT_EXT:
            return "text"
        elif ext in FileManager.DOC_EXT:
            return "document"
        else:
            return "unknown"

    # 表格文件加载
    @staticmethod
    def load_table(file_path: str) -> pd.DataFrame:
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".csv":
            return pd.read_csv(file_path)
        elif ext in [".xls", ".xlsx"]:
            return pd.read_excel(file_path)
        else:
            raise ValueError(f"不支持的表格格式：{ext}")

    # 文档加载
    @staticmethod
    def load_document(file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".pdf":
            loader = PyPDFLoader(file_path)
        elif ext == ".docx":
            loader = UnstructuredWordDocumentLoader(file_path)
        else:
            raise ValueError(f"不支持的文档格式: {ext}")

        docs = loader.load()
        return "\n".join([d.page_content for d in docs])

    # 文本文件加载
    @staticmethod
    def load_text(file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()

        if ext == ".md":
            loader = UnstructuredMarkdownLoader(file_path)
            docs = loader.load()
            return "\n".join([d.page_content for d in docs])
        elif ext == ".txt":
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        else:
            raise ValueError(f"不支持的文本格式：{ext}")

    # 单文件加载
    def load_file(self, file_path: str) -> Dict[str, Any]:
        if not os.path.isfile(file_path):
            raise ValueError(f"不是文件：{file_path}")

        file_type = self.detect_type(file_path)

        try:
            if file_type == "table":
                content = self.load_table(file_path)
            elif file_type == "text":
                content = self.load_text(file_path)
            elif file_type == "document":
                content = self.load_document(file_path)
            else:
                content = None

            return {
                "path": file_path,
                "type": file_type,
                "content": content,
            }

        except Exception as e:
            return {
                "path": file_path,
                "type": "error",
                "error": str(e),
                "content": None,
            }

    # 文件夹递归读取
    def load_folder(self, folder_path: str) -> List[Dict[str, Any]]:
        if not os.path.isdir(folder_path):
            raise ValueError(f"不是目录：{folder_path}")

        results = []

        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                results.append(self.load_file(file_path))

        return results

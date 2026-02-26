import os
import pandas as pd
from typing import List, Dict, Any, Optional

# 文档加载器（PDF / Word / Markdown）
from langchain_community.document_loaders import (
    PyPDFLoader,
    UnstructuredWordDocumentLoader,
    UnstructuredMarkdownLoader,
    TextLoader
)

# 可扩展数据库加载接口（这里以 SQLite 为例）
import sqlite3


class FileLoader:
    """
    文件加载器：
    1. 支持本地文件加载（表格/文本/文档）
    2. 支持数据库文件加载
    """

    TABLE_EXT = {".csv", ".xls", ".xlsx"}
    TEXT_EXT = {".txt", ".md"}
    DOC_EXT = {".pdf", ".docx"}

    # -----------------------------------
    # 文件类型检测
    # -----------------------------------
    @staticmethod
    def detect_type(file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        if ext in FileLoader.TABLE_EXT:
            return "table"
        elif ext in FileLoader.TEXT_EXT:
            return "text"
        elif ext in FileLoader.DOC_EXT:
            return "document"
        else:
            return "unknown"

    # -----------------------------------
    # 表格加载
    # -----------------------------------
    @staticmethod
    def load_table(file_path: str) -> pd.DataFrame:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".csv":
            return pd.read_csv(file_path)
        elif ext in [".xls", ".xlsx"]:
            return pd.read_excel(file_path)
        else:
            raise ValueError(f"不支持的表格格式：{ext}")

    # -----------------------------------
    # 文本加载
    # -----------------------------------
    @staticmethod
    def load_text(file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".txt":
            with open(file_path, "r", encoding="utf-8") as f:
                return f.read()
        elif ext == ".md":
            loader = UnstructuredMarkdownLoader(file_path)
            docs = loader.load()
            return "\n".join([d.page_content for d in docs])
        else:
            raise ValueError(f"不支持的文本格式：{ext}")

    # -----------------------------------
    # 文档加载
    # -----------------------------------
    @staticmethod
    def load_document(file_path: str) -> str:
        ext = os.path.splitext(file_path)[1].lower()
        if ext == ".pdf":
            loader = PyPDFLoader(file_path)
        elif ext == ".docx":
            loader = UnstructuredWordDocumentLoader(file_path)
        else:
            raise ValueError(f"不支持的文档格式：{ext}")

        docs = loader.load()
        return "\n".join([d.page_content for d in docs])

    # -----------------------------------
    # 单文件加载
    # -----------------------------------
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

            return {"path": file_path, "type": file_type, "content": content}

        except Exception as e:
            return {"path": file_path, "type": "error", "error": str(e), "content": None}

    # -----------------------------------
    # 文件夹递归加载
    # -----------------------------------
    def load_folder(self, folder_path: str) -> List[Dict[str, Any]]:
        if not os.path.isdir(folder_path):
            raise ValueError(f"不是目录：{folder_path}")

        results = []
        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                results.append(self.load_file(file_path))
        return results

    # -----------------------------------
    # 数据库加载（以 SQLite 为例）
    # -----------------------------------
    @staticmethod
    def load_from_sqlite(db_path: str, table_name: str) -> pd.DataFrame:
        """
        从 SQLite 数据库中读取表格数据
        :param db_path: SQLite 数据库路径
        :param table_name: 表名
        :return: pandas DataFrame
        """
        if not os.path.isfile(db_path):
            raise ValueError(f"数据库文件不存在: {db_path}")
        conn = sqlite3.connect(db_path)
        try:
            df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
        finally:
            conn.close()
        return df

if __name__ == '__main__':
    loader = FileLoader()

    # 1. 加载单文件
    file_info = loader.load_file("data/example.pdf")
    print(file_info["type"], len(file_info["content"]))

    # 2. 加载文件夹
    folder_info = loader.load_folder("data/")
    print(len(folder_info))

    # 3. 从数据库加载
    df = loader.load_from_sqlite("data/example.db", "users")
    print(df.head())

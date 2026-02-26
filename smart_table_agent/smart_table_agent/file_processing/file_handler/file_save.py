import os
from typing import List, Optional

# 向量数据库相关
from langchain.vectorstores import FAISS, Chroma
from langchain.embeddings.openai import OpenAIEmbeddings
from langchain.schema import Document


class FileSave:
    """
    文件保存类
    功能：
    1. 本地保存文本或切割块
    2. 保存到向量数据库，方便后续检索
    """

    def __init__(self, save_dir: str = "./processed", vector_db: str = "faiss"):
        self.save_dir = save_dir
        os.makedirs(self.save_dir, exist_ok=True)

        self.vector_db_type = vector_db.lower()
        self.vector_store = None  # 向量数据库实例
        self.embedding_model = OpenAIEmbeddings()  # 默认使用 OpenAI Embeddings

    # -----------------------------
    # 本地文本保存
    # -----------------------------
    def save_text(self, content: str, filename: str) -> str:
        save_path = os.path.join(self.save_dir, filename)
        with open(save_path, "w", encoding="utf-8") as f:
            f.write(content)
        return save_path

    # -----------------------------
    # 保存文本切块
    # -----------------------------
    def save_chunks(self, chunks: List[str], base_filename: str) -> List[str]:
        paths = []
        for i, chunk in enumerate(chunks):
            fname = f"{os.path.splitext(base_filename)[0]}_chunk{i+1}.txt"
            path = self.save_text(chunk, fname)
            paths.append(path)
        return paths

    # -----------------------------
    # 初始化向量数据库
    # -----------------------------
    def init_vector_store(self, embeddings=None, persist_dir: Optional[str] = None):
        if embeddings is not None:
            self.embedding_model = embeddings

        if self.vector_db_type == "faiss":
            self.vector_store = FAISS(embedding_function=self.embedding_model.embed_query, index=None)
        elif self.vector_db_type == "chroma":
            self.vector_store = Chroma(persist_directory=persist_dir, embedding_function=self.embedding_model)
        else:
            raise ValueError(f"不支持的向量数据库类型: {self.vector_db_type}")

    # -----------------------------
    # 保存到向量数据库
    # -----------------------------
    def save_to_vector_db(self, chunks: List[str], metadata_list: Optional[List[dict]] = None):
        """
        将文本切块保存到向量数据库
        :param chunks: 文本列表
        :param metadata_list: 可选元数据列表
        """
        if self.vector_store is None:
            raise RuntimeError("向量数据库未初始化，请先调用 init_vector_store()")

        docs = []
        for i, chunk in enumerate(chunks):
            metadata = metadata_list[i] if metadata_list and i < len(metadata_list) else {}
            docs.append(Document(page_content=chunk, metadata=metadata))

        self.vector_store.add_documents(docs)

    # -----------------------------
    # 保存到向量数据库并持久化（Chroma 特有）
    # -----------------------------
    def persist_vector_db(self):
        if self.vector_db_type == "chroma" and self.vector_store:
            self.vector_store.persist()


if __name__ == '__main__':
    # 初始化保存类
    saver = FileSave(save_dir="./processed", vector_db="chroma")

    # 保存文本
    text_path = saver.save_text("这是测试文本", "example.txt")
    print("本地文本保存路径:", text_path)

    # 保存文本切块
    chunks = ["第一块内容", "第二块内容"]
    chunk_paths = saver.save_chunks(chunks, "example.txt")
    print("本地切块路径:", chunk_paths)

    # 初始化向量数据库
    saver.init_vector_store(persist_dir="./vector_db")

    # 保存切块到向量数据库
    metadata_list = [{"source": "example.txt"} for _ in chunks]
    saver.save_to_vector_db(chunks, metadata_list)

    # 持久化（仅 Chroma）
    saver.persist_vector_db()

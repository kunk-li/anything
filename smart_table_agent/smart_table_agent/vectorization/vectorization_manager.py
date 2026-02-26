import numpy as np
from sentence_transformers import SentenceTransformer  # 导入句子向量模型
import faiss  # 导入 FAISS 向量数据库库
from typing import List, Tuple  # 类型注解


class VectorManager:
    def __init__(self, vector_dim: int, method: str = 'sentence_transformer', st_model_name: str = 'all-MiniLM-L6-v2'):
        """
        vector_dim: 向量维度
        method: 向量化方法，目前仅支持 'sentence_transformer'
        st_model_name: SentenceTransformer 模型名
        """
        self.method = method
        self.vector_dim = vector_dim  # 向量维度
        self.texts: List[str] = []   # 用于存储原始文本
        self.vectors = None           # 用于存储对应向量

        # 初始化向量化模型
        if method == 'sentence_transformer':
            self.model = SentenceTransformer(st_model_name)  # 加载预训练句子向量模型
        else:
            raise ValueError("目前仅支持 sentence_transformer 向量化")

        # 初始化 FAISS 索引，使用内积(IP)计算近似余弦相似度
        self.index = faiss.IndexFlatIP(vector_dim)
        self.id_map = []  # 存储对应文本索引，用于检索时返回文本内容

    def add_texts(self, new_texts: List[str]):
        """
        新增文本并向量化，同时加入 FAISS 索引
        """
        # 生成句子向量，并归一化（方便余弦相似度计算）
        vecs = self.model.encode(new_texts, normalize_embeddings=True)
        # 将向量加入 FAISS 索引
        self.index.add(vecs)
        # 将文本加入 id_map，用于检索返回
        self.id_map.extend(new_texts)
        # 合并新的向量到已有向量矩阵
        self.vectors = vecs if self.vectors is None else np.vstack([self.vectors, vecs])
        # 保存原始文本
        self.texts.extend(new_texts)

    def most_similar(self, query: str, top_k: int = 5) -> List[Tuple[str, float]]:
        """
        查询与输入文本最相似的 top_k 文本
        返回 [(文本, 相似度), ...]
        """
        # 对查询文本生成向量，并归一化
        q_vec = self.model.encode([query], normalize_embeddings=True)
        # 在 FAISS 索引中搜索 top_k 相似向量，返回相似度和索引
        D, I = self.index.search(q_vec, top_k)
        # 根据索引从 id_map 获取对应文本，并返回相似度
        return [(self.id_map[i], float(D[0][idx])) for idx, i in enumerate(I[0])]

    def save_index(self, folder_path: str):
        """
        将 FAISS 索引和文本数据保存到本地
        """
        import os
        os.makedirs(folder_path, exist_ok=True)  # 创建文件夹
        # 保存 FAISS 向量索引
        faiss.write_index(self.index, f"{folder_path}/faiss.index")
        import json
        # 保存文本内容
        with open(f"{folder_path}/texts.json", "w", encoding="utf-8") as f:
            json.dump(self.texts, f, ensure_ascii=False, indent=2)

    def load_index(self, folder_path: str):
        """
        从本地加载 FAISS 索引和文本
        """
        import json
        # 加载 FAISS 向量索引
        self.index = faiss.read_index(f"{folder_path}/faiss.index")
        # 加载文本
        with open(f"{folder_path}/texts.json", "r", encoding="utf-8") as f:
            self.texts = json.load(f)
        # 更新 id_map
        self.id_map = self.texts.copy()


# ------------------------
# 使用示例
# ------------------------
if __name__ == "__main__":
    texts = [
        "我喜欢自然语言处理",
        "自然语言处理很有趣",
        "我喜欢机器学习",
        "他是个刹车"
    ]

    text_s = [
        "我喜欢自然语言处理1",
        "自然语言处理很有趣1",
        "我喜欢机器学习1",
        "那是个傻子吗"
    ]

    folder = "faiss_db"  # 本地保存向量数据库的文件夹
    vector_dim = 384  # all-MiniLM-L6-v2 模型输出向量维度

    # 初始化向量数据库管理器
    db = VectorManager(vector_dim)
    # 添加文本并向量化
    db.add_texts(texts)
    db.add_texts(text_s)

    # 保存向量数据库到本地
    db.save_index(folder)

    # 测试从本地加载向量数据库
    db2 = VectorManager(vector_dim)
    db2.load_index(folder)

    # 查询与文本最相似的 top_k 文本
    print("与查询相似的文本:\n", db2.most_similar("他是个傻子"))

import faiss
import numpy as np


class VectorDatabase:
    def __init__(self, dimension, use_gpu=False):
        self.dimension = dimension
        self.use_gpu = use_gpu
        self.index = None
        self.id_to_data = {}
        self.next_id = 0

    def create_index(self, index_type="hnsw"):
        """创建索引"""
        if index_type == "flat":
            self.index = faiss.IndexFlatL2(self.dimension)
        elif index_type == "ivf":
            nlist = min(4096, 100000 // 39)  # 自适应设置
            quantizer = faiss.IndexFlatL2(self.dimension)
            self.index = faiss.IndexIVFFlat(quantizer, self.dimension, nlist)
        elif index_type == "hnsw":
            self.index = faiss.IndexHNSWFlat(self.dimension, 32)
        else:
            raise ValueError(f"不支持的索引类型: {index_type}")

        if self.use_gpu:
            res = faiss.StandardGpuResources()
            self.index = faiss.index_cpu_to_gpu(res, 0, self.index)

    def add_vectors(self, vectors, datas=None):
        """添加向量和数据"""
        vectors = np.array(vectors).astype('float32')

        # 归一化（如果使用余弦相似度）
        faiss.normalize_L2(vectors)

        # 分配ID
        start_id = self.next_id
        end_id = start_id + len(vectors)
        ids = np.arange(start_id, end_id)

        # 添加向量
        if isinstance(self.index, faiss.IndexIDMap):
            self.index.add_with_ids(vectors, ids)
        else:
            self.index = faiss.IndexIDMap(self.index)
            self.index.add_with_ids(vectors, ids)

        # 存储原始数据
        if datas is not None:
            for vec_id, data in zip(ids, datas):
                self.id_to_data[vec_id] = data

        self.next_id = end_id
        return ids

    def search(self, query_vector, k=10, threshold=None):
        """搜索相似向量"""
        query_vector = np.array(query_vector).astype('float32').reshape(1, -1)
        faiss.normalize_L2(query_vector)

        distances, indices = self.index.search(query_vector, k)

        # 过滤结果
        results = []
        for dist, idx in zip(distances[0], indices[0]):
            if idx != -1 and (threshold is None or dist >= threshold):
                result = {
                    'id': int(idx),
                    'score': float(dist),
                    'data': self.id_to_data.get(idx)
                }
                results.append(result)

        return results

    def save(self, path):
        """保存索引"""
        if not isinstance(self.index, faiss.IndexIDMap):
            self.index = faiss.IndexIDMap(self.index)
        faiss.write_index(faiss.index_gpu_to_cpu(self.index), path)

    def load(self, path):
        """加载索引"""
        self.index = faiss.read_index(path)
        if self.use_gpu:
            res = faiss.StandardGpuResources()
            self.index = faiss.index_cpu_to_gpu(res, 0, self.index)


# 使用示例
if __name__ == "__main__":
    # 初始化向量数据库
    db = VectorDatabase(dimension=128, use_gpu=False)
    db.create_index(index_type="hnsw")

    # 生成测试数据
    n_vectors = 10000
    vectors = np.random.randn(n_vectors, 128).astype('float32')

    # 添加数据
    datas = [f"data_{i}" for i in range(n_vectors)]
    db.add_vectors(vectors, datas)

    # 搜索
    query = np.random.randn(128).astype('float32')
    results = db.search(query, k=5)

    for result in results:
        print(f"ID: {result['id']}, Score: {result['score']:.4f}, Data: {result['data']}")
import re
from typing import List, Optional
from langchain.text_splitter import RecursiveCharacterTextSplitter

class FileSplitter:
    """
    文件切割器
    支持：
    1. 按段落切割
    2. 按句子切割
    3. 按固定长度切割（可设置重叠）
    4. 语义切割（可扩展与向量库结合）
    """

    def __init__(self):
        pass

    # ---------------------------------
    # 按段落切割
    # ---------------------------------
    @staticmethod
    def split_by_paragraph(text: str) -> List[str]:
        paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
        return paragraphs

    # ---------------------------------
    # 按句子切割
    # ---------------------------------
    @staticmethod
    def split_by_sentence(text: str) -> List[str]:
        # 简单按标点切割，可扩展使用 nltk / spacy
        sentences = re.split(r'(?<=[。！？\.\!\?])\s*', text)
        sentences = [s.strip() for s in sentences if s.strip()]
        return sentences

    # ---------------------------------
    # 按固定长度切割
    # ---------------------------------
    @staticmethod
    def split_by_length(text: str, chunk_size: int = 500, overlap: int = 50) -> List[str]:
        chunks = []
        start = 0
        while start < len(text):
            end = start + chunk_size
            chunks.append(text[start:end])
            start += chunk_size - overlap
        return chunks

    # ---------------------------------
    # 语义切割（使用 LangChain RecursiveCharacterTextSplitter）
    # 可自定义 chunk_size, chunk_overlap, separators
    # ---------------------------------
    @staticmethod
    def semantic_split(text: str, chunk_size: int = 500, chunk_overlap: int = 50,
                       separators: Optional[List[str]] = None) -> List[str]:
        if separators is None:
            separators = ["\n\n", "\n", ".", "!", "?"]  # 默认分隔符优先级
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            separators=separators
        )
        return splitter.split_text(text)

if __name__ == '__main__':
    splitter = FileSplitter()

    text = """这是第一段文本。
    这里是第一段的第二句。

    这是第二段文本。
    第二段有两句。"""

    # 按段落
    paragraphs = splitter.split_by_paragraph(text)
    print("段落切割:", paragraphs)

    # 按句子
    sentences = splitter.split_by_sentence(text)
    print("句子切割:", sentences)

    # 按固定长度
    chunks = splitter.split_by_length(text, chunk_size=10, overlap=2)
    print("固定长度切割:", chunks)

    # 语义切割
    semantic_chunks = splitter.semantic_split(text, chunk_size=20, chunk_overlap=5)
    print("语义切割:", semantic_chunks)


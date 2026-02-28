from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List


class BaseDocumentParser(ABC):
    """文档解析抽象基类。

    负责将不同格式的原始文件解析为系统统一的标准文本结构，不做任何存储操作。
    """

    @abstractmethod
    def parse_file(self, file_path: str) -> Dict:
        """解析单个文件为统一标准文本结构。

        Args:
            file_path: 文件路径（支持 txt、pdf、docx、md、py、xlsx、xls、ppt、pptx、csv、json、xml）

        Returns:
            dict: {"content": str, "file_name": str, "meta": dict}

        Raises:
            RAGException: 文件不存在、类型不支持或解析失败等。
        """

    @abstractmethod
    def parse_folder(self, folder_path: str) -> List[Dict]:
        """解析文件夹下所有支持格式的文件，返回结果列表。

        Args:
            folder_path: 文件夹路径

        Returns:
            list[dict]: 每个元素同 parse_file 输出格式

        Raises:
            RAGException: 文件夹不存在或解析失败等。
        """

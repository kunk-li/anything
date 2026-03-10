from __future__ import annotations

import hashlib
import logging
import math
import time
import uuid
from typing import Any, List, Optional

from ..config.config import EmbeddingModuleConfig
from ..core.base import BaseEmbedding
from ..model.data_model import EmbeddingRequest, EmbeddingResponse
from ..utils.tool_functions import (
    EmbeddingValidationError,
    clean_text,
    clean_texts,
    normalize_vector,
    normalize_vectors,
    validate_vector_dim,
)

from common_utils_module.core.impl import CommonUtils  # type: ignore
from config_module.core.impl import ConfigManager  # type: ignore
from log_module.core.impl import SystemLogger  # type: ignore
from exception_module.core.impl import RAGException  # type: ignore
from llm_adapter_module.core.impl import LLMService  # type: ignore
from sentence_transformers import SentenceTransformer  # type: ignore


def _deterministic_vector(text: str, dim: int) -> List[float]:
    """
    生成确定性伪向量，便于无外部依赖时调试。
    正式环境应由真实模型输出替代。
    """
    values: List[float] = []
    seed = text.encode("utf-8")
    counter = 0
    while len(values) < dim:
        digest = hashlib.sha256(seed + str(counter).encode("utf-8")).digest()
        for idx in range(0, len(digest), 4):
            chunk = digest[idx: idx + 4]
            if len(chunk) < 4:
                continue
            num = int.from_bytes(chunk, byteorder="big", signed=False)
            values.append((num % 2000000) / 1000000.0 - 1.0)
            if len(values) >= dim:
                break
        counter += 1
    return values[:dim]


class _BaseEmbeddingImpl(BaseEmbedding):
    """本地/远程实现的公共逻辑。"""

    def __init__(self) -> None:
        self.utils = CommonUtils()
        self.logger = SystemLogger()
        self.config = ConfigManager()
        self.config.load_config()

        self.vector_dim = int(self.config.get_config("vector_db.vector_dimension", 384))
        self.default_normalize = bool(
            self.config.get_config("llm.common.normalize_vector", True)
        )
        self.default_batch_size = int(self.config.get_config("llm.common.batch_size", 32))

    def _trace_id(self) -> str:
        return uuid.uuid4().hex

    def _handle_validation_error(self, exc: Exception) -> None:
        raise RAGException("PARAM_INVALID", str(exc))

    def _post_process_vectors(
        self,
        vectors: List[List[float]],
        normalize: bool,
        expected_dim: Optional[int] = None,
    ) -> List[List[float]]:
        if normalize:
            vectors = normalize_vectors(vectors)
        dim = expected_dim if expected_dim is not None else self.vector_dim
        for vector in vectors:
            validate_vector_dim(vector, dim)
        return vectors

    def _build_success_response(
        self,
        vectors: List[List[float]],
        start_time: float,
        trace_id: str,
    ) -> EmbeddingResponse:
        return EmbeddingResponse(
            code="SUCCESS",
            message="ok",
            vector_result=vectors,
            cost_time=round(time.time() - start_time, 6),
            trace_id=trace_id,
        )

    def _build_error_response(
        self,
        exc: Exception,
        start_time: float,
        trace_id: str,
    ) -> EmbeddingResponse:
        code = getattr(exc, "code", "EMBEDDING_CALL_FAILED")
        message = getattr(exc, "message", str(exc))
        return EmbeddingResponse(
            code=code,
            message=message,
            vector_result=None,
            cost_time=round(time.time() - start_time, 6),
            trace_id=trace_id,
        )


class STEmbedding(_BaseEmbeddingImpl):
    """
    本地 Sentence-BERT 向量模型实现类。
    设计上要求开发/测试环境可用；若缺少 sentence-transformers 则抛统一异常。
    """

    def __init__(self) -> None:
        super().__init__()
        self.model_name = self.config.get_config("embedding.model_name", "all-MiniLM-L6-v2")

        if SentenceTransformer is None:
            raise RAGException(
                "EMBEDDING_INIT_FAILED",
                "未安装 sentence-transformers，请执行：pip install sentence-transformers",
            )

        try:
            self.model = SentenceTransformer(self.model_name)
            actual_dim = getattr(self.model, "get_sentence_embedding_dimension", None)
            if callable(actual_dim):
                dim = actual_dim()
                if isinstance(dim, int) and dim > 0:
                    self.vector_dim = dim
            self.logger.info(
                f"本地 Embedding 模型初始化成功：{self.model_name}，向量维度：{self.vector_dim}"
            )
        except Exception as exc:
            raise RAGException("EMBEDDING_INIT_FAILED", f"本地 Embedding 初始化失败：{exc}") from exc

    def embed_text(self, text: str) -> List[float]:
        try:
            cleaned = clean_text(text)
            vector = self.model.encode(
                cleaned,
                normalize_embeddings=False,
                convert_to_numpy=False,
            )
            result = [float(v) for v in vector]
            result = self._post_process_vectors(
                [result],
                normalize=self.default_normalize,
                expected_dim=len(result),
            )[0]
            return result
        except EmbeddingValidationError as exc:
            self._handle_validation_error(exc)
        except RAGException:
            raise
        except Exception as exc:
            self.logger.exception("本地单条文本向量化失败")
            raise RAGException("EMBEDDING_CALL_FAILED", f"本地单条文本向量化失败：{exc}") from exc

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        try:
            cleaned = clean_texts(texts)
            vectors = self.model.encode(
                cleaned,
                batch_size=self.default_batch_size,
                normalize_embeddings=False,
                convert_to_numpy=False,
            )
            result = [[float(v) for v in vector] for vector in vectors]
            expected_dim = len(result[0]) if result else self.vector_dim
            result = self._post_process_vectors(
                result,
                normalize=self.default_normalize,
                expected_dim=expected_dim,
            )
            return result
        except EmbeddingValidationError as exc:
            self._handle_validation_error(exc)
        except RAGException:
            raise
        except Exception as exc:
            self.logger.exception("本地批量文本向量化失败")
            raise RAGException("EMBEDDING_CALL_FAILED", f"本地批量文本向量化失败：{exc}") from exc

    def call_embedding(self, request: EmbeddingRequest) -> EmbeddingResponse:
        start_time = time.time()
        trace_id = self._trace_id()

        try:
            if not isinstance(request, EmbeddingRequest):
                raise RAGException("PARAM_INVALID", "request 必须为 EmbeddingRequest 类型")

            input_type = request.input_type.upper().strip()
            normalize = request.normalize

            if input_type == "SINGLE":
                if not request.single_text:
                    raise RAGException("PARAM_MISSING", "single_text 不能为空")
                vector = self.embed_text(request.single_text)
                if normalize is False:
                    cleaned = clean_text(request.single_text)
                    raw_vector = self.model.encode(
                        cleaned,
                        normalize_embeddings=False,
                        convert_to_numpy=False,
                    )
                    vector = [float(v) for v in raw_vector]
                return self._build_success_response([vector], start_time, trace_id)

            if input_type == "BATCH":
                if not request.batch_texts:
                    raise RAGException("PARAM_MISSING", "batch_texts 不能为空")
                if request.batch_size:
                    self.default_batch_size = int(request.batch_size)
                vectors = self.embed_texts(request.batch_texts)
                if normalize is False:
                    cleaned = clean_texts(request.batch_texts)
                    raw_vectors = self.model.encode(
                        cleaned,
                        batch_size=self.default_batch_size,
                        normalize_embeddings=False,
                        convert_to_numpy=False,
                    )
                    vectors = [[float(v) for v in vector] for vector in raw_vectors]
                return self._build_success_response(vectors, start_time, trace_id)

            raise RAGException("PARAM_INVALID", "input_type 仅支持 SINGLE 或 BATCH")
        except Exception as exc:
            self.logger.exception("本地统一向量化调用失败")
            return self._build_error_response(exc, start_time, trace_id)


class LLMEmbedding(_BaseEmbeddingImpl):
    """
    远程向量模型实现类：对接 llm_adapter_module。
    生产环境推荐使用本类。
    """

    def __init__(self) -> None:
        super().__init__()
        self.llm_service = LLMService()
        self.default_vector_model = self.config.get_config(
            "llm.default_vector_model",
            "text-embedding-ada-002",
        )
        self.logger.info(
            f"远程 Embedding 服务初始化完成，默认模型：{self.default_vector_model}，向量维度：{self.vector_dim}"
        )

    def _call_remote(
        self,
        texts: List[str],
        model_name: Optional[str] = None,
    ) -> List[List[float]]:
        chosen_model = model_name or self.default_vector_model

        if hasattr(self.llm_service, "get_embeddings"):
            vectors = self.llm_service.get_embeddings(texts=texts, model_name=chosen_model)
        elif hasattr(self.llm_service, "embed_texts"):
            vectors = self.llm_service.embed_texts(texts=texts, model_name=chosen_model)
        else:
            raise RAGException(
                "EMBEDDING_CALL_FAILED",
                "LLMService 未提供 get_embeddings/embed_texts 接口",
            )

        if not isinstance(vectors, list) or not vectors:
            raise RAGException("EMBEDDING_CALL_FAILED", "远程向量服务返回为空")

        result = [[float(v) for v in vector] for vector in vectors]
        return result

    def embed_text(self, text: str) -> List[float]:
        try:
            cleaned = clean_text(text)
            vectors = self._call_remote([cleaned])
            vectors = self._post_process_vectors(
                vectors,
                normalize=self.default_normalize,
                expected_dim=len(vectors[0]),
            )
            return vectors[0]
        except EmbeddingValidationError as exc:
            self._handle_validation_error(exc)
        except RAGException:
            raise
        except Exception as exc:
            self.logger.exception("远程单条文本向量化失败")
            raise RAGException("EMBEDDING_CALL_FAILED", f"远程单条文本向量化失败：{exc}") from exc

    def embed_texts(self, texts: List[str]) -> List[List[float]]:
        try:
            cleaned = clean_texts(texts)
            vectors = self._call_remote(cleaned)
            expected_dim = len(vectors[0]) if vectors else self.vector_dim
            vectors = self._post_process_vectors(
                vectors,
                normalize=self.default_normalize,
                expected_dim=expected_dim,
            )
            return vectors
        except EmbeddingValidationError as exc:
            self._handle_validation_error(exc)
        except RAGException:
            raise
        except Exception as exc:
            self.logger.exception("远程批量文本向量化失败")
            raise RAGException("EMBEDDING_CALL_FAILED", f"远程批量文本向量化失败：{exc}") from exc

    def call_embedding(self, request: EmbeddingRequest) -> EmbeddingResponse:
        start_time = time.time()
        trace_id = self._trace_id()

        try:
            if not isinstance(request, EmbeddingRequest):
                raise RAGException("PARAM_INVALID", "request 必须为 EmbeddingRequest 类型")

            input_type = request.input_type.upper().strip()
            model_name = request.model_name or self.default_vector_model
            normalize = request.normalize

            if input_type == "SINGLE":
                if not request.single_text:
                    raise RAGException("PARAM_MISSING", "single_text 不能为空")

                cleaned = clean_text(request.single_text)
                vectors = self._call_remote([cleaned], model_name=model_name)
                if normalize:
                    vectors = normalize_vectors(vectors)
                validate_vector_dim(vectors[0], len(vectors[0]))
                return self._build_success_response(vectors, start_time, trace_id)

            if input_type == "BATCH":
                if not request.batch_texts:
                    raise RAGException("PARAM_MISSING", "batch_texts 不能为空")

                cleaned = clean_texts(request.batch_texts)
                vectors = self._call_remote(cleaned, model_name=model_name)
                if normalize:
                    vectors = normalize_vectors(vectors)
                expected_dim = len(vectors[0]) if vectors else self.vector_dim
                for vector in vectors:
                    validate_vector_dim(vector, expected_dim)
                return self._build_success_response(vectors, start_time, trace_id)

            raise RAGException("PARAM_INVALID", "input_type 仅支持 SINGLE 或 BATCH")
        except Exception as exc:
            self.logger.exception("远程统一向量化调用失败")
            return self._build_error_response(exc, start_time, trace_id)
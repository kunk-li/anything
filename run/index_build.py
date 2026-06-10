# -*- coding: utf-8 -*-
"""
最小可运行索引构建脚本
主链路：
    parse -> store -> chunk -> embed -> upsert

运行方式示例：
    python index_build.py --source-type folder --source-path ./documents
    python index_build.py --source-type file --source-path ./documents/demo.md
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from bootstrap import build_data_layer
from chunker_module import chunk_document, build_upsert_items


def parse_sources(parser: Any, source_type: str, source_path: str) -> List[Dict[str, Any]]:
    if parser is None:
        raise RuntimeError("document_parser 未构建成功")

    if source_type == "file":
        if hasattr(parser, "parse_file"):
            result = parser.parse_file(source_path)
            if isinstance(result, dict):
                return [result]
            raise RuntimeError("parse_file 返回格式非法")
        raise RuntimeError("document_parser 不支持 parse_file")

    if source_type == "folder":
        if hasattr(parser, "parse_folder"):
            result = parser.parse_folder(source_path)
            if isinstance(result, list):
                return result
            raise RuntimeError("parse_folder 返回格式非法")
        raise RuntimeError("document_parser 不支持 parse_folder")

    raise ValueError(f"不支持的 source_type: {source_type}")


def create_and_save_document(store: Any, item: Dict[str, Any]) -> Dict[str, Any]:
    """按 BaseDocumentStore 抽象契约创建并持久化文档。

    契约（见 data_layer/document_store_module/core/base.py）：
        create_document(content, file_name, file_type, content_hash) -> Dict[str, str]
        save_document(document) -> bool

    create_document 签名不含 meta；解析阶段产出的 meta 在 document 创建后
    再合并进 document["meta"]，便于下游 chunker / 引用回溯使用。
    """
    if store is None:
        raise RuntimeError("document_store 未构建成功")

    content = item.get("content", "")
    file_name = item.get("file_name") or Path(item.get("meta", {}).get("source_path", "unknown.txt")).name
    meta = item.get("meta") or {}
    source = meta.get("source", "local")

    file_type = Path(file_name).suffix.lower().lstrip(".") or "txt"
    content_hash = hashlib.md5(content.encode("utf-8")).hexdigest()

    document = store.create_document(
        content=content,
        file_name=file_name,
        file_type=file_type,
        content_hash=content_hash,
    )

    # 补齐后续 chunk / 引用回溯需要的 meta 字段（create_document 契约不含 meta）
    if isinstance(document, dict):
        document.setdefault("content", content)
        document.setdefault("file_name", file_name)
        document.setdefault("file_type", file_type)
        document.setdefault("content_hash", content_hash)
        if "meta" not in document or document["meta"] is None:
            document["meta"] = {}
        document["meta"].setdefault("source", source)
        for k, v in meta.items():
            document["meta"].setdefault(k, v)
        # 顶层 stored_path 字段随 info.json 持久化 (write_info_file 可选字段)
        if meta.get("stored_path"):
            document["stored_path"] = meta["stored_path"]

    store.save_document(document)

    return document


def embed_chunk_texts(embedding: Any, texts: List[str]) -> List[List[float]]:
    if embedding is None:
        raise RuntimeError("embedding 未构建成功")

    if hasattr(embedding, "embed_texts"):
        result = embedding.embed_texts(texts)
        if isinstance(result, dict):
            data = result.get("data")
            if isinstance(data, dict) and "items" in data:
                return [x.get("embedding") for x in data.get("items", [])]
            if "items" in result:
                return [x.get("embedding") for x in result.get("items", [])]
        if isinstance(result, list):
            return result

    if hasattr(embedding, "embed_text"):
        vectors = []
        for text in texts:
            v = embedding.embed_text(text)
            if isinstance(v, dict):
                if "embedding" in v:
                    vectors.append(v["embedding"])
                elif isinstance(v.get("data"), dict) and "embedding" in v["data"]:
                    vectors.append(v["data"]["embedding"])
                else:
                    raise RuntimeError("embed_text 返回格式非法")
            else:
                vectors.append(v)
        return vectors

    raise RuntimeError("embedding 不支持 embed_texts/embed_text")


def upsert_vectors(vector_db: Any, items: List[Dict[str, Any]]) -> Dict[str, Any]:
    if vector_db is None:
        raise RuntimeError("vector_db 未构建成功，请检查 faiss 依赖或 bootstrap 配置")
    if not hasattr(vector_db, "upsert_vectors"):
        raise RuntimeError("vector_db 不支持 upsert_vectors")
    return vector_db.upsert_vectors(items)


def build_index(
    source_type: str,
    source_path: str,
    chunk_size_tokens: int = 400,
    chunk_overlap_tokens: int = 80,
    data_layer: "Optional[Dict[str, Any]]" = None,
    bm25_retriever: Any = None,         # Task #49: 同步喂 BM25 索引
    bm25_index_path: Optional[str] = None,
) -> Dict[str, Any]:
    """构建索引. data_layer 不传时现 new (CLI 模式), 传入时复用 (ApiService 模式).

    Task #49: 若传入 bm25_retriever, 则在 upsert 向量后同步 add_chunks 到 BM25,
    并 (可选) 持久化到 bm25_index_path 让进程重启后可恢复。
    """
    if data_layer is None:
        data_layer = build_data_layer()

    parser = data_layer.get("document_parser")
    store = data_layer.get("document_store")
    embedding = data_layer.get("embedding")
    vector_db = data_layer.get("vector_db")

    # 早失败，报错更明确
    if parser is None:
        raise RuntimeError("document_parser 未构建成功")
    if store is None:
        raise RuntimeError("document_store 未构建成功")
    if embedding is None:
        raise RuntimeError("embedding 未构建成功，请检查 bootstrap 中的初始化方式")
    if vector_db is None:
        raise RuntimeError("vector_db 未构建成功，请检查 faiss 依赖或 bootstrap 配置")

    parsed_items = parse_sources(parser=parser, source_type=source_type, source_path=source_path)

    # 单文件模式把原始文件路径写进 meta -> info.json 的 stored_path,
    # DELETE /documents 据此回收 uploads/ 原件 (P5)
    if source_type == "file":
        for item in parsed_items:
            if not isinstance(item, dict):
                continue
            if not isinstance(item.get("meta"), dict):
                item["meta"] = {}
            item["meta"]["stored_path"] = str(Path(source_path).resolve())

    total_docs = 0
    total_chunks = 0
    total_vectors = 0
    docs_summary = []

    for item in parsed_items:
        raw_content = item.get("content", "")
        raw_file_name = item.get("file_name")

        # 跳过空内容，避免 document_store 直接报错
        if raw_content is None or not str(raw_content).strip():
            docs_summary.append(
                {
                    "doc_id": None,
                    "file_name": raw_file_name,
                    "chunk_count": 0,
                    "vector_count": 0,
                    "upsert_result": None,
                    "skipped": True,
                    "reason": "empty content after parsing",
                }
            )
            continue

        document = create_and_save_document(store, item)
        doc_id = document.get("doc_id")
        file_name = document.get("file_name")
        content = document.get("content", "")
        meta = document.get("meta") or {}
        source = meta.get("source", "local")

        chunks = chunk_document(
            doc_id=doc_id,
            content=content,
            file_name=file_name,
            source=source,
            chunk_size_tokens=chunk_size_tokens,
            chunk_overlap_tokens=chunk_overlap_tokens,
        )

        if not chunks:
            docs_summary.append(
                {
                    "doc_id": doc_id,
                    "file_name": file_name,
                    "chunk_count": 0,
                    "vector_count": 0,
                    "upsert_result": None,
                    "skipped": True,
                    "reason": "no chunks generated",
                }
            )
            continue

        vectors = embed_chunk_texts(embedding, [c["content"] for c in chunks])
        upsert_items = build_upsert_items(chunks, vectors)
        upsert_result = upsert_vectors(vector_db, upsert_items)

        # Task #49: 同步把 chunks 喂给 BM25 (失败仅 print, 不影响向量主链路)
        if bm25_retriever is not None:
            try:
                added = bm25_retriever.add_chunks(chunks)
                print(f"[bm25] +{added} chunks (size={bm25_retriever.size})")
            except Exception as e:
                print(f"[bm25] add_chunks 失败 (忽略): {e}")

        total_docs += 1
        total_chunks += len(chunks)
        total_vectors += len(vectors)

        docs_summary.append(
            {
                "doc_id": doc_id,
                "file_name": file_name,
                "chunk_count": len(chunks),
                "vector_count": len(vectors),
                "upsert_result": upsert_result,
            }
        )

    # Task #49: 持久化 BM25 索引 (仅当本次有新 chunks 落库时)
    if bm25_retriever is not None and bm25_index_path and total_chunks > 0:
        ok = bm25_retriever.save(bm25_index_path)
        print(f"[bm25] save {'OK' if ok else 'FAIL'}: path={bm25_index_path}, size={bm25_retriever.size}")

    return {
        "code": "SUCCESS",
        "message": "index build finished",
        "data": {
            "source_type": source_type,
            "source_path": source_path,
            "total_docs": total_docs,
            "total_chunks": total_chunks,
            "total_vectors": total_vectors,
            "documents": docs_summary,
        },
    }


def rebuild_index(
    data_layer: "Optional[Dict[str, Any]]" = None,
    bm25_retriever: Any = None,
    bm25_index_path: Optional[str] = None,
    chunk_size_tokens: int = 400,
    chunk_overlap_tokens: int = 80,
    progress_cb: Any = None,
) -> Dict[str, Any]:
    """全量重建 (P14): 清空向量库 + BM25, 以 document_store 已存解析文档为源重灌。

    用途: 清理已删文档残留 (compaction) / chunk 参数变更后重建 / 修复索引损坏。
    POST /index/build 走这里 (后台线程), progress_cb(done, total) 回报进度。
    """
    if data_layer is None:
        data_layer = build_data_layer()
    store = data_layer.get("document_store")
    embedding = data_layer.get("embedding")
    vector_db = data_layer.get("vector_db")
    if store is None or embedding is None or vector_db is None:
        raise RuntimeError("document_store/embedding/vector_db 未构建成功")

    docs = store.list_documents() or []
    total = len(docs)

    if hasattr(vector_db, "clear"):
        vector_db.clear()
    if bm25_retriever is not None:
        bm25_retriever.clear()

    rebuilt_docs = 0
    total_chunks = 0
    skipped: List[Dict[str, Any]] = []
    for i, item in enumerate(docs):
        doc_id = (item or {}).get("doc_id")
        document = store.get_document(doc_id) if doc_id else None
        content = (document or {}).get("content") or ""
        file_name = (document or {}).get("file_name") or "unknown.txt"
        if not str(content).strip():
            skipped.append({"doc_id": doc_id, "reason": "empty content"})
        else:
            chunks = chunk_document(
                doc_id=doc_id,
                content=content,
                file_name=file_name,
                source="rebuild",
                chunk_size_tokens=chunk_size_tokens,
                chunk_overlap_tokens=chunk_overlap_tokens,
            )
            if not chunks:
                skipped.append({"doc_id": doc_id, "reason": "no chunks generated"})
            else:
                vectors = embed_chunk_texts(embedding, [c["content"] for c in chunks])
                upsert_vectors(vector_db, build_upsert_items(chunks, vectors))
                if bm25_retriever is not None:
                    try:
                        bm25_retriever.add_chunks(chunks)
                    except Exception as e:
                        print(f"[bm25] rebuild add_chunks 失败 (忽略): {e}")
                rebuilt_docs += 1
                total_chunks += len(chunks)
        if progress_cb is not None:
            try:
                progress_cb(i + 1, total)
            except Exception:
                pass

    if bm25_retriever is not None and bm25_index_path:
        ok = bm25_retriever.save(bm25_index_path)
        print(f"[bm25] rebuild save {'OK' if ok else 'FAIL'}: size={bm25_retriever.size}")

    return {
        "code": "SUCCESS",
        "message": "index rebuild finished",
        "data": {
            "total_docs": total,
            "rebuilt_docs": rebuilt_docs,
            "total_chunks": total_chunks,
            "skipped": skipped,
        },
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-type", choices=["file", "folder"], required=True)
    parser.add_argument("--source-path", required=True)
    parser.add_argument("--chunk-size-tokens", type=int, default=400)
    parser.add_argument("--chunk-overlap-tokens", type=int, default=80)
    args = parser.parse_args()

    result = build_index(
        source_type=args.source_type,
        source_path=args.source_path,
        chunk_size_tokens=args.chunk_size_tokens,
        chunk_overlap_tokens=args.chunk_overlap_tokens,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
from __future__ import annotations

import os
import json
from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple, List

from .base import BaseDocumentStore
from ..config.config import DocumentStoreConfig
from ..utils.tool_functions import (
    now_str,
    generate_doc_id,
    get_document_path,
    get_info_file_path,
    get_file_size,
    calculate_content_hash,
    sanitize_file_name,
    normalize_file_type,
    is_uuid4,
    is_duplicate_aux,
    is_zombie_file,
    backup_file,
    json_dump,
    json_load,
)

# 注: ConfigManager / SystemLogger 由 deps 注入,不在此直接 import
from deps_module import BasicDeps

try:
    from exception_module.core.impl import SystemBaseException
except Exception:  # pragma: no cover
    class SystemBaseException(Exception):  # type: ignore
        def __init__(self, code: str, message: str, *, details=None):
            self.code = code
            self.message = message
            self.details = details or {}
            super().__init__(message)


class DocumentStoreException(SystemBaseException):
    """文档存储模块异常 (DOCUMENT_* / QUOTA_DOC_EXCEEDED)."""
    pass



class LocalDocumentStore(BaseDocumentStore):
    """Local filesystem implementation.

    Stores:
    - Document file: {storage_dir}/{doc_id}.{storage_type}
    - Info file:     {storage_dir}/{doc_id}.info.json (JSON, UTF-8)
    - Hash map:      {storage_dir}/.hash_doc_map.json (hash -> doc_id)
    """

    def __init__(self, deps: Optional[BasicDeps] = None, tenant_id: str = "default"):
        """
        Args:
            tenant_id: 租户标识 (Task #33). 数据按 tenant_id 切目录:
                <base_storage_dir>/<tenant_id>/{doc_id}.{ext}

                关键约定 (docs/multi-tenancy-design.md §10):
                - 写永远写到 <base>/<tenant_id>/
                - 读优先查子目录, 未找到时仅 default 租户 fallback 老扁平路径
        """
        from deps_module import build_basic_deps
        deps = deps or build_basic_deps()
        self.config_manager = deps.config
        self.logger = deps.logger
        self.tenant_id = self._validate_tenant_id(tenant_id)

        defaults = DocumentStoreConfig()

        # base = yaml 配置; storage_dir = base/<tenant_id>/ (实际工作目录)
        self.base_storage_dir = self.config_manager.get_config(
            "document_store.storage_dir", default=defaults.storage_dir
        )
        self.storage_dir = os.path.join(self.base_storage_dir, self.tenant_id)

        self.supported_file_types = self.config_manager.get_config(
            "document_store.supported_file_types", default=defaults.supported_file_types
        )
        if isinstance(self.supported_file_types, str):
            self.supported_file_types = [x.strip().lower() for x in self.supported_file_types.split(",") if x.strip()]

        self.hash_algorithm = str(
            self.config_manager.get_config("document_store.hash_algorithm", default=defaults.hash_algorithm)
        ).lower()

        self.zombie_threshold = int(
            self.config_manager.get_config("document_store.zombie_threshold_days", default=defaults.zombie_threshold_days)
        )

        self.core_doc_prefix = self.config_manager.get_config("document_store.core_doc_prefix", default=defaults.core_doc_prefix)
        if isinstance(self.core_doc_prefix, str):
            self.core_doc_prefix = [x.strip() for x in self.core_doc_prefix.split(",") if x.strip()]

        self.base_backup_dir = self.config_manager.get_config("document_store.backup_dir", default=defaults.backup_dir)
        self.backup_dir = os.path.join(self.base_backup_dir, self.tenant_id)
        self.hash_map_filename = self.config_manager.get_config("document_store.hash_map_filename", default=defaults.hash_map_filename)

        self.hash_map_path = os.path.join(self.storage_dir, self.hash_map_filename)
        # default 租户: 若子目录 hash_map 不存在, fallback 加载老扁平路径
        if not os.path.exists(self.hash_map_path) and self.tenant_id == "default":
            legacy = os.path.join(self.base_storage_dir, self.hash_map_filename)
            if os.path.exists(legacy):
                self._legacy_hash_map_path = legacy
                self.logger.info(
                    f"[migration] tenant=default 从老扁平 hash_map fallback: {legacy}"
                )
            else:
                self._legacy_hash_map_path = None
        else:
            self._legacy_hash_map_path = None

        self.hash_doc_map: Dict[str, str] = self._load_hash_map()

        self._init_storage_dir()

    @staticmethod
    def _validate_tenant_id(tenant_id: str) -> str:
        """字符集白名单校验 (深度防御, 防 path traversal). 见 docs/multi-tenancy-design.md §9.2"""
        import re
        if not isinstance(tenant_id, str) or not re.match(r"^[a-z0-9_-]{3,32}$", tenant_id):
            raise ValueError(
                f"tenant_id 必须是 3-32 位 [a-z0-9_-] 字符, 实际收到: {tenant_id!r}"
            )
        return tenant_id

    def _init_storage_dir(self) -> None:
        """Ensure storage_dir and backup_dir exist."""
        os.makedirs(self.storage_dir, exist_ok=True)
        os.makedirs(self.backup_dir, exist_ok=True)

    def _load_hash_map(self) -> Dict[str, str]:
        """Load persisted hash->doc_id map, or return empty dict.

        Task #33 PR3b: 若子目录 hash_map 不存在且当前是 default 租户,
        从老扁平路径 fallback 加载 (写仍写到子目录).
        """
        try:
            load_path = self.hash_map_path
            if not os.path.exists(load_path) and getattr(self, "_legacy_hash_map_path", None):
                load_path = self._legacy_hash_map_path
            if os.path.exists(load_path):
                data = json_load(load_path)
                if isinstance(data, dict):
                    # normalize values
                    return {str(k): str(v) for k, v in data.items()}
            return {}
        except Exception as e:
            self.logger.error(f"Failed to load hash map: {e}", exc_info=True)
            return {}

    def _save_hash_map(self) -> None:
        """Persist hash->doc_id map."""
        try:
            json_dump(self.hash_doc_map, self.hash_map_path)
        except Exception as e:
            self.logger.error(f"Failed to save hash map: {e}", exc_info=True)

    def _is_supported_type(self, file_type: str) -> bool:
        ft = normalize_file_type(file_type)
        return ft in set([x.lower() for x in self.supported_file_types])

    def _storage_type_for(self, file_type: str) -> str:
        ft = normalize_file_type(file_type)
        if ft == "unknown":
            return "txt"
        return ft

    def _doc_paths(self, doc_id: str, storage_type: str) -> Tuple[str, str]:
        doc_path = get_document_path(self.storage_dir, doc_id, storage_type)
        info_path = get_info_file_path(self.storage_dir, doc_id)
        return doc_path, info_path

    def _read_document_file(self, doc_path: str) -> str:
        with open(doc_path, "r", encoding="utf-8") as f:
            return f.read()

    def _write_document_file(self, doc_path: str, content: str) -> None:
        os.makedirs(os.path.dirname(doc_path), exist_ok=True)
        with open(doc_path, "w", encoding="utf-8") as f:
            f.write(content)

    def create_document(self, content: str, file_name: str, file_type: str, content_hash: str) -> Dict[str, str]:
        if content is None or content == "":
            self.logger.warning(f"create_document failed: empty content, file_name={file_name}")
            raise ValueError("content不能为空，无法创建文档")

        ft = normalize_file_type(file_type)
        if ft != "unknown" and not self._is_supported_type(ft):
            self.logger.warning(f"create_document failed: unsupported file_type={ft}, file_name={file_name}")
            raise ValueError(f"不支持的文件类型：{ft}")

        storage_type = self._storage_type_for(ft)

        safe_file_name = sanitize_file_name(file_name, storage_type)
        doc_id = generate_doc_id()

        # content_hash: verify/compute
        if not content_hash:
            content_hash = calculate_content_hash(content, self.hash_algorithm)
        else:
            # if provided hash doesn't match configured algorithm, recompute to be safe
            # (can't reliably infer algorithm from hash length due to collisions; only strict when mismatch lengths)
            expected_len = 32 if self.hash_algorithm == "md5" else 64
            if len(content_hash.strip()) != expected_len:
                content_hash = calculate_content_hash(content, self.hash_algorithm)

        create_time = now_str()
        update_time = create_time
        last_access_time = create_time

        doc_path, info_path = self._doc_paths(doc_id, storage_type)

        document: Dict[str, str] = {
            "doc_id": doc_id,
            "content": content,
            "file_name": safe_file_name,
            "file_type": ft,
            "storage_type": storage_type,
            "create_time": create_time,
            "update_time": update_time,
            "file_size": "0",  # updated after save
            "content_hash": content_hash.lower(),
            "last_access_time": last_access_time,
            "info_file_path": info_path.replace("\\", "/"),
            # not part of spec but used for duplicate supplementary check
            "content_length": str(len(content.encode("utf-8"))),
        }
        return document

    def _check_doc_quota(self, document: Dict[str, str]) -> None:
        """PR4b: 写入前查 quotas.<tid>.max_documents 配额。

        以 hash_doc_map 当前大小 + 1 (若新 content_hash) 估算下界, 超限抛 QUOTA_DOC_EXCEEDED。
        相同 content_hash 视为更新, 不算新文档, 不查 quota (允许重复写)。

        - 没配置 quota = 不限制 (向后兼容)
        - 配置 quota 为 0 = 拒绝任何新文档 (运维有意冻结)
        """
        try:
            key = f"quotas.{self.tenant_id}.max_documents"
            limit = self.config_manager.get_config(key, None)
        except Exception:
            limit = None
        if limit is None:
            return
        content_hash = str(document.get("content_hash") or "").lower()
        # 已存在该 hash -> 算作更新而非新增, 直接放行
        if content_hash and content_hash in self.hash_doc_map:
            return
        projected = len(self.hash_doc_map) + 1
        if projected > int(limit):
            self.logger.error(
                f"[quota] document quota exceeded: tenant={self.tenant_id} "
                f"projected={projected} limit={limit}"
            )
            raise DocumentStoreException(
                "QUOTA_DOC_EXCEEDED",
                f"文档数超过租户配额: projected={projected}, limit={limit} "
                f"(tenant={self.tenant_id})",
            )

    def save_document(self, document: Dict[str, str]) -> bool:
        required = [
            "doc_id", "content", "file_name", "file_type", "storage_type",
            "create_time", "update_time", "content_hash", "last_access_time", "info_file_path",
        ]
        for k in required:
            if k not in document:
                raise KeyError(f"document缺少必要字段：{k}")

        doc_id = document["doc_id"]
        if not is_uuid4(doc_id):
            raise ValueError("doc_id格式非法，需为UUID4")

        # PR4b: 配额硬限放在 try 之前, 让 DocumentStoreException 直接上抛 (不被 except 吞)
        self._check_doc_quota(document)

        storage_type = self._storage_type_for(document.get("storage_type") or document.get("file_type") or "unknown")
        doc_path, info_path = self._doc_paths(doc_id, storage_type)

        try:
            # persist content
            self._write_document_file(doc_path, document["content"])

            # update file_size
            file_size = get_file_size(doc_path)
            document["file_size"] = str(file_size)
            document["storage_type"] = storage_type
            document["info_file_path"] = info_path.replace("\\", "/")

            # write info file
            if not self.write_info_file(document):
                # if info file write fails, rollback doc file to avoid orphan
                try:
                    os.remove(doc_path)
                except Exception:
                    pass
                return False

            # update hash map
            self.hash_doc_map[str(document["content_hash"]).lower()] = doc_id
            self._save_hash_map()
            return True
        except Exception as e:
            self.logger.error(f"save_document failed: {e}", exc_info=True)
            return False

    def read_info_file(self, doc_id: str) -> Optional[Dict[str, str]]:
        if not is_uuid4(doc_id):
            raise ValueError("doc_id格式非法，需为UUID4")
        info_path = get_info_file_path(self.storage_dir, doc_id)
        try:
            if not os.path.exists(info_path):
                return None
            return json_load(info_path)
        except Exception as e:
            self.logger.warning(f"read_info_file failed for {doc_id}: {e}", exc_info=True)
            return None

    def write_info_file(self, document: Dict[str, str]) -> bool:
        required = [
            "doc_id", "content", "file_name", "file_type", "storage_type",
            "create_time", "update_time", "file_size", "content_hash", "last_access_time", "info_file_path",
        ]
        for k in required:
            if k not in document:
                raise KeyError(f"document缺少必要字段：{k}")

        doc_id = document["doc_id"]
        if not is_uuid4(doc_id):
            raise ValueError("doc_id格式非法，需为UUID4")

        info_path = get_info_file_path(self.storage_dir, doc_id)
        try:
            json_dump({k: document.get(k) for k in required}, info_path)
            return True
        except Exception as e:
            self.logger.error(f"write_info_file failed: {e}", exc_info=True)
            return False

    def list_documents(self) -> List[Dict[str, Any]]:
        """Task JJ (#70): 列出当前 tenant 下所有已索引文档的元信息.

        扫描 storage_dir 下所有 .info.json, 解析后返回简化结构 (前端表格用).
        失败的 info 文件跳过, 不抛错.
        """
        if not os.path.isdir(self.storage_dir):
            return []
        docs: List[Dict[str, Any]] = []
        try:
            for entry in os.listdir(self.storage_dir):
                if not entry.endswith(".info.json"):
                    continue
                doc_id = entry[: -len(".info.json")]
                if not is_uuid4(doc_id):
                    continue
                try:
                    info = self.read_info_file(doc_id) or {}
                except Exception:
                    continue
                docs.append({
                    "doc_id": doc_id,
                    "file_name": info.get("file_name"),
                    "file_type": info.get("file_type"),
                    "content_hash": info.get("content_hash"),
                    "content_length": info.get("content_length") or info.get("content_size"),
                    "created_time": info.get("created_time") or info.get("upload_time"),
                    "last_access_time": info.get("last_access_time"),
                    "source": (info.get("meta") or {}).get("source") if isinstance(info.get("meta"), dict) else None,
                })
        except OSError:
            return []
        # 按 created_time 倒序 (最近上传的在前)
        docs.sort(key=lambda d: str(d.get("created_time") or ""), reverse=True)
        return docs

    def get_document(self, doc_id: str) -> Optional[Dict[str, str]]:
        if not is_uuid4(doc_id):
            raise ValueError("doc_id格式非法，需为UUID4")

        info = self.read_info_file(doc_id)
        if not info:
            return None

        storage_type = self._storage_type_for(info.get("storage_type") or info.get("file_type") or "unknown")
        doc_path = get_document_path(self.storage_dir, doc_id, storage_type)
        if not os.path.exists(doc_path):
            # integrity issue -> treat as missing
            return None

        try:
            content = self._read_document_file(doc_path)
            info["content"] = content

            # update last_access_time
            info["last_access_time"] = now_str()
            info["file_size"] = str(get_file_size(doc_path))
            info["storage_type"] = storage_type
            info["info_file_path"] = get_info_file_path(self.storage_dir, doc_id).replace("\\", "/")

            self.write_info_file(info)
            return info
        except Exception as e:
            self.logger.error(f"get_document failed: {e}", exc_info=True)
            return None

    def update_document(self, doc_id: str, new_content: str, new_content_hash: str) -> bool:
        if not is_uuid4(doc_id):
            raise ValueError("doc_id格式非法，需为UUID4")
        if new_content is None or new_content == "":
            raise ValueError("new_content不能为空")

        info = self.read_info_file(doc_id)
        if not info:
            return False

        storage_type = self._storage_type_for(info.get("storage_type") or info.get("file_type") or "unknown")
        doc_path = get_document_path(self.storage_dir, doc_id, storage_type)
        if not os.path.exists(doc_path):
            return False

        try:
            self._write_document_file(doc_path, new_content)

            info["content"] = new_content
            info["update_time"] = now_str()
            info["last_access_time"] = info["update_time"]
            info["file_size"] = str(get_file_size(doc_path))

            if not new_content_hash:
                new_content_hash = calculate_content_hash(new_content, self.hash_algorithm)
            else:
                expected_len = 32 if self.hash_algorithm == "md5" else 64
                if len(new_content_hash.strip()) != expected_len:
                    new_content_hash = calculate_content_hash(new_content, self.hash_algorithm)
            info["content_hash"] = new_content_hash.lower()

            # persist info
            if not self.write_info_file(info):
                return False

            # update hash map (remove old hash entries pointing to doc_id)
            old_hashes = [h for h, did in self.hash_doc_map.items() if did == doc_id]
            for h in old_hashes:
                self.hash_doc_map.pop(h, None)
            self.hash_doc_map[info["content_hash"]] = doc_id
            self._save_hash_map()
            return True
        except Exception as e:
            self.logger.error(f"update_document failed: {e}", exc_info=True)
            return False

    def delete_document(self, doc_id: str) -> bool:
        if not is_uuid4(doc_id):
            raise ValueError("doc_id格式非法，需为UUID4")

        info = self.read_info_file(doc_id)
        storage_type = self._storage_type_for((info or {}).get("storage_type") or (info or {}).get("file_type") or "unknown")
        doc_path, info_path = self._doc_paths(doc_id, storage_type)

        success = True
        release_size = 0

        # delete doc file
        try:
            if os.path.exists(doc_path):
                release_size += get_file_size(doc_path)
                os.remove(doc_path)
        except Exception as e:
            self.logger.error(f"delete_document doc file failed: {e}", exc_info=True)
            success = False

        # delete info file
        try:
            if os.path.exists(info_path):
                release_size += get_file_size(info_path)
                os.remove(info_path)
        except Exception as e:
            self.logger.error(f"delete_document info file failed: {e}", exc_info=True)
            success = False

        # update hash map
        try:
            to_remove = [h for h, did in self.hash_doc_map.items() if did == doc_id]
            for h in to_remove:
                self.hash_doc_map.pop(h, None)
            self._save_hash_map()
        except Exception as e:
            self.logger.error(f"delete_document hash map update failed: {e}", exc_info=True)

        return success

    def _list_info_files(self) -> List[str]:
        return [
            os.path.join(self.storage_dir, f)
            for f in os.listdir(self.storage_dir)
            if f.endswith(".info.json")
        ]

    def _existing_docs_min_info(self) -> List[Dict[str, str]]:
        """Read all info files and extract minimal fields for duplicate check."""
        docs: List[Dict[str, str]] = []
        for p in self._list_info_files():
            try:
                info = json_load(p)
                docs.append({
                    "doc_id": info.get("doc_id"),
                    "file_name": info.get("file_name"),
                    "file_type": info.get("file_type"),
                    "content_length": info.get("content_length") or info.get("content_length") or info.get("content_length"),
                    # content_length might not exist in info; fallback to len(content) is too expensive; store when saving
                    "content_length": info.get("content_length") or info.get("content_length") or info.get("content_length"),
                })
                # If older info doesn't have content_length, compute from stored doc file size approx by reading content
                if not docs[-1].get("content_length"):
                    did = info.get("doc_id")
                    st = self._storage_type_for(info.get("storage_type") or info.get("file_type") or "unknown")
                    dp = get_document_path(self.storage_dir, did, st)
                    if os.path.exists(dp):
                        try:
                            c = self._read_document_file(dp)
                            docs[-1]["content_length"] = str(len(c.encode("utf-8")))
                        except Exception:
                            docs[-1]["content_length"] = "-1"
            except Exception:
                continue
        return docs

    def check_duplicate_file(
        self,
        content_hash: str,
        file_name: str,
        file_type: str,
        content_length: int,
        force_save: bool = False,
    ) -> Tuple[bool, Optional[str]]:
        if force_save:
            return False, None
        if not content_hash:
            raise ValueError("content_hash不能为空")
        if content_length < 0:
            raise ValueError("content_length不能小于0")

        ch = str(content_hash).lower()
        if ch in self.hash_doc_map:
            return True, self.hash_doc_map[ch]

        # supplementary check
        existing = self._existing_docs_min_info()
        dup = is_duplicate_aux(existing, file_name, file_type, content_length)
        if dup:
            return True, dup
        return False, None

    def identify_zombie_files(self, threshold_days: int) -> List[dict]:
        if threshold_days < 0:
            raise ValueError("threshold_days不能小于0")

        zombies: List[dict] = []
        now = datetime.now()
        threshold_dt = now - timedelta(days=threshold_days)

        # Build doc_id set from info files
        info_files = self._list_info_files()
        info_by_doc: Dict[str, Dict] = {}
        for ip in info_files:
            try:
                info = json_load(ip)
                did = info.get("doc_id")
                if did:
                    info_by_doc[str(did)] = info
            except Exception:
                # damaged info file: treat as zombie (type1/4)
                zombies.append({
                    "doc_id": None,
                    "path": ip,
                    "reason": "info_file_damaged",
                    "file_size": get_file_size(ip),
                })

        # Scan all files in storage dir for orphan/incomplete pairs
        all_files = [os.path.join(self.storage_dir, f) for f in os.listdir(self.storage_dir)]
        for fp in all_files:
            if os.path.isdir(fp) or fp.endswith(self.hash_map_filename):
                continue
            base = os.path.basename(fp)
            if base.endswith(".info.json"):
                doc_id = base[:-len(".info.json")]
                # if info file exists but doc file missing => type4
                info = info_by_doc.get(doc_id)
                if info:
                    st = self._storage_type_for(info.get("storage_type") or info.get("file_type") or "unknown")
                    dp = get_document_path(self.storage_dir, doc_id, st)
                    if not os.path.exists(dp):
                        zombies.append({
                            "doc_id": doc_id,
                            "path": fp,
                            "reason": "missing_document_file",
                            "file_size": get_file_size(fp),
                        })
                continue

            # document file: parse doc_id prefix before first dot
            m = base.split(".", 1)
            if len(m) != 2:
                continue
            doc_id, ext = m[0], m[1]
            info_path = get_info_file_path(self.storage_dir, doc_id)
            if not os.path.exists(info_path):
                zombies.append({
                    "doc_id": doc_id,
                    "path": fp,
                    "reason": "missing_info_file",
                    "file_size": get_file_size(fp),
                })

        # Type2: long-not-accessed and not-updated
        for doc_id, info in info_by_doc.items():
            # protect core docs by prefix
            if any(str(doc_id).startswith(pfx) for pfx in self.core_doc_prefix):
                continue
            try:
                if is_zombie_file(info, threshold_days):
                    # ensure both files exist; if not, covered by type4 already
                    st = self._storage_type_for(info.get("storage_type") or info.get("file_type") or "unknown")
                    dp = get_document_path(self.storage_dir, doc_id, st)
                    ip = get_info_file_path(self.storage_dir, doc_id)
                    if os.path.exists(dp) and os.path.exists(ip):
                        zombies.append({
                            "doc_id": doc_id,
                            "path": dp,
                            "reason": "stale_no_access_update",
                            "file_size": get_file_size(dp) + get_file_size(ip),
                            "last_access_time": info.get("last_access_time"),
                            "update_time": info.get("update_time"),
                        })
            except Exception as e:
                zombies.append({
                    "doc_id": doc_id,
                    "path": get_info_file_path(self.storage_dir, doc_id),
                    "reason": f"time_parse_error:{e}",
                    "file_size": get_file_size(get_info_file_path(self.storage_dir, doc_id)),
                })

        return zombies

    def clean_zombie_files(self, threshold_days: int, backup: bool = False) -> Dict[str, int]:
        zombies = self.identify_zombie_files(threshold_days)
        total = len(zombies)
        success = 0
        fail = 0
        release_size = 0

        for z in zombies:
            doc_id = z.get("doc_id")
            path = z.get("path")
            if not path:
                continue

            # protect core docs: if doc_id is known and matches prefix, skip
            if doc_id and any(str(doc_id).startswith(pfx) for pfx in self.core_doc_prefix):
                continue

            try:
                # Determine paired files
                to_delete = []
                if path.endswith(".info.json"):
                    to_delete.append(path)
                    if doc_id:
                        # try infer doc file from info if possible
                        info = None
                        try:
                            info = json_load(path)
                        except Exception:
                            info = None
                        if info:
                            st = self._storage_type_for(info.get("storage_type") or info.get("file_type") or "unknown")
                            dp = get_document_path(self.storage_dir, doc_id, st)
                            if os.path.exists(dp):
                                to_delete.append(dp)
                        else:
                            # unknown: delete any file starting with doc_id.
                            for f in os.listdir(self.storage_dir):
                                if f.startswith(f"{doc_id}.") and not f.endswith(".info.json"):
                                    to_delete.append(os.path.join(self.storage_dir, f))
                else:
                    to_delete.append(path)
                    if doc_id:
                        ip = get_info_file_path(self.storage_dir, doc_id)
                        if os.path.exists(ip):
                            to_delete.append(ip)

                # backup first
                if backup:
                    for fp in to_delete:
                        backup_file(fp, self.backup_dir)

                # delete
                for fp in set(to_delete):
                    if os.path.exists(fp):
                        release_size += get_file_size(fp)
                        os.remove(fp)

                # update hash map if doc_id known
                if doc_id:
                    old_hashes = [h for h, did in self.hash_doc_map.items() if did == doc_id]
                    for h in old_hashes:
                        self.hash_doc_map.pop(h, None)
                success += 1
            except Exception as e:
                self.logger.error(f"clean_zombie_files failed for {path}: {e}", exc_info=True)
                fail += 1

        self._save_hash_map()
        return {"total": total, "success": success, "fail": fail, "release_size": release_size}

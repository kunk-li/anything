# -*- coding: utf-8 -*-
"""
UploadJanitorMixin (从 impl.py 拆出 — 上传临时目录清理, 零行为变更)

    _start_upload_janitor / _clean_uploads_once

依赖 ApiService (self): __init__ 建的 upload 目录 / 清理配置 / logger。
"""
from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Dict


class UploadJanitorMixin:
    """后台周期清理过期上传临时文件。"""

    def _start_upload_janitor(self) -> None:
        """startup 钩子: retention>0 且注入了 document_store_factory 时起每日清理线程。"""
        days = int(self.config.get_config("api_service.upload_retention_days", 0) or 0)
        if days <= 0 or self.document_store_factory is None:
            return

        def _loop() -> None:
            while True:
                try:
                    removed = self._clean_uploads_once(days)
                    if removed.get("indexed_originals") or removed.get("screenshots"):
                        self.logger.info(f"[upload-janitor] 清理完成: {removed}")
                except Exception as e:
                    self.logger.warning(f"[upload-janitor] 清理失败 (下轮重试): {e}")
                time.sleep(24 * 3600)

        threading.Thread(target=_loop, name="upload-janitor", daemon=True).start()
        self.logger.info(f"[upload-janitor] 已启动: retention={days} 天, 每日扫描一次")

    def _clean_uploads_once(self, days: int) -> Dict[str, int]:
        """清一轮 uploads/:

        - 已索引的非图片原件 (info.stored_path 引用 + 超期): 内容已在 document_store,
          原件只是冗余拷贝, 安全删
        - uploads/screenshots/ 超期截图 (工具产物, 可再生)
        不删: 图片原件 (image_describe 会话中按路径回读)、未被索引引用的文件
        (可能是索引失败的孤本, 删了内容就没了)。
        """
        removed = {"indexed_originals": 0, "screenshots": 0}
        upload_dir = Path(
            self.config.get_config("api_service.upload_dir", "./uploads")
        ).resolve()
        if not upload_dir.is_dir():
            return removed
        cutoff = time.time() - days * 24 * 3600

        referenced: set = set()
        try:
            store = self.document_store_factory("default")
            for item in (store.list_documents() or []):
                sp = (item or {}).get("stored_path")
                if sp:
                    referenced.add(str(Path(sp).resolve()))
        except Exception as e:
            self.logger.warning(f"[upload-janitor] 读取已索引清单失败, 本轮跳过原件清理: {e}")
            referenced = set()

        image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
        if referenced:
            for p in upload_dir.iterdir():
                try:
                    if not p.is_file():
                        continue
                    if p.suffix.lower() in image_exts:
                        continue
                    if p.stat().st_mtime > cutoff:
                        continue
                    if str(p.resolve()) in referenced:
                        p.unlink()
                        removed["indexed_originals"] += 1
                except OSError:
                    continue

        shots = upload_dir / "screenshots"
        if shots.is_dir():
            for p in shots.iterdir():
                try:
                    if p.is_file() and p.stat().st_mtime <= cutoff:
                        p.unlink()
                        removed["screenshots"] += 1
                except OSError:
                    continue
        return removed

# file_manager/storage/s3.py
from __future__ import annotations

"""
S3/MinIO 存储实现（异步，基于 aioboto3）
注意：需要在环境中配置 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
或使用 MinIO 时提供 endpoint_url 和签名版本等配置。

支持的方法：
- put: 流式上传（直接向 S3 上传分块或缓冲写入）
- get: 流式下载（支持 Range）
- head: 获取对象元信息
- delete: 删除对象
- presign_get / presign_put: 预签名 URL（方便前端直传/直下）
- multipart_*: multipart init/part presign/complete/abort （可选）
"""

import asyncio
from typing import AsyncIterator, Optional, Dict, Tuple, Any, List
import aiofiles
import aioboto3
from botocore.exceptions import ClientError

from .base import Storage, StorageNotFound
from ..core.models import ObjectMeta, ByteRange

# 默认每次下载/上传内存缓冲块大小（可调）
_CHUNK_SIZE = 1024 * 1024  # 1MB


class S3Storage(Storage):
    def __init__(
        self,
        bucket: str,
        *,
        aws_access_key_id: Optional[str] = None,
        aws_secret_access_key: Optional[str] = None,
        region_name: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        aws_session_token: Optional[str] = None,
        acl: Optional[str] = None,
    ) -> None:
        """
        初始化 S3Storage
        - bucket: 目标 bucket 名称（若不存在需先创建）
        - endpoint_url: 若使用 MinIO，传入 http(s)://host:port
        - 若不传 creds，aioboto3 会使用环境变量或 IAM 角色
        """
        self.bucket = bucket
        self._session = aioboto3.Session()
        self._aws_kwargs: Dict[str, Any] = {}
        if aws_access_key_id:
            self._aws_kwargs["aws_access_key_id"] = aws_access_key_id
        if aws_secret_access_key:
            self._aws_kwargs["aws_secret_access_key"] = aws_secret_access_key
        if aws_session_token:
            self._aws_kwargs["aws_session_token"] = aws_session_token
        if region_name:
            self._aws_kwargs["region_name"] = region_name
        if endpoint_url:
            self._aws_kwargs["endpoint_url"] = endpoint_url

        self.acl = acl

    async def put(
        self,
        key: str,
        body: AsyncIterator[bytes],
        *,
        content_type: Optional[str] = None,
        metadata: Optional[Dict[str, str]] = None,
        content_length: Optional[int] = None,
    ) -> ObjectMeta:
        """
        简单实现：把异步流写入一个临时文件，然后用 aioboto3 的 upload_fileobj（在异步会话中）上传。
        优点：实现简单、健壮。缺点：会落盘（或增加内存/临时空间）。
        可改进：实现 multipart 直传（前端直传 + presign）或边读边流到 S3 UploadPart。
        """
        import tempfile
        tmp = None
        # 写临时文件（异步）
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp_path = tmp.name
        tmp.close()

        size = 0
        md5 = __import__("hashlib").md5()
        async with aiofiles.open(tmp_path, "wb") as f:
            async for chunk in body:
                if not chunk:
                    continue
                size += len(chunk)
                md5.update(chunk)
                await f.write(chunk)

        # 使用 aioboto3 上传临时文件
        session_ctx = self._session.client("s3", **self._aws_kwargs)
        async with session_ctx as client:
            extra_args = {}
            if content_type:
                extra_args["ContentType"] = content_type
            if metadata:
                extra_args["Metadata"] = metadata
            if self.acl:
                extra_args["ACL"] = self.acl

            # upload_fileobj 需要 file-like 对象；aioboto3 提供 put_object 但对大文件效率问题，
            # 这里使用 upload_fileobj via aioboto3's client.
            try:
                # 注意：put_object 也能，但部分 minio 环境推荐 upload_fileobj
                with open(tmp_path, "rb") as fobj:
                    await client.upload_fileobj(
                        fobj, Bucket=self.bucket, Key=key, ExtraArgs=extra_args
                    )
            except ClientError as e:
                raise RuntimeError(f"S3 上传失败: {e}") from e

            # 获取 head 对象元信息
            try:
                head = await client.head_object(Bucket=self.bucket, Key=key)
                obj_etag = head.get("ETag").strip('"') if head.get("ETag") else None
                obj_size = int(head.get("ContentLength", size))
                obj_ct = head.get("ContentType")
            except ClientError:
                obj_etag = md5.hexdigest()
                obj_size = size
                obj_ct = content_type

        # 清理临时文件
        try:
            import os
            os.remove(tmp_path)
        except Exception:
            pass

        return ObjectMeta(key=key, size=obj_size, etag=obj_etag, content_type=obj_ct)

    async def head(self, key: str) -> ObjectMeta:
        session_ctx = self._session.client("s3", **self._aws_kwargs)
        async with session_ctx as client:
            try:
                head = await client.head_object(Bucket=self.bucket, Key=key)
            except ClientError as e:
                # 404 或 NoSuchKey 等映射为 StorageNotFound
                raise StorageNotFound(key) from e
            size = int(head.get("ContentLength", 0))
            etag = head.get("ETag").strip('"') if head.get("ETag") else None
            content_type = head.get("ContentType")
            return ObjectMeta(key=key, size=size, etag=etag, content_type=content_type)

    async def get(
        self,
        key: str,
        *,
        byte_range: Optional[ByteRange] = None,
    ) -> Tuple[AsyncIterator[bytes], ObjectMeta]:
        session_ctx = self._session.client("s3", **self._aws_kwargs)
        async with session_ctx as client:
            # head first to get size & validate existence
            try:
                head = await client.head_object(Bucket=self.bucket, Key=key)
            except ClientError as e:
                raise StorageNotFound(key) from e

            size = int(head.get("ContentLength", 0))
            etag = head.get("ETag").strip('"') if head.get("ETag") else None
            content_type = head.get("ContentType")
            meta = ObjectMeta(key=key, size=size, etag=etag, content_type=content_type)

            # 构造 Range header（如果传入）
            range_header = None
            if byte_range is not None:
                start = max(0, byte_range.start)
                end = byte_range.end
                if end is None:
                    range_header = f"bytes={start}-"
                else:
                    range_header = f"bytes={start}-{end}"

            # 使用 get_object 获取 streaming body（aioboto3 返回 StreamingBody 支持 async iterator）
            try:
                if range_header:
                    resp = await client.get_object(Bucket=self.bucket, Key=key, Range=range_header)
                else:
                    resp = await client.get_object(Bucket=self.bucket, Key=key)
            except ClientError as e:
                # 如果 Range 不满足，会抛出 416 之类，需要上层判断并抛出对应异常
                raise

            body = resp["Body"]

            async def streamer() -> AsyncIterator[bytes]:
                # aioboto3 的 Body 是 async iterator 风格：await body.read(n)
                try:
                    while True:
                        chunk = await body.read(_CHUNK_SIZE)
                        if not chunk:
                            break
                        yield chunk
                finally:
                    # 关闭 body
                    await body.close()

            return streamer(), meta

    async def delete(self, key: str) -> None:
        session_ctx = self._session.client("s3", **self._aws_kwargs)
        async with session_ctx as client:
            try:
                await client.delete_object(Bucket=self.bucket, Key=key)
            except ClientError:
                # 幂等：忽略不存在错误
                return

    # ---------------- 可选：预签名 URL ----------------
    async def presign_get(self, key: str, *, expires_in: int = 900) -> Dict[str, Any]:
        """
        返回用于下载的预签名 URL（GET），返回 dict 包含 url 与 headers（如果需要）。
        """
        session_ctx = self._session.client("s3", **self._aws_kwargs)
        async with session_ctx as client:
            url = await client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.bucket, "Key": key},
                ExpiresIn=expires_in,
            )
            return {"url": url, "headers": {}}

    async def presign_put(
        self, key: str, *, expires_in: int = 900, content_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        返回用于上传的预签名 PUT URL（注意：PUT 对某些前端限制较多，可用 POST form upload 替代）
        """
        session_ctx = self._session.client("s3", **self._aws_kwargs)
        async with session_ctx as client:
            params = {"Bucket": self.bucket, "Key": key}
            if content_type:
                params["ContentType"] = content_type
            url = await client.generate_presigned_url(
                ClientMethod="put_object",
                Params=params,
                ExpiresIn=expires_in,
            )
            return {"url": url, "headers": {"Content-Type": content_type} if content_type else {}}

    # -------------- multipart (高阶能力，可按需实现) ----------------
    async def multipart_init(self, key: str, *, content_type: Optional[str] = None) -> str:
        session_ctx = self._session.client("s3", **self._aws_kwargs)
        async with session_ctx as client:
            extra = {}
            if content_type:
                extra["ContentType"] = content_type
            resp = await client.create_multipart_upload(Bucket=self.bucket, Key=key, **extra)
            return resp["UploadId"]

    async def multipart_presign_part(
        self, key: str, upload_id: str, part_number: int, *, expires_in: int = 900
    ) -> Dict[str, Any]:
        """
        生成某个 part 的预签名 URL。上层客户端可以按此直传到 S3。
        """
        session_ctx = self._session.client("s3", **self._aws_kwargs)
        async with session_ctx as client:
            url = await client.generate_presigned_url(
                ClientMethod="upload_part",
                Params={"Bucket": self.bucket, "Key": key, "UploadId": upload_id, "PartNumber": part_number},
                ExpiresIn=expires_in,
            )
            return {"url": url, "headers": {}}

    async def multipart_complete(self, key: str, upload_id: str, parts: List[Dict[str, Any]]) -> ObjectMeta:
        """
        parts: [{"PartNumber": 1, "ETag": "..."}]
        """
        session_ctx = self._session.client("s3", **self._aws_kwargs)
        async with session_ctx as client:
            resp = await client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={"Parts": parts},
            )
            # complete 返回可能包含 Location/Bucket/Key/ETag
            # 再 head 一次以获取 size/content-type
            head = await client.head_object(Bucket=self.bucket, Key=key)
            return ObjectMeta(
                key=key,
                size=int(head.get("ContentLength", 0)),
                etag=head.get("ETag").strip('"') if head.get("ETag") else None,
                content_type=head.get("ContentType"),
            )

    async def multipart_abort(self, key: str, upload_id: str) -> None:
        session_ctx = self._session.client("s3", **self._aws_kwargs)
        async with session_ctx as client:
            await client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)

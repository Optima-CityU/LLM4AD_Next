"""
S3 兼容存储服务封装（RustFS）。

提供文件上传、下载、删除、列举等操作的统一接口，
支持批量操作和预签名 URL 生成。
"""

from __future__ import annotations

import io
import re
from pathlib import Path
from typing import IO
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import settings


class Storage:
    """S3 兼容存储客户端。

    封装 boto3 的 S3 操作，提供简洁的文件管理接口。
    初始化时自动确保默认存储桶存在。
    """

    def __init__(self, default_bucket: str = "llm4ad"):
        """初始化 S3 客户端，并确保默认存储桶可用。

        Args:
            default_bucket: 默认存储桶名称，未指定 bucket 时使用该值。
        """
        self.default_bucket = default_bucket
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.RUSTFS_ENDPOINT,
            aws_access_key_id=settings.RUSTFS_ACCESS_KEY,
            aws_secret_access_key=settings.RUSTFS_SECRET_KEY,
            config=Config(signature_version="s3v4", max_pool_connections=50),
            region_name="us-east-1",
        )

        # RustFS sends an unsolicited 100 Continue that breaks urllib3 parsing.
        def _strip_expect(request, **_):
            try:
                request.headers.pop("Expect", None)
            except Exception:
                pass

        self.client.meta.events.register("before-send.s3.*", _strip_expect)
        self.ensure_bucket(self.default_bucket)

    def _bucket(self, bucket: str | None) -> str:
        """获取存储桶名称，未指定时使用默认桶。"""
        return bucket or self.default_bucket

    def ensure_bucket(self, bucket: str | None = None) -> None:
        """确保存储桶存在，不存在则自动创建。"""
        b = self._bucket(bucket)
        try:
            self.client.head_bucket(Bucket=b)
        except ClientError:
            self.client.create_bucket(Bucket=b)

    def upload(
        self, key: str, data: bytes | IO[bytes], bucket: str | None = None, content_type: str | None = None
    ) -> None:
        """上传单个文件，支持 bytes 或文件对象。"""
        extra = {"ContentType": content_type} if content_type else {}
        if isinstance(data, bytes):
            data = io.BytesIO(data)
        self.client.upload_fileobj(data, self._bucket(bucket), key, ExtraArgs=extra or None)

    def download(self, key: str, bucket: str | None = None) -> bytes:
        """下载文件并返回字节内容。"""
        buf = io.BytesIO()
        self.client.download_fileobj(self._bucket(bucket), key, buf)
        buf.seek(0)
        return buf.read()

    def iter_download(self, key: str, bucket: str | None = None):
        """Yield bounded chunks and release the object connection when closed."""
        body = self.client.get_object(Bucket=self._bucket(bucket), Key=key)["Body"]
        try:
            yield from body.iter_chunks(chunk_size=1024 * 1024)
        finally:
            body.close()

    def delete(self, key: str, bucket: str | None = None) -> None:
        """删除指定对象。"""
        self.client.delete_object(Bucket=self._bucket(bucket), Key=key)

    def delete_many(self, keys: list[str], bucket: str | None = None) -> None:
        """批量删除对象（S3 ``DeleteObjects`` 每次最多 1000 个 key）。"""
        if not keys:
            return
        b = self._bucket(bucket)
        for i in range(0, len(keys), 1000):
            batch = keys[i:i + 1000]
            self.client.delete_objects(
                Bucket=b,
                Delete={"Objects": [{"Key": k} for k in batch], "Quiet": True},
            )

    def upload_files(
        self, prefix: str, files: list[tuple[str, IO[bytes], str | None]], bucket: str | None = None
    ) -> list[str]:
        """批量上传文件到同一前缀目录下。

        Args:
            prefix: 存储路径前缀
            files: 文件列表，每项为 (文件名, 文件对象, Content-Type)
            bucket: 存储桶名称

        Returns:
            上传成功的所有 key 列表
        """
        keys: list[str] = []
        for filename, fileobj, content_type in files:
            key = f"{prefix}/{filename}".replace("//", "/")
            self.upload(key, fileobj, bucket=bucket, content_type=content_type)
            keys.append(key)
        return keys

    def download_files_local(
        self,
        key: str,
        local_path: str,
        bucket: str | None = None,
        dir_prefix: str = "",
        strip_first_level: bool = False,
    ):
        """批量下载指定前缀下的所有文件到本地目录，保留相对路径结构。

        Args:
            key: S3 路径前缀
            local_path: 本地目标目录
            bucket: 存储桶名称
            dir_prefix: 本地目录前缀，为每个顶层目录添加该前缀
            strip_first_level: 是否去掉相对路径的第一层目录

        Returns:
            下载成功返回 True，无文件时返回 False
        """
        key_path = key.strip("/")
        local_path = Path(local_path)

        keys = self.list_objects(prefix=key_path, bucket=bucket)
        if not keys:
            return False

        for key in keys:
            relative = key[len(key_path):].lstrip("/")
            if not relative:
                continue
            if strip_first_level:
                parts = relative.split("/", 1)
                if len(parts) < 2:
                    continue
                relative = parts[1]
            if dir_prefix:
                parts = relative.split("/", 1)
                parts[0] = dir_prefix + parts[0]
                relative = "/".join(parts)
            dest = local_path / relative
            dest.parent.mkdir(parents=True, exist_ok=True)
            data = self.download(key)
            dest.write_bytes(data)
        return True

    def copy_objects(self, src_prefix: str, dst_prefix: str, bucket: str | None = None) -> list[str]:
        """将指定前缀下的所有对象复制到新前缀下，保留相对路径结构。

        Args:
            src_prefix: 源路径前缀
            dst_prefix: 目标路径前缀
            bucket: 存储桶名称

        Returns:
            复制后的目标 key 列表
        """
        b = self._bucket(bucket)
        src_prefix = src_prefix.strip("/")
        dst_prefix = dst_prefix.strip("/")

        src_keys = self.list_objects(prefix=src_prefix, bucket=bucket)
        dst_keys: list[str] = []
        for key in src_keys:
            relative = key[len(src_prefix):].lstrip("/")
            if not relative:
                continue
            dst_key = f"{dst_prefix}/{relative}"
            self.client.copy_object(
                Bucket=b,
                CopySource={"Bucket": b, "Key": key},
                Key=dst_key,
            )
            dst_keys.append(dst_key)
        return dst_keys

    def list_objects(self, prefix: str, bucket: str | None = None) -> list[str]:
        """列出指定前缀下的所有对象 key（自动分页）。"""
        b = self._bucket(bucket)
        keys: list[str] = []
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=b, Prefix=prefix):
            for obj in page.get("Contents", []):
                keys.append(obj["Key"])
        return keys

    def get_prefix_total_size(self, prefix: str, bucket: str | None = None) -> int:
        """计算指定前缀下所有对象的总大小（字节）。"""
        b = self._bucket(bucket)
        total = 0
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=b, Prefix=prefix):
            for obj in page.get("Contents", []):
                total += obj.get("Size", 0)
        return total

    def presigned_url(
        self,
        key: str,
        bucket: str | None = None,
        expires: int = 3600,
        filename: str | None = None,
    ) -> str:
        """生成预签名下载 URL。

        强制 ``Content-Disposition: attachment`` 并将 ``Content-Type`` 覆盖为
        ``application/octet-stream``，避免浏览器渲染用户上传的 HTML/SVG/PDF/JS
        等内容导致的存储型 XSS。文件名支持非 ASCII，通过 RFC 6266 ``filename*``
        编码下发。

        Args:
            key: 对象 key
            bucket: 存储桶名称
            expires: 有效期（秒），默认 1 小时
            filename: 下载时显示的文件名，未指定则取 key 的 basename

        Returns:
            预签名 URL 字符串
        """
        name = filename or key.rsplit("/", 1)[-1] or "download"
        return self.client.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": self._bucket(bucket),
                "Key": key,
                "ResponseContentDisposition": _attachment_disposition(name),
                "ResponseContentType": "application/octet-stream",
            },
            ExpiresIn=expires,
        )


def _attachment_disposition(filename: str) -> str:
    """构造 ``Content-Disposition: attachment`` 头值。

    防御 header injection（剥离 CR/LF/双引号/反斜杠），并按 RFC 6266 同时给出
    ASCII fallback ``filename`` 与 UTF-8 编码的 ``filename*``，覆盖新旧浏览器。
    """
    cleaned = re.sub(r'[\r\n"\\]', "", filename) or "download"
    ascii_safe = cleaned.encode("ascii", errors="ignore").decode("ascii") or "download"
    encoded = quote(cleaned, safe="")
    return f'attachment; filename="{ascii_safe}"; filename*=UTF-8\'\'{encoded}'


# 全局单例
storage = Storage()

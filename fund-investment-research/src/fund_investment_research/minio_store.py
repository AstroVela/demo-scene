"""Small MinIO boundary with no credential-bearing repr or output."""

from __future__ import annotations

import io

from minio import Minio

from .config import MinioConfig


class MinioStore:
    def __init__(self, config: MinioConfig):
        self.config = config
        self.client = Minio(
            config.endpoint,
            access_key=config.access_key,
            secret_key=config.secret_key,
            secure=config.secure,
        )

    def probe(self) -> None:
        self.client.bucket_exists(self.config.bucket)

    def ensure_bucket(self) -> None:
        if not self.client.bucket_exists(self.config.bucket):
            self.client.make_bucket(self.config.bucket)

    def put_bytes(
        self,
        object_key: str,
        content: bytes,
        *,
        content_type: str,
    ) -> None:
        self.client.put_object(
            self.config.bucket,
            object_key,
            io.BytesIO(content),
            len(content),
            content_type=content_type,
        )

    def get_bytes(self, bucket: str, object_key: str) -> bytes:
        response = self.client.get_object(bucket, object_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def stat(self, bucket: str, object_key: str):
        return self.client.stat_object(bucket, object_key)

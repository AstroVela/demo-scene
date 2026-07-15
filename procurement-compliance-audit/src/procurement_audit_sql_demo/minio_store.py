"""MinIO object primitives used by fixture loading, OCR, and AI requests."""

from __future__ import annotations

import io

from minio import Minio

from .config import MinioConfig


class MinioStore:
    def __init__(self, config: MinioConfig):
        self.client = Minio(
            config.endpoint,
            access_key=config.access_key,
            secret_key=config.secret_key,
            secure=config.secure,
        )

    def probe(self) -> None:
        self.client.list_buckets()

    def ensure_bucket(self, bucket: str) -> None:
        if not self.client.bucket_exists(bucket):
            self.client.make_bucket(bucket)

    def get_bytes(self, bucket: str, object_key: str) -> bytes:
        response = self.client.get_object(bucket, object_key)
        try:
            return response.read()
        finally:
            response.close()
            response.release_conn()

    def put_bytes(
        self,
        bucket: str,
        object_key: str,
        value: bytes,
        content_type: str,
    ) -> None:
        self.client.put_object(
            bucket,
            object_key,
            io.BytesIO(value),
            len(value),
            content_type=content_type,
        )

    def remove_prefix(self, bucket: str, prefix: str) -> None:
        for item in self.client.list_objects(bucket, prefix=prefix, recursive=True):
            self.client.remove_object(bucket, item.object_name)

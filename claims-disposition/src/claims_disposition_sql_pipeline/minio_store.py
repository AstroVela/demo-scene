"""MinIO object primitives used by fixtures and Vane UDFs."""

from __future__ import annotations

import hashlib
import io

from minio import Minio
from minio.error import S3Error

from .config import MinioConfig


class MinioStore:
    """Provide the object operations used by probes, OCR, and AI inputs."""

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

    def exists(self, bucket: str, object_key: str) -> bool:
        try:
            self.client.stat_object(bucket, object_key)
            return True
        except S3Error as exc:
            if exc.code in {"NoSuchKey", "NoSuchObject", "NoSuchBucket"}:
                return False
            raise

    def get_bytes(self, bucket: str, object_key: str) -> bytes:
        """Read one source object and always release the HTTP connection."""

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

    def sha256(self, bucket: str, object_key: str) -> str | None:
        if not self.exists(bucket, object_key):
            return None
        return hashlib.sha256(self.get_bytes(bucket, object_key)).hexdigest()

    def remove_prefix(self, bucket: str, prefix: str) -> None:
        for item in self.client.list_objects(bucket, prefix=prefix, recursive=True):
            self.client.remove_object(bucket, item.object_name)

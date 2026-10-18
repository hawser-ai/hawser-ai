"""S3-compatible artifact store and object reader (blueprint §19)."""

from __future__ import annotations

from typing import Any

from hawser_server.config import ServerConfig


class S3ArtifactStore:
    """Content-addressed objects under ``<tenant>/<hh>/<hex>`` in one bucket."""

    def __init__(
        self,
        bucket: str,
        *,
        endpoint_url: str | None = None,
        region: str | None = None,
        access_key: str | None = None,
        secret_key: str | None = None,
        presign_ttl_s: int = 900,
        client: Any | None = None,
    ) -> None:
        self.bucket = bucket
        self.presign_ttl_s = presign_ttl_s
        if client is None:
            import boto3  # noqa: PLC0415 - optional at import time

            client = boto3.client(
                "s3",
                endpoint_url=endpoint_url,
                region_name=region,
                aws_access_key_id=access_key,
                aws_secret_access_key=secret_key,
            )
        self.client = client

    def put_bytes(self, key: str, data: bytes) -> None:
        if self.exists(key):
            return
        self.client.put_object(Bucket=self.bucket, Key=key, Body=data)

    def get_bytes(self, key: str) -> bytes:
        obj = self.client.get_object(Bucket=self.bucket, Key=key)
        return obj["Body"].read()

    def exists(self, key: str) -> bool:
        try:
            self.client.head_object(Bucket=self.bucket, Key=key)
        except Exception as exc:
            code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey", "NotFound"):
                return False
            raise
        return True

    def url(self, key: str, *, ttl_s: int = 900) -> str:
        return self.client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self.bucket, "Key": key},
            ExpiresIn=min(ttl_s, self.presign_ttl_s),
        )

    def ensure_bucket(self) -> None:
        try:
            self.client.head_bucket(Bucket=self.bucket)
        except Exception:
            self.client.create_bucket(Bucket=self.bucket)

    def read_uri(self, uri: str) -> bytes:
        """Resolve an ``s3://bucket/key`` input through the same credentials."""
        if not uri.startswith("s3://"):
            raise ValueError(f"only s3:// inputs are supported, got {uri!r}")
        bucket, _, key = uri.removeprefix("s3://").partition("/")
        obj = self.client.get_object(Bucket=bucket, Key=key)
        return obj["Body"].read()


def s3_store_from_config(cfg: ServerConfig, *, client: Any | None = None) -> S3ArtifactStore:
    assert cfg.s3_bucket is not None
    return S3ArtifactStore(
        cfg.s3_bucket,
        endpoint_url=cfg.s3_endpoint_url,
        region=cfg.s3_region,
        access_key=cfg.s3_access_key,
        secret_key=cfg.s3_secret_key,
        presign_ttl_s=cfg.s3_presign_ttl_s,
        client=client,
    )


__all__ = ["S3ArtifactStore", "s3_store_from_config"]

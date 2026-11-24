"""Almacenamiento S3 compatible (MinIO en el stack local, S3 u otro compatible en producción).

- Usa ``S3_ENDPOINT`` (URL interna, p.ej. ``http://minio:9000``) para todas las operaciones.
- Las descargas NO usan URLs presignadas: la API hace de proxy en streaming tras verificar
  permisos, de modo que el bucket nunca se expone al navegador (``S3_PUBLIC_ENDPOINT`` no es
  necesario para los adjuntos).
- El bucket se crea si no existe (una sola vez por proceso, de forma perezosa).
"""

from __future__ import annotations

import logging
import threading
from typing import Any, BinaryIO

from app.core.config import settings
from app.services.storage.base import (
    DEFAULT_CHUNK_SIZE,
    ObjectNotFound,
    StorageUnavailable,
    StoredObject,
)

logger = logging.getLogger(__name__)

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NoSuchBucket", "NotFound"}


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None) or {}
    return str(response.get("Error", {}).get("Code", ""))


class S3Storage:
    """Implementación de ``ObjectStorage`` sobre boto3."""

    def __init__(
        self,
        *,
        endpoint_url: str | None,
        bucket: str,
        access_key: str | None,
        secret_key: str | None,
        region: str = "us-east-1",
    ) -> None:
        self.endpoint_url = endpoint_url or None
        self.bucket = bucket
        self.region = region or "us-east-1"
        self._access_key = access_key or None
        self._secret_key = secret_key or None
        self._client: Any = None
        self._client_lock = threading.Lock()
        self._bucket_ready = False
        self._bucket_lock = threading.Lock()

    @classmethod
    def from_settings(cls) -> "S3Storage":
        return cls(
            endpoint_url=settings.s3_endpoint,
            bucket=settings.s3_bucket,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            region=settings.s3_region,
        )

    # ------------------------------------------------------------------ cliente

    @property
    def client(self) -> Any:
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = self._build_client()
        return self._client

    def _build_client(self) -> Any:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - dependencia declarada
            raise StorageUnavailable("boto3 no está instalado") from exc
        config = Config(
            signature_version="s3v4",
            s3={"addressing_style": "path"},
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=5,
            read_timeout=60,
        )
        kwargs: dict[str, Any] = {"region_name": self.region, "config": config}
        if self.endpoint_url:
            kwargs["endpoint_url"] = self.endpoint_url
        if self._access_key and self._secret_key:
            kwargs["aws_access_key_id"] = self._access_key
            kwargs["aws_secret_access_key"] = self._secret_key
        return boto3.client("s3", **kwargs)

    @staticmethod
    def _wrap(exc: Exception, action: str) -> StorageUnavailable:
        logger.error("Error de almacenamiento al %s: %s", action, exc)
        return StorageUnavailable(f"No se pudo {action} en el almacenamiento de objetos")

    # ------------------------------------------------------------------ operaciones

    def ensure_bucket(self) -> None:
        if self._bucket_ready:
            return
        from botocore.exceptions import BotoCoreError, ClientError

        with self._bucket_lock:
            if self._bucket_ready:
                return
            try:
                self.client.head_bucket(Bucket=self.bucket)
            except ClientError as exc:
                if _error_code(exc) not in _NOT_FOUND_CODES:
                    raise self._wrap(exc, "verificar el bucket") from exc
                self._create_bucket()
            except BotoCoreError as exc:
                raise self._wrap(exc, "verificar el bucket") from exc
            self._bucket_ready = True

    def _create_bucket(self) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        kwargs: dict[str, Any] = {"Bucket": self.bucket}
        if self.region and self.region != "us-east-1":
            kwargs["CreateBucketConfiguration"] = {"LocationConstraint": self.region}
        try:
            self.client.create_bucket(**kwargs)
            logger.info("Bucket de adjuntos '%s' creado", self.bucket)
        except ClientError as exc:
            if _error_code(exc) in {"BucketAlreadyOwnedByYou", "BucketAlreadyExists"}:
                return
            raise self._wrap(exc, "crear el bucket") from exc
        except BotoCoreError as exc:
            raise self._wrap(exc, "crear el bucket") from exc

    def put_object(
        self,
        key: str,
        fileobj: BinaryIO,
        *,
        size: int,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        self.ensure_bucket()
        extra: dict[str, Any] = {"ContentType": content_type}
        if metadata:
            extra["Metadata"] = metadata
        try:
            self.client.upload_fileobj(fileobj, self.bucket, key, ExtraArgs=extra)
        except (BotoCoreError, ClientError) as exc:
            raise self._wrap(exc, "guardar el archivo") from exc

    def open_object(self, key: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> StoredObject:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                raise ObjectNotFound(key) from exc
            raise self._wrap(exc, "leer el archivo") from exc
        except BotoCoreError as exc:
            raise self._wrap(exc, "leer el archivo") from exc
        body = response["Body"]
        return StoredObject(
            size=response.get("ContentLength"),
            content_type=response.get("ContentType"),
            chunks=body.iter_chunks(chunk_size=chunk_size),
            _closer=[body.close],
        )

    def delete_object(self, key: str) -> None:
        from botocore.exceptions import BotoCoreError, ClientError

        try:
            self.client.delete_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _error_code(exc) in _NOT_FOUND_CODES:
                return
            raise self._wrap(exc, "eliminar el archivo") from exc
        except BotoCoreError as exc:
            raise self._wrap(exc, "eliminar el archivo") from exc

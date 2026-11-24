"""Almacenamiento de objetos para adjuntos (MinIO/S3).

Uso::

    from app.services.storage import get_storage
    storage = get_storage()            # S3Storage configurado desde S3_* (singleton)
    storage.put_object(key, fileobj, size=n, content_type="application/pdf")

En pruebas se reemplaza con ``set_storage(InMemoryStorage())`` o con
``app.dependency_overrides[get_storage] = lambda: memoria``.
"""

from __future__ import annotations

import threading

from app.services.storage.base import (
    DEFAULT_CHUNK_SIZE,
    ObjectNotFound,
    ObjectStorage,
    StorageError,
    StorageUnavailable,
    StoredObject,
)
from app.services.storage.memory import InMemoryStorage
from app.services.storage.s3 import S3Storage

__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "InMemoryStorage",
    "ObjectNotFound",
    "ObjectStorage",
    "S3Storage",
    "StorageError",
    "StorageUnavailable",
    "StoredObject",
    "get_storage",
    "set_storage",
]

_storage: ObjectStorage | None = None
_lock = threading.Lock()


def get_storage() -> ObjectStorage:
    """Instancia compartida del almacenamiento (dependencia FastAPI)."""
    global _storage
    if _storage is None:
        with _lock:
            if _storage is None:
                _storage = S3Storage.from_settings()
    return _storage


def set_storage(storage: ObjectStorage | None) -> None:
    """Reemplaza (o reinicia con ``None``) el almacenamiento compartido."""
    global _storage
    with _lock:
        _storage = storage

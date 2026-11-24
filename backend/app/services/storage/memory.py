"""Almacenamiento en memoria (pruebas y desarrollo sin MinIO)."""

from __future__ import annotations

import threading
from typing import BinaryIO

from app.services.storage.base import (
    DEFAULT_CHUNK_SIZE,
    ObjectNotFound,
    StorageUnavailable,
    StoredObject,
)


class InMemoryStorage:
    """Implementación de ``ObjectStorage`` que guarda los objetos en un diccionario.

    ``fail_puts=True`` simula un almacenamiento caído (para probar el manejo de errores).
    """

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.content_types: dict[str, str] = {}
        self.metadata: dict[str, dict[str, str]] = {}
        self.fail_puts = False
        self.bucket_ready = False
        self._lock = threading.Lock()

    def ensure_bucket(self) -> None:
        self.bucket_ready = True

    def put_object(
        self,
        key: str,
        fileobj: BinaryIO,
        *,
        size: int,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        if self.fail_puts:
            raise StorageUnavailable("Almacenamiento simulado no disponible")
        data = fileobj.read()
        if len(data) != size:
            raise StorageUnavailable(f"Tamaño inesperado: {len(data)} != {size}")
        with self._lock:
            self.objects[key] = data
            self.content_types[key] = content_type
            self.metadata[key] = dict(metadata or {})

    def open_object(self, key: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> StoredObject:
        with self._lock:
            data = self.objects.get(key)
            content_type = self.content_types.get(key)
        if data is None:
            raise ObjectNotFound(key)

        def chunks():
            for start in range(0, len(data), chunk_size):
                yield data[start : start + chunk_size]

        return StoredObject(size=len(data), content_type=content_type, chunks=chunks())

    def delete_object(self, key: str) -> None:
        with self._lock:
            self.objects.pop(key, None)
            self.content_types.pop(key, None)
            self.metadata.pop(key, None)

"""Contrato del almacenamiento de objetos usado por los adjuntos."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import BinaryIO, Protocol, runtime_checkable

DEFAULT_CHUNK_SIZE = 64 * 1024


class StorageError(Exception):
    """Error genérico del almacenamiento de objetos."""


class StorageUnavailable(StorageError):
    """El servicio de almacenamiento no responde o rechazó la operación."""


class ObjectNotFound(StorageError):
    """El objeto solicitado no existe en el bucket."""


@dataclass
class StoredObject:
    """Objeto abierto para lectura en streaming. Llamar a ``close()`` al terminar."""

    size: int | None
    content_type: str | None
    chunks: Iterator[bytes]
    _closer: list = field(default_factory=list, repr=False)

    def close(self) -> None:
        for closer in self._closer:
            try:
                closer()
            except Exception:  # pragma: no cover - cierre best-effort
                pass
        self._closer.clear()

    def iter_and_close(self) -> Iterator[bytes]:
        """Itera los bloques y libera la conexión al terminar (o si se aborta)."""
        try:
            yield from self.chunks
        finally:
            self.close()


@runtime_checkable
class ObjectStorage(Protocol):
    """Operaciones mínimas que necesitan los adjuntos."""

    def ensure_bucket(self) -> None: ...

    def put_object(
        self,
        key: str,
        fileobj: BinaryIO,
        *,
        size: int,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> None: ...

    def open_object(self, key: str, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> StoredObject: ...

    def delete_object(self, key: str) -> None: ...

"""Validación de archivos subidos: nombre saneado, lista blanca de tipos y firma mágica.

La extensión determina el tipo permitido; el MIME declarado por el navegador solo se acepta
si es coherente con la extensión (o genérico), y el contenido debe empezar con la firma
esperada. El MIME almacenado y servido es siempre el canónico de la extensión, nunca el
declarado por el cliente.
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import quote

MAX_FILENAME_LENGTH = 200
GENERIC_MIMES = frozenset(
    {"", "application/octet-stream", "binary/octet-stream", "application/x-download", "application/unknown"}
)

_ZIP = (b"PK\x03\x04", b"PK\x05\x06")
_OLE = (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1",)


def _starts(*signatures: bytes) -> Callable[[bytes], bool]:
    return lambda head: any(head.startswith(sig) for sig in signatures)


def _is_webp(head: bytes) -> bool:
    return len(head) >= 12 and head[:4] == b"RIFF" and head[8:12] == b"WEBP"


def _is_iso_media(head: bytes) -> bool:
    # MP4/MOV: caja "ftyp" en el desplazamiento 4.
    return len(head) >= 12 and head[4:8] == b"ftyp"


def _is_text(head: bytes) -> bool:
    """Texto plano: sin bytes nulos ni caracteres de control binarios (UTF-8/Latin-1/UTF-16 con BOM)."""
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True  # UTF-16 con BOM (exportaciones de Windows)
    if b"\x00" in head:
        return False
    allowed_controls = {9, 10, 12, 13, 27}
    controls = sum(1 for byte in head if byte < 32 and byte not in allowed_controls)
    return controls <= max(1, len(head) // 100)


@dataclass(frozen=True)
class FileRule:
    """Tipo de archivo permitido."""

    mime: str
    check: Callable[[bytes], bool]
    aliases: frozenset[str] = field(default_factory=frozenset)
    label: str = ""

    def accepts_declared(self, declared: str) -> bool:
        return declared in GENERIC_MIMES or declared == self.mime or declared in self.aliases


def _rule(mime: str, check: Callable[[bytes], bool], *aliases: str, label: str = "") -> FileRule:
    return FileRule(mime=mime, check=check, aliases=frozenset(aliases), label=label)


_TEXT_ALIASES = ("text/plain", "text/x-log", "application/x-log")
_OFFICE_ZIP_ALIASES = ("application/zip", "application/x-zip-compressed")

ALLOWED_FILE_TYPES: dict[str, FileRule] = {
    # Documentos
    "pdf": _rule("application/pdf", _starts(b"%PDF-"), "application/x-pdf", label="PDF"),
    "docx": _rule(
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        _starts(*_ZIP), *_OFFICE_ZIP_ALIASES, label="Word",
    ),
    "xlsx": _rule(
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        _starts(*_ZIP), *_OFFICE_ZIP_ALIASES, label="Excel",
    ),
    "pptx": _rule(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        _starts(*_ZIP), *_OFFICE_ZIP_ALIASES, label="PowerPoint",
    ),
    "odt": _rule("application/vnd.oasis.opendocument.text", _starts(*_ZIP), *_OFFICE_ZIP_ALIASES),
    "ods": _rule("application/vnd.oasis.opendocument.spreadsheet", _starts(*_ZIP), *_OFFICE_ZIP_ALIASES),
    "odp": _rule("application/vnd.oasis.opendocument.presentation", _starts(*_ZIP), *_OFFICE_ZIP_ALIASES),
    "doc": _rule("application/msword", _starts(*_OLE), label="Word 97-2003"),
    "xls": _rule("application/vnd.ms-excel", _starts(*_OLE), label="Excel 97-2003"),
    "ppt": _rule("application/vnd.ms-powerpoint", _starts(*_OLE), label="PowerPoint 97-2003"),
    "msg": _rule("application/vnd.ms-outlook", _starts(*_OLE), "application/x-ole-storage", label="Outlook"),
    "eml": _rule("message/rfc822", _is_text, "text/plain", label="Correo"),
    # Texto y datos
    "txt": _rule("text/plain", _is_text, *_TEXT_ALIASES),
    "log": _rule("text/plain", _is_text, *_TEXT_ALIASES),
    "csv": _rule(
        "text/csv", _is_text, "text/plain", "application/csv", "text/comma-separated-values",
        "application/vnd.ms-excel", "text/x-csv", "application/x-csv",
    ),
    "json": _rule("application/json", _is_text, "text/json", "text/plain"),
    # Imágenes (sin SVG: puede contener scripts)
    "png": _rule("image/png", _starts(b"\x89PNG\r\n\x1a\n")),
    "jpg": _rule("image/jpeg", _starts(b"\xff\xd8\xff"), "image/pjpeg"),
    "jpeg": _rule("image/jpeg", _starts(b"\xff\xd8\xff"), "image/pjpeg"),
    "gif": _rule("image/gif", _starts(b"GIF87a", b"GIF89a")),
    "webp": _rule("image/webp", _is_webp),
    "bmp": _rule("image/bmp", _starts(b"BM"), "image/x-ms-bmp"),
    # Video corto (evidencias)
    "mp4": _rule("video/mp4", _is_iso_media),
    # Archivos comprimidos
    "zip": _rule("application/zip", _starts(*_ZIP), "application/x-zip-compressed", "multipart/x-zip"),
    "7z": _rule("application/x-7z-compressed", _starts(b"7z\xbc\xaf\x27\x1c")),
    # Capturas de red (SOC)
    "pcap": _rule(
        "application/vnd.tcpdump.pcap",
        _starts(b"\xd4\xc3\xb2\xa1", b"\xa1\xb2\xc3\xd4", b"\x4d\x3c\xb2\xa1", b"\xa1\xb2\x3c\x4d"),
        "application/octet-stream", "application/x-pcap",
    ),
    "pcapng": _rule("application/x-pcapng", _starts(b"\x0a\x0d\x0d\x0a"), "application/vnd.tcpdump.pcap"),
}

ALLOWED_EXTENSIONS: tuple[str, ...] = tuple(sorted(ALLOWED_FILE_TYPES))


class FileValidationError(ValueError):
    """Archivo rechazado. ``status_code`` sugiere la respuesta HTTP (415 o 422)."""

    def __init__(self, message: str, status_code: int = 415) -> None:
        super().__init__(message)
        self.status_code = status_code


_RESERVED_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')
_WHITESPACE = re.compile(r"\s+")
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(1, 10)), *(f"lpt{i}" for i in range(1, 10))}


def sanitize_filename(raw: str | None, *, max_length: int = MAX_FILENAME_LENGTH) -> str:
    """Nombre seguro para mostrar/descargar: sin rutas, controles ni caracteres reservados."""
    name = unicodedata.normalize("NFKC", raw or "")
    # Solo el último componente de la ruta (navegadores antiguos envían rutas completas).
    name = re.split(r"[\\/]", name)[-1]
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = _RESERVED_CHARS.sub("_", name)
    name = _WHITESPACE.sub(" ", name).strip(" .")
    if not name:
        name = "archivo"
    stem, ext = os.path.splitext(name)
    if stem.lower() in _WINDOWS_RESERVED:
        stem = f"_{stem}"
    ext = ext[:21]
    if len(stem) + len(ext) > max_length:
        stem = stem[: max(1, max_length - len(ext))].rstrip(" .") or "archivo"
    return f"{stem}{ext}"


def file_extension(filename: str) -> str:
    return os.path.splitext(filename)[1].lower().lstrip(".")


def validate_file_type(filename: str, declared_mime: str | None, head: bytes) -> FileRule:
    """Valida extensión, MIME declarado y firma mágica. Devuelve la regla aplicada."""
    extension = file_extension(filename)
    rule = ALLOWED_FILE_TYPES.get(extension)
    if rule is None:
        allowed = ", ".join(ALLOWED_EXTENSIONS)
        raise FileValidationError(
            f"Tipo de archivo no permitido (.{extension or 'sin extensión'}). Permitidos: {allowed}"
        )
    declared = (declared_mime or "").split(";")[0].strip().lower()
    if not rule.accepts_declared(declared):
        raise FileValidationError(
            f"El tipo declarado ({declared}) no corresponde a un archivo .{extension}"
        )
    if not head:
        raise FileValidationError("El archivo está vacío", status_code=422)
    if not rule.check(head):
        raise FileValidationError(
            f"El contenido del archivo no corresponde a un archivo .{extension} válido"
        )
    return rule


def content_disposition(filename: str, disposition: str = "attachment") -> str:
    """Cabecera Content-Disposition segura (RFC 6266 / RFC 5987) con respaldo ASCII."""
    ascii_name = (
        unicodedata.normalize("NFKD", filename).encode("ascii", "ignore").decode("ascii")
    )
    ascii_name = re.sub(r'[^A-Za-z0-9._ ()\[\]-]', "_", ascii_name).strip() or "archivo"
    return f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(filename, safe='')}"

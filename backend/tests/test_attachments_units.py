"""Pruebas unitarias de saneamiento, firmas mágicas, almacenamiento en memoria y migración."""

from __future__ import annotations

import io
from pathlib import Path

import pytest
import sqlalchemy as sa

from app.core.db import Base
from app.services.storage import InMemoryStorage, ObjectNotFound
from app.services.storage.validation import (
    FileValidationError,
    content_disposition,
    sanitize_filename,
    validate_file_type,
)

BACKEND_DIR = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("informe.pdf", "informe.pdf"),
        ("C:\\Users\\ana\\Desktop\\reporte final.xlsx", "reporte final.xlsx"),
        ("../../etc/passwd", "passwd"),
        ("  ..oculto.txt  ", "oculto.txt"),
        ("a<b>c:d|e?.log", "a_b_c_d_e_.log"),
        ("CON.txt", "_CON.txt"),
        ("", "archivo"),
        (None, "archivo"),
        ("...", "archivo"),
        ("factura\u202efdp.exe", "facturafdp.exe"),
        ("Diagnóstico   de   red.pdf", "Diagnóstico de red.pdf"),
    ],
)
def test_sanitize_filename(raw, expected):
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_truncates_but_keeps_extension():
    name = sanitize_filename("x" * 500 + ".pdf")
    assert len(name) == 200
    assert name.endswith(".pdf")


@pytest.mark.parametrize(
    "filename,head",
    [
        ("a.pdf", b"%PDF-1.4"),
        ("a.docx", b"PK\x03\x04rest"),
        ("a.xls", b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"),
        ("a.jpg", b"\xff\xd8\xff\xe0"),
        ("a.webp", b"RIFF\x00\x00\x00\x00WEBPVP8 "),
        ("a.mp4", b"\x00\x00\x00\x18ftypmp42"),
        ("a.pcap", b"\xd4\xc3\xb2\xa1"),
        ("a.csv", "nombre,área\nAna,Telecom\n".encode("utf-8")),
        ("a.txt", "texto Latin-1 ñ".encode("latin-1")),
        ("a.TXT", b"mayusculas"),
    ],
)
def test_valid_signatures(filename, head):
    validate_file_type(filename, None, head)


def test_invalid_signature_message():
    with pytest.raises(FileValidationError) as info:
        validate_file_type("a.jpg", "image/jpeg", b"%PDF-1.4")
    assert info.value.status_code == 415


def test_content_disposition_ascii_fallback_and_utf8():
    header = content_disposition('Diagnóstico "red".pdf')
    assert header.startswith('attachment; filename="Diagnostico _red_.pdf"')
    assert "filename*=UTF-8''Diagn%C3%B3stico%20%22red%22.pdf" in header
    assert "\n" not in header and "\r" not in header


def test_in_memory_storage_roundtrip():
    storage = InMemoryStorage()
    storage.ensure_bucket()
    storage.put_object("k", io.BytesIO(b"x" * 150_000), size=150_000, content_type="text/plain")
    stored = storage.open_object("k", chunk_size=64 * 1024)
    assert stored.size == 150_000
    chunks = list(stored.iter_and_close())
    assert len(chunks) == 3 and b"".join(chunks) == b"x" * 150_000
    storage.delete_object("k")
    with pytest.raises(ObjectNotFound):
        storage.open_object("k")


def test_s3_storage_maps_errors(monkeypatch):
    """S3Storage traduce los errores de botocore a errores del dominio."""
    from botocore.exceptions import ClientError, EndpointConnectionError

    from app.services.storage import S3Storage, StorageUnavailable

    class FakeClient:
        def __init__(self):
            self.created = False

        def head_bucket(self, Bucket):
            raise ClientError({"Error": {"Code": "404"}}, "HeadBucket")

        def create_bucket(self, **kwargs):
            self.created = True

        def upload_fileobj(self, *args, **kwargs):
            raise EndpointConnectionError(endpoint_url="http://minio:9000")

        def get_object(self, Bucket, Key):
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "GetObject")

        def delete_object(self, Bucket, Key):
            raise ClientError({"Error": {"Code": "NoSuchKey"}}, "DeleteObject")

    storage = S3Storage(endpoint_url="http://minio:9000", bucket="b", access_key="a", secret_key="s")
    fake = FakeClient()
    storage._client = fake
    storage.ensure_bucket()
    assert fake.created is True
    with pytest.raises(StorageUnavailable):
        storage.put_object("k", io.BytesIO(b"x"), size=1, content_type="text/plain")
    with pytest.raises(ObjectNotFound):
        storage.open_object("k")
    storage.delete_object("k")  # inexistente: no falla


def test_migration_0004_matches_model(tmp_path):
    from alembic import command
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from alembic.util.exc import CommandError

    config = Config(str(BACKEND_DIR / "alembic.ini"))
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    config.attributes["sqlalchemy.url"] = url
    config.attributes["configure_logger"] = False
    try:
        script = ScriptDirectory.from_config(config)
        revision = script.get_revision("0004_attachments")
        script.get_revision("0003_flows")
    except (CommandError, KeyError) as exc:  # pragma: no cover - cadena incompleta en desarrollo
        pytest.skip(f"Cadena de migraciones incompleta: {exc}")
    assert revision.down_revision == "0003_flows"

    command.upgrade(config, "0004_attachments")
    engine = sa.create_engine(url)
    try:
        inspector = sa.inspect(engine)
        model_table = Base.metadata.tables["ticket_attachments"]
        db_columns = {c["name"]: c for c in inspector.get_columns("ticket_attachments")}
        assert set(db_columns) == set(model_table.columns.keys())
        for column in model_table.columns:
            assert db_columns[column.name]["nullable"] == column.nullable, column.name
        assert {ix.name for ix in model_table.indexes} <= {
            ix["name"] for ix in inspector.get_indexes("ticket_attachments")
        }
    finally:
        engine.dispose()
    command.downgrade(config, "0003_flows")
    engine = sa.create_engine(url)
    try:
        assert "ticket_attachments" not in sa.inspect(engine).get_table_names()
    finally:
        engine.dispose()

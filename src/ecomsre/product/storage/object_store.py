"""Create-once content-addressed local object storage."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

from ecomsre.product.storage.sqlite_store import SqliteStoreV1


class ObjectStoreIntegrityError(RuntimeError):
    pass


@dataclass(frozen=True)
class StoredObjectV1:
    object_sha256: str
    byte_size: int
    media_type: str
    path: Path


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


class ContentAddressedObjectStoreV1:
    def __init__(self, root: Path, *, metadata_store: SqliteStoreV1) -> None:
        self.root = Path(root).expanduser().resolve()
        self.metadata_store = metadata_store
        self.sha_root = self.root / "sha256"
        self.sha_root.mkdir(parents=True, exist_ok=True)

    def _path_for(self, object_sha256: str) -> Path:
        if len(object_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in object_sha256
        ):
            raise ValueError("object SHA-256 is invalid")
        return self.sha_root / object_sha256[:2] / f"{object_sha256}.json"

    def put_json(self, payload: Any) -> StoredObjectV1:
        data = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return self.put_bytes(data, media_type="application/json")

    def put_bytes(self, data: bytes, *, media_type: str) -> StoredObjectV1:
        if (
            not media_type
            or len(media_type) > 255
            or any(character in media_type for character in "\r\n\x00")
        ):
            raise ValueError("object media type is invalid")
        object_sha256 = hashlib.sha256(data).hexdigest()
        path = self._path_for(object_sha256)
        path.parent.mkdir(parents=True, exist_ok=True)
        file_descriptor, temporary_name = tempfile.mkstemp(
            prefix=".tmp-",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(file_descriptor, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            try:
                os.link(temporary_path, path)
            except FileExistsError:
                if path.read_bytes() != data:
                    raise ObjectStoreIntegrityError("existing object bytes differ")
            _fsync_directory(path.parent)
        finally:
            temporary_path.unlink(missing_ok=True)
        self._bind_metadata(
            object_sha256=object_sha256,
            byte_size=len(data),
            media_type=media_type,
        )
        return StoredObjectV1(
            object_sha256=object_sha256,
            byte_size=len(data),
            media_type=media_type,
            path=path,
        )

    def read_bytes(self, object_sha256: str) -> bytes:
        path = self._path_for(object_sha256)
        try:
            data = path.read_bytes()
        except FileNotFoundError as error:
            raise ObjectStoreIntegrityError("stored object bytes are missing") from error
        if hashlib.sha256(data).hexdigest() != object_sha256:
            raise ObjectStoreIntegrityError("stored object digest differs")
        with self.metadata_store.connect() as connection:
            metadata = connection.execute(
                "SELECT byte_size FROM evidence_objects WHERE object_sha256 = ?",
                (object_sha256,),
            ).fetchone()
        if metadata is None:
            raise ObjectStoreIntegrityError("stored object metadata is missing")
        if metadata["byte_size"] != len(data):
            raise ObjectStoreIntegrityError("stored object metadata differs")
        return data

    def _bind_metadata(
        self,
        *,
        object_sha256: str,
        byte_size: int,
        media_type: str,
    ) -> None:
        with self.metadata_store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute(
                    "SELECT byte_size, media_type FROM evidence_objects "
                    "WHERE object_sha256 = ?",
                    (object_sha256,),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        "INSERT INTO evidence_objects("
                        "object_sha256, byte_size, media_type, created_at"
                        ") VALUES (?, ?, ?, ?)",
                        (
                            object_sha256,
                            byte_size,
                            media_type,
                            datetime.now(UTC).isoformat(),
                        ),
                    )
                elif (
                    existing["byte_size"] != byte_size
                    or existing["media_type"] != media_type
                ):
                    raise ObjectStoreIntegrityError("existing object metadata differs")
                connection.execute("COMMIT")
            except Exception:
                connection.execute("ROLLBACK")
                raise


__all__ = (
    "ContentAddressedObjectStoreV1",
    "ObjectStoreIntegrityError",
    "StoredObjectV1",
)

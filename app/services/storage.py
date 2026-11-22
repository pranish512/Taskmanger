"""Local-disk storage for task attachments."""

import logging
import uuid
from collections.abc import Iterable
from pathlib import Path

from fastapi import UploadFile

from app.core.config import settings
from app.core.exceptions import BadRequestError, PayloadTooLargeError

logger = logging.getLogger(__name__)

_CHUNK_SIZE = 1024 * 1024


def safe_filename(name: str | None) -> str:
    """Keep only the base name of what the client sent (no directories)."""
    base = Path((name or "").replace("\\", "/")).name.strip()
    return (base or "file")[:255]


def file_path(stored_name: str) -> Path:
    return settings.upload_dir / stored_name


def save_upload(upload: UploadFile) -> tuple[str, int]:
    """Stream the upload to disk under a random name. Returns (stored_name, size_bytes)."""
    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    suffix = Path(safe_filename(upload.filename)).suffix[:16]
    stored_name = f"{uuid.uuid4().hex}{suffix}"
    destination = file_path(stored_name)
    max_bytes = settings.max_upload_size_mb * 1024 * 1024

    size = 0
    try:
        with destination.open("wb") as out:
            while chunk := upload.file.read(_CHUNK_SIZE):
                size += len(chunk)
                if size > max_bytes:
                    raise PayloadTooLargeError(
                        f"File is larger than the {settings.max_upload_size_mb} MB limit"
                    )
                out.write(chunk)
        if size == 0:
            raise BadRequestError("The uploaded file is empty")
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return stored_name, size


def delete_files(stored_names: Iterable[str]) -> None:
    for name in stored_names:
        try:
            file_path(name).unlink(missing_ok=True)
        except OSError:
            logger.warning("Could not delete attachment file %s", name)

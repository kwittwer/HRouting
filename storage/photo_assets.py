"""Validated, content-addressed external photo assets for HRP projects."""

from __future__ import annotations

import hashlib
import os
import tempfile
import warnings
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import BinaryIO, Iterator
from uuid import UUID

from PIL import Image, UnidentifiedImageError


MAX_PHOTO_BYTES = 200 * 1024 * 1024
MAX_PHOTO_PIXELS = 80_000_000
_IMAGE_FORMATS = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG"}


def resolve_photo_path(project_path: str | Path, relative_path: str) -> Path:
    """Resolve a portable relative path, rejecting traversal and symlink escape."""
    if not isinstance(relative_path, str) or not relative_path:
        raise ValueError("Photo path must be a nonempty relative POSIX path")
    if "\\" in relative_path or PureWindowsPath(relative_path).drive:
        raise ValueError("Photo path must not contain Windows separators or drives")
    parts = relative_path.split("/")
    for part in parts:
        if part in {"", ".", ".."} or part.endswith((".", " ")):
            raise ValueError("Photo path contains an unsafe component")
        if any(ord(character) < 32 or character in '<>:"|?*' for character in part):
            raise ValueError("Photo path contains an unsafe character")
        if PureWindowsPath(part).is_reserved():
            raise ValueError("Photo path contains a reserved Windows name")
    root = Path(project_path).parent.resolve()
    candidate = root.joinpath(*parts).resolve()
    if not candidate.is_relative_to(root) or candidate == root:
        raise ValueError("Photo path escapes the project directory")
    return candidate


def _expected_hash(photo: dict) -> str:
    digest = photo.get("sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise ValueError("Photo metadata must contain a SHA-256 digest")
    if any(character not in "0123456789abcdefABCDEF" for character in digest):
        raise ValueError("Invalid photo SHA-256 digest")
    return digest.lower()


def _hash_stream(source: BinaryIO) -> str:
    digest = hashlib.sha256()
    while chunk := source.read(1024 * 1024):
        digest.update(chunk)
    return digest.hexdigest()


def _verify_hash(path: Path, expected_hash: str) -> Path:
    with path.open("rb") as handle:
        actual_hash = _hash_stream(handle)
    if actual_hash != expected_hash:
        raise ValueError(f"Photo SHA-256 mismatch: {path}")
    return path


def verify_photo(project_path: str | Path, photo: dict) -> Path:
    """Return the safe photo path, raising on missing or changed content."""
    path = resolve_photo_path(project_path, photo.get("path"))
    return _verify_hash(path, _expected_hash(photo))


@contextmanager
def _staged_copy(
    source: Path, directory: Path, *, max_bytes: int | None = None,
) -> Iterator[tuple[Path, str]]:
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb", dir=directory, prefix=".photo-", suffix=".tmp", delete=False,
        ) as handle:
            temporary_path = Path(handle.name)
            digest = hashlib.sha256()
            size = 0
            with source.open("rb") as original:
                while chunk := original.read(1024 * 1024):
                    size += len(chunk)
                    if max_bytes is not None and size > max_bytes:
                        raise ValueError("Photo exceeds the maximum file size")
                    digest.update(chunk)
                    handle.write(chunk)
            handle.flush()
            os.fsync(handle.fileno())
        yield temporary_path, digest.hexdigest()
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _image_metadata(path: Path, expected_format: str) -> dict:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with path.open("rb") as handle, Image.open(handle, formats=[expected_format]) as image:
                if image.format != expected_format:
                    raise ValueError("Photo content does not match its JPG/JPEG/PNG extension")
                if image.width * image.height > MAX_PHOTO_PIXELS:
                    raise ValueError("Photo exceeds the maximum pixel count")
                width, height = image.size
                image.verify()
            with path.open("rb") as handle, Image.open(handle, formats=[expected_format]) as image:
                orientation = image.getexif().get(274, 1)
                if orientation not in range(1, 9):
                    orientation = 1
                image.load()
    except (UnidentifiedImageError, OSError, SyntaxError,
            Image.DecompressionBombError, Image.DecompressionBombWarning) as error:
        raise ValueError("Invalid or oversized photo image") from error
    if orientation in {5, 6, 7, 8}:
        width, height = height, width
    return {"width": width, "height": height, "exif_orientation": orientation}


def _publish_photo(
    staged_path: Path, project_path: str | Path, relative_path: str, digest: str,
) -> Path:
    destination = resolve_photo_path(project_path, relative_path)
    if destination.exists():
        return _verify_hash(destination, digest)
    os.replace(staged_path, destination)
    return destination


def import_photo(
    source_path: str | Path, project_path: str | Path, project_uuid: str,
) -> dict:
    """Copy original image bytes and return portable, verified photo metadata."""
    source = Path(source_path)
    image_format = _IMAGE_FORMATS.get(source.suffix.lower())
    if image_format is None:
        raise ValueError("Only JPG, JPEG and PNG photos are supported")
    if source.stat().st_size > MAX_PHOTO_BYTES:
        raise ValueError("Photo exceeds the maximum file size")
    project_id = str(UUID(str(project_uuid)))
    directory = resolve_photo_path(project_path, f"photos/{project_id}/.import").parent
    directory.mkdir(parents=True, exist_ok=True)
    directory = resolve_photo_path(project_path, f"photos/{project_id}/.import").parent
    with _staged_copy(source, directory, max_bytes=MAX_PHOTO_BYTES) as (staged, digest):
        metadata = _image_metadata(staged, image_format)
        byte_size = staged.stat().st_size
        extension = "jpg" if image_format == "JPEG" else "png"
        relative_path = f"photos/{project_id}/{digest}.{extension}"
        _publish_photo(staged, project_path, relative_path, digest)
        return {
            "path": relative_path,
            "sha256": digest,
            "original_name": source.name,
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "byte_size": byte_size,
            **metadata,
        }


def copy_project_photos(
    raw: dict, source_project_path: str | Path, target_project_path: str | Path,
) -> None:
    """Copy only referenced photos before saving, without changing metadata.

    Missing assets in the same project directory emit RuntimeWarning. Across
    directories, an existing target is accepted only when its hash matches.
    Changed source or target content always raises ValueError.
    """
    params = raw.get("params")
    if not isinstance(params, dict) or "photo_documentation" not in params:
        return
    config = params["photo_documentation"]
    if not isinstance(config, dict) or not isinstance(config.get("photos", {}), dict):
        raise ValueError("Invalid photo documentation metadata")
    same_directory = (
        Path(source_project_path).parent.resolve() == Path(target_project_path).parent.resolve()
    )
    for photo_id, photo in config.get("photos", {}).items():
        if not isinstance(photo, dict):
            raise ValueError(f"Invalid photo metadata: {photo_id}")
        digest = _expected_hash(photo)
        relative_path = photo.get("path")
        source = resolve_photo_path(source_project_path, relative_path)
        destination = resolve_photo_path(target_project_path, relative_path)
        try:
            _verify_hash(source, digest)
        except FileNotFoundError as error:
            if same_directory:
                warnings.warn(
                    f"Missing photo asset {photo_id}: {relative_path}; metadata retained",
                    RuntimeWarning, stacklevel=2,
                )
                continue
            try:
                _verify_hash(destination, digest)
            except FileNotFoundError:
                raise FileNotFoundError(f"Missing photo asset {photo_id}: {relative_path}") from error
            continue
        if same_directory:
            continue
        if destination.exists():
            _verify_hash(destination, digest)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination = resolve_photo_path(target_project_path, relative_path)
        with _staged_copy(source, destination.parent) as (staged, actual_hash):
            if actual_hash != digest:
                raise ValueError(f"Photo SHA-256 mismatch during copy: {source}")
            _publish_photo(staged, target_project_path, relative_path, digest)
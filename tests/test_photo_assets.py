"""External photo storage and atomic project save regressions."""

from __future__ import annotations

import hashlib
import json
import shutil
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import pytest
from PIL import Image, ImageOps

from model.document import Document
from storage.hrp_io import load_document, save_document, save_raw
from storage.migration import CURRENT_HRP_FORMAT_VERSION, FORMAT_VERSION_KEY
from storage.photo_assets import copy_project_photos, import_photo, resolve_photo_path, verify_photo


@pytest.mark.parametrize("failure", ["serialization", "replace"])
def test_save_raw_failure_preserves_destination_and_cleans_temporary_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    target = tmp_path / "project.hrp"
    original = b'{"original": true}'
    target.write_bytes(original)
    raw = {"invalid": object()} if failure == "serialization" else {"params": {}}

    def fail_replace(*args: object) -> None:
        raise OSError("replace blocked")

    if failure == "replace":
        monkeypatch.setattr("storage.hrp_io.os.replace", fail_replace)
    with pytest.raises(TypeError if failure == "serialization" else OSError):
        save_raw(raw, target)

    assert target.read_bytes() == original
    assert list(tmp_path.iterdir()) == [target]


def _image(path: Path, *, image_format: str = "PNG", orientation: int = 1) -> bytes:
    path.parent.mkdir(parents=True, exist_ok=True)
    with Image.new("RGB", (6, 4), "red") as image:
        exif = Image.Exif()
        exif[274] = orientation
        image.save(path, format=image_format, exif=exif)
    return path.read_bytes()


def test_import_preserves_original_bytes_and_deduplicates(tmp_path: Path) -> None:
    source = tmp_path / "input" / "Baustelle-\u00e4.png"
    original = _image(source)
    project = tmp_path / "project" / "plan.hrp"
    project_id = str(uuid4())
    first = import_photo(source, project, project_id)
    duplicate_source = source.with_name("renamed.PNG")
    shutil.copyfile(source, duplicate_source)
    second = import_photo(duplicate_source, project, project_id)

    assert first["path"] == second["path"]
    assert first["sha256"] == hashlib.sha256(original).hexdigest()
    assert first["original_name"] == source.name
    assert second["original_name"] == duplicate_source.name
    assert first["width"] == 6 and first["height"] == 4
    assert first["byte_size"] == len(original)
    assert datetime.fromisoformat(first["imported_at"]).tzinfo is not None
    assert verify_photo(project, first).read_bytes() == original
    assert source.read_bytes() == original
    assert str(source) not in str(first)
    assert len(list((project.parent / "photos").rglob("*.*"))) == 1


@pytest.mark.parametrize("orientation", range(1, 9))
def test_import_reports_exif_transposed_dimensions(tmp_path: Path, orientation: int) -> None:
    source = tmp_path / "camera.JPEG"
    original = _image(source, image_format="JPEG", orientation=orientation)
    project = tmp_path / "project.hrp"
    photo = import_photo(source, project, str(uuid4()))
    with Image.open(source) as image:
        with ImageOps.exif_transpose(image) as canonical:
            assert (photo["width"], photo["height"]) == canonical.size
    assert photo["exif_orientation"] == orientation
    assert photo["path"].endswith(".jpg")
    assert verify_photo(project, photo).read_bytes() == original


@pytest.mark.parametrize("relative", [
    "", ".", "..", "../outside.png", "photos/../outside.png", "/outside.png",
    "C:/outside.png", "C:outside.png", "photos\\outside.png", "\\\\server\\file.png",
    "photos//file.png", "photos/./file.png", "photos/file.png:stream",
    "photos/.. /file.png", "photos/NUL.png", "photos/file\x00.png",
])
def test_resolve_rejects_unsafe_paths(tmp_path: Path, relative: str) -> None:
    with pytest.raises(ValueError):
        resolve_photo_path(tmp_path / "project.hrp", relative)


def test_resolve_rejects_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    try:
        (root / "photos").symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks are unavailable")
    with pytest.raises(ValueError, match="escapes"):
        resolve_photo_path(root / "project.hrp", "photos/image.png")


def test_verify_rejects_changed_or_missing_content(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    _image(source)
    project = tmp_path / "project.hrp"
    photo = import_photo(source, project, str(uuid4()))
    stored = verify_photo(project, photo)
    stored.write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        verify_photo(project, photo)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        import_photo(source, project, photo["path"].split("/")[1])
    assert stored.read_bytes() == b"changed"
    stored.unlink()
    with pytest.raises(FileNotFoundError):
        verify_photo(project, photo)
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("kind", ["fake", "wrong_format", "unsupported", "bytes", "pixels"])
def test_import_rejects_invalid_or_oversized_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, kind: str,
) -> None:
    source = tmp_path / ("source.gif" if kind == "unsupported" else "source.png")
    if kind == "fake":
        source.write_bytes(b"not an image")
    else:
        _image(source, image_format="JPEG" if kind == "wrong_format" else "PNG")
    if kind == "bytes":
        monkeypatch.setattr("storage.photo_assets.MAX_PHOTO_BYTES", 1)
    if kind == "pixels":
        monkeypatch.setattr("storage.photo_assets.MAX_PHOTO_PIXELS", 1)
    with pytest.raises(ValueError):
        import_photo(source, tmp_path / "project.hrp", str(uuid4()))
    assert not list(tmp_path.rglob("*.tmp"))


def _photo_project(tmp_path: Path) -> tuple[Document, dict, Path]:
    source = tmp_path / "input" / "photo.png"
    _image(source)
    project = tmp_path / "original" / "plan.hrp"
    project_id = str(uuid4())
    photo = import_photo(source, project, project_id)
    config = {"version": 1, "project_uuid": project_id, "photos": {"PHOTO-1": photo}}
    doc = Document.from_dict({"canvas": {}, "params": {"photo_documentation": config}})
    save_document(doc, project)
    return doc, photo, project


def test_save_as_copies_only_referenced_photos_and_preserves_original(tmp_path: Path) -> None:
    doc, photo, source = _photo_project(tmp_path)
    original_hrp = source.read_bytes()
    original_photo = verify_photo(source, photo).read_bytes()
    orphan = verify_photo(source, photo).with_name("unreferenced.png")
    orphan.write_bytes(b"retained for undo")
    target = tmp_path / "copy" / "renamed.hrp"
    config_before = deepcopy(doc.settings["photo_documentation"])

    save_document(doc, target)

    assert doc.source_path == target
    assert source.read_bytes() == original_hrp
    assert verify_photo(source, photo).read_bytes() == original_photo
    assert verify_photo(target, photo).read_bytes() == original_photo
    assert orphan.read_bytes() == b"retained for undo"
    assert not (verify_photo(target, photo).parent / orphan.name).exists()
    restored = load_document(target)
    assert restored.settings["photo_documentation"] == config_before
    assert doc.settings["photo_documentation"] == config_before
    raw = json.loads(target.read_text(encoding="utf-8"))
    assert raw[FORMAT_VERSION_KEY] == CURRENT_HRP_FORMAT_VERSION
    assert raw["params"]["photo_documentation"]["photos"]["PHOTO-1"]["path"] == photo["path"]
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("new_name", [False, True])
def test_missing_photo_same_directory_can_still_save(tmp_path: Path, new_name: bool) -> None:
    doc, photo, source = _photo_project(tmp_path)
    verify_photo(source, photo).unlink()
    target = source.with_name("renamed.hrp") if new_name else source
    config = deepcopy(doc.settings["photo_documentation"])
    with pytest.warns(RuntimeWarning, match="Missing photo asset PHOTO-1"):
        save_document(doc, target)
    assert load_document(target).settings["photo_documentation"] == config
    assert doc.source_path == target


@pytest.mark.parametrize("target_state", ["missing", "matching", "changed"])
def test_missing_source_requires_verified_target_in_another_directory(
    tmp_path: Path, target_state: str,
) -> None:
    doc, photo, source = _photo_project(tmp_path)
    target = tmp_path / "other" / "plan.hrp"
    source_asset = verify_photo(source, photo)
    destination = resolve_photo_path(target, photo["path"])
    if target_state != "missing":
        destination.parent.mkdir(parents=True)
        destination.write_bytes(source_asset.read_bytes() if target_state == "matching" else b"other")
    source_asset.unlink()
    if target_state == "matching":
        save_document(doc, target)
        assert verify_photo(target, photo) == destination
        assert doc.source_path == target
    else:
        with pytest.raises(FileNotFoundError if target_state == "missing" else ValueError):
            save_document(doc, target)
        assert not target.exists()
        assert doc.source_path == source


@pytest.mark.parametrize("location", ["source", "target"])
def test_save_as_refuses_changed_content_without_overwriting(tmp_path: Path, location: str) -> None:
    doc, photo, source = _photo_project(tmp_path)
    target = tmp_path / "other" / "plan.hrp"
    destination = resolve_photo_path(target, photo["path"])
    destination.parent.mkdir(parents=True)
    destination.write_bytes(verify_photo(source, photo).read_bytes())
    changed = verify_photo(source, photo) if location == "source" else destination
    changed.write_bytes(b"unrelated content")
    old_hrp = b'{"existing": true}'
    target.write_bytes(old_hrp)
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        save_document(doc, target)
    assert changed.read_bytes() == b"unrelated content"
    assert target.read_bytes() == old_hrp
    assert doc.source_path == source
    assert not list(tmp_path.rglob("*.tmp"))


@pytest.mark.parametrize("failure", ["missing", "copy", "serialization", "replace"])
def test_failed_save_as_retains_hrp_and_document_source_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    import os

    doc, photo, source = _photo_project(tmp_path)
    target = tmp_path / "other" / "plan.hrp"
    target.parent.mkdir()
    original_target = b'{"original target": true}'
    target.write_bytes(original_target)
    original_source = source.read_bytes()
    replace = os.replace

    def fail_replace(source_path: str | Path, destination_path: str | Path) -> None:
        destination_path = Path(destination_path)
        if failure == "copy" and destination_path.suffix == ".png":
            raise OSError("asset copy blocked")
        if failure == "replace" and destination_path == target:
            raise OSError("HRP replace blocked")
        replace(source_path, destination_path)

    monkeypatch.setattr("storage.hrp_io.os.replace", fail_replace)
    if failure == "missing":
        verify_photo(source, photo).unlink()
    if failure == "serialization":
        doc.settings["unserializable"] = object()
    with pytest.raises(TypeError if failure == "serialization" else OSError):
        save_document(doc, target)
    assert target.read_bytes() == original_target
    assert source.read_bytes() == original_source
    assert doc.source_path == source
    assert not list(tmp_path.rglob("*.tmp"))


def test_new_document_uses_target_directory_as_photo_source(tmp_path: Path) -> None:
    doc, photo, project = _photo_project(tmp_path)
    fresh = Document.from_dict(doc.to_dict())
    save_document(fresh, project)
    assert fresh.source_path == project
    assert verify_photo(project, photo).exists()


def test_copy_project_photos_preserves_raw_metadata(tmp_path: Path) -> None:
    doc, photo, source = _photo_project(tmp_path)
    raw = doc.to_dict()
    original = deepcopy(raw)
    target = tmp_path / "other" / "plan.hrp"
    assert copy_project_photos(raw, source, target) is None
    assert raw == original
    assert verify_photo(target, photo).exists()


@pytest.mark.parametrize("raw", [{}, {"params": {}}, {"params": {"photo_documentation": {}}}])
def test_copy_without_photos_does_not_create_directories(tmp_path: Path, raw: dict) -> None:
    target = tmp_path / "other" / "plan.hrp"
    assert copy_project_photos(raw, tmp_path / "source.hrp", target) is None
    assert not target.parent.exists()


def test_save_raw_keeps_format_version_utf8_and_input_unchanged(tmp_path: Path) -> None:
    raw = {"name": "\u00e4", FORMAT_VERSION_KEY: 0}
    target = tmp_path / "nested" / "plan.hrp"
    save_raw(raw, target)
    text = target.read_text(encoding="utf-8")
    assert "\u00e4" in text
    assert json.loads(text)[FORMAT_VERSION_KEY] == CURRENT_HRP_FORMAT_VERSION
    assert raw[FORMAT_VERSION_KEY] == 0


def test_import_interrupted_replace_keeps_original_and_cleans_stage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source.png"
    original = _image(source)

    def fail_replace(*args: object) -> None:
        raise OSError("import blocked")

    monkeypatch.setattr("storage.photo_assets.os.replace", fail_replace)
    with pytest.raises(OSError, match="import blocked"):
        import_photo(source, tmp_path / "project.hrp", str(uuid4()))
    assert source.read_bytes() == original
    assert not list((tmp_path / "photos").rglob("*.*"))


def test_import_rejects_truncated_image_without_storing_it(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    original = _image(source)
    source.write_bytes(original[:len(original) // 2])
    with pytest.raises(ValueError):
        import_photo(source, tmp_path / "project.hrp", str(uuid4()))
    assert not list((tmp_path / "photos").rglob("*.*"))


@pytest.mark.parametrize("project_id", ["../escape", "C:/escape", "not-a-uuid"])
def test_import_rejects_unsafe_project_uuid(tmp_path: Path, project_id: str) -> None:
    source = tmp_path / "source.png"
    _image(source)
    with pytest.raises(ValueError):
        import_photo(source, tmp_path / "project.hrp", project_id)
    assert not (tmp_path / "photos").exists()


@pytest.mark.parametrize("digest", [None, "", "0" * 63, "z" * 64, 123])
def test_verify_rejects_invalid_expected_hash(tmp_path: Path, digest: object) -> None:
    source = tmp_path / "photo.png"
    _image(source)
    with pytest.raises(ValueError, match="SHA-256"):
        verify_photo(tmp_path / "project.hrp", {"path": "photo.png", "sha256": digest})


@pytest.mark.parametrize("config", [None, [], {"photos": []}, {"photos": {"PHOTO-1": None}}])
def test_copy_refuses_malformed_metadata(tmp_path: Path, config: object) -> None:
    raw = {"params": {"photo_documentation": config}}
    with pytest.raises(ValueError):
        copy_project_photos(raw, tmp_path / "source.hrp", tmp_path / "other" / "plan.hrp")
    assert not (tmp_path / "other").exists()


def test_same_directory_save_refuses_changed_photo(tmp_path: Path) -> None:
    doc, photo, project = _photo_project(tmp_path)
    original_hrp = project.read_bytes()
    verify_photo(project, photo).write_bytes(b"changed")
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        save_document(doc, project)
    assert project.read_bytes() == original_hrp


def test_save_as_reuses_verified_existing_target(tmp_path: Path) -> None:
    doc, photo, source = _photo_project(tmp_path)
    target = tmp_path / "other" / "plan.hrp"
    copy_project_photos(doc.to_dict(), source, target)
    stored = verify_photo(target, photo)
    timestamp = stored.stat().st_mtime_ns
    save_document(doc, target)
    assert verify_photo(target, photo).stat().st_mtime_ns == timestamp
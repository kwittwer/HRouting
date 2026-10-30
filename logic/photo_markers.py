"""Lazy OpenCV ArUco support. All decoded photo arrays use uint8 BGR."""

from __future__ import annotations

from collections import Counter
import hashlib
import math
from pathlib import Path

from model.document import Document
from model.elements import ElecPoint
from model.photo_documentation import _next_id

MAX_PHOTO_PIXELS = 80_000_000
MAX_PHOTO_BYTES = 256 * 1024 * 1024
MAX_DICTIONARY_MARKERS = 2048


def _aruco():
    import cv2

    if not hasattr(cv2, "aruco") or not all(hasattr(cv2.aruco, name) for name in (
            "Dictionary", "extendDictionary", "generateImageMarker", "ArucoDetector")):
        raise RuntimeError("Photo markers require opencv-python-headless >= 4.10 with ArUco")
    return cv2.aruco


def _dictionary(config: dict):
    import numpy as np

    aruco = _aruco()
    payload = config.get("dictionary", {})
    if (not isinstance(payload, dict) or type(payload.get("marker_size")) is not int
            or payload.get("marker_size") not in (5, 6)):
        raise ValueError("Missing or invalid project dictionary")
    marker_size = payload["marker_size"]
    raw_bytes = np.asarray(payload.get("bytes_list"))
    if (raw_bytes.ndim != 3 or not 1 <= len(raw_bytes) <= MAX_DICTIONARY_MARKERS
            or raw_bytes.shape[1:] != ((marker_size ** 2 + 7) // 8, 4)
            or raw_bytes.dtype.kind not in "iu" or np.any(raw_bytes < 0) or np.any(raw_bytes > 255)):
        raise ValueError("Invalid project dictionary bytes_list")
    correction = payload.get("max_correction_bits")
    if (not isinstance(correction, int) or isinstance(correction, bool)
            or not 0 <= correction <= marker_size ** 2 // 2):
        raise ValueError("Invalid project dictionary correction limit")
    return aruco.Dictionary(raw_bytes.astype(np.uint8), marker_size, correction)


def _ensure_dictionary(config: dict, needed: int) -> None:
    if needed > MAX_DICTIONARY_MARKERS:
        raise ValueError(f"Project dictionary capacity limit is {MAX_DICTIONARY_MARKERS}")
    payload = config.get("dictionary", {})
    if not isinstance(payload, dict):
        raise ValueError("dictionary must be an object")
    base = _dictionary(config) if payload.get("bytes_list") is not None else None
    if base is not None and len(base.bytesList) >= needed:
        return
    marker_size = payload.get("marker_size", 5)
    if type(marker_size) is not int or marker_size not in (5, 6):
        raise ValueError("marker_size must be 5 or 6")
    identity = config.get("project_uuid")
    if not isinstance(identity, str) or not identity:
        raise ValueError("project_uuid is required before generating markers")
    seed = int(hashlib.sha256(identity.encode("utf-8")).hexdigest()[:8], 16) & 0x7fffffff
    capacity = min(MAX_DICTIONARY_MARKERS, max(64, math.ceil(needed / 64) * 64))
    aruco = _aruco()
    dictionary = aruco.extendDictionary(capacity, marker_size, baseDictionary=base, randomSeed=seed)
    if base is not None:
        import numpy as np

        if not np.array_equal(dictionary.bytesList[:len(base.bytesList)], base.bytesList):
            raise RuntimeError("Dictionary extension changed existing marker bytes")
    config["dictionary"] = {
        **payload, "version": 1, "marker_size": marker_size, "seed": seed,
        "bytes_list": dictionary.bytesList.tolist(),
        "max_correction_bits": int(dictionary.maxCorrectionBits),
    }


def _side(value: object) -> float:
    if isinstance(value, bool):
        raise ValueError("side_mm must be positive and finite")
    try:
        side = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("side_mm must be positive and finite") from error
    if not math.isfinite(side) or side <= 0:
        raise ValueError("side_mm must be positive and finite")
    return side


def _new_marker(config: dict, side_mm: float, surface_id: str, point_id: str = "") -> dict:
    registry = config.setdefault("markers", {})
    aruco_ids = [marker.get("aruco_id") for marker in registry.values()]
    if any(not isinstance(value, int) or isinstance(value, bool) or value < 0 for value in aruco_ids):
        raise ValueError("Existing registry contains invalid aruco_id values")
    if len(set(aruco_ids)) != len(aruco_ids):
        raise ValueError("Existing registry contains duplicate aruco_id values")
    aruco_id = max(aruco_ids, default=-1) + 1
    _ensure_dictionary(config, aruco_id + 1)
    marker_id = _next_id(registry, "MARK")
    marker = {
        "id": marker_id, "aruco_id": aruco_id, "surface_id": surface_id,
        "side_mm": side_mm, "print_version": 1,
    }
    if point_id:
        marker["point_id"] = point_id
    registry[marker_id] = marker
    return marker


def ensure_markers(document: Document, config: dict, point_ids=None, side_mm=80.0) -> dict:
    """Append AP registrations on demand; return the mutated config.

    AP IDs do not determine ArUco IDs. Deleted APs retain their registrations;
    a changed physical side length creates a new registration, not a rewrite.
    No plan position, installation height or wall suggestion becomes a reference.
    """
    side = _side(side_mm)
    selected = ([point.id for point in document.elements_of(ElecPoint)] if point_ids is None
                else [point_ids] if isinstance(point_ids, str) else list(point_ids))
    if any(not isinstance(document.get(point_id), ElecPoint) for point_id in selected):
        raise ValueError("point_ids must refer to existing electrical points")
    registry = config.setdefault("markers", {})
    for point_id in dict.fromkeys(selected):
        matching = [marker for marker in registry.values()
                    if marker.get("point_id") == point_id and marker.get("side_mm") == side]
        if matching:
            continue
        surface_id = config.get("assignments", {}).get(point_id, "")
        if surface_id:
            surface = config.get("surfaces", {}).get(surface_id)
            if not isinstance(surface, dict) or surface.get("status") != "current":
                raise ValueError(f"{point_id} is assigned to a missing/stale surface")
        _new_marker(config, side, surface_id, point_id)
    return config


def add_reference_marker(config: dict, surface_id: str, side_mm=120.0) -> dict:
    """Return a new marker record; its measured reference must be entered later."""
    side = _side(side_mm)
    surface = config.get("surfaces", {}).get(surface_id)
    if not isinstance(surface, dict) or surface.get("status") != "current":
        raise ValueError("Reference marker requires a current surface")
    return _new_marker(config, side, surface_id)


def marker_image(config: dict, marker_id: str, pixels=400):
    """Return a grayscale uint8 square. The caller adds the white quiet zone."""
    marker = config.get("markers", {}).get(marker_id)
    if not isinstance(marker, dict):
        raise ValueError("Unknown marker_id")
    dictionary = _dictionary(config)
    aruco_id = marker.get("aruco_id")
    if not isinstance(aruco_id, int) or not 0 <= aruco_id < len(dictionary.bytesList):
        raise ValueError("Marker is outside the project dictionary")
    if not isinstance(pixels, int) or isinstance(pixels, bool) or not (dictionary.markerSize + 2) * 2 <= pixels <= 8192:
        raise ValueError("pixels must be an integer between twice the cell count and 8192")
    return _aruco().generateImageMarker(dictionary, aruco_id, pixels)


def _image_array(image):
    import numpy as np

    if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
            or image.ndim not in (2, 3) or min(image.shape[:2], default=0) <= 0
            or image.shape[0] * image.shape[1] > MAX_PHOTO_PIXELS
            or (image.ndim == 3 and image.shape[2] not in (3, 4))):
        raise ValueError("Image must be uint8 gray/BGR/BGRA and at most 80 megapixels")
    return np.ascontiguousarray(image)


def detect_markers(image, config: dict) -> list:
    """Return canonical photo-pixel corners, in decoded TL,TR,BR,BL order.

    Repeated physical IDs and unregistered dictionary codes remain visible with
    warnings and must not be used for automatic calibration.
    """
    import cv2

    source = _image_array(image)
    if not config.get("dictionary"):
        return []
    dictionary = _dictionary(config)
    if source.ndim == 3:
        source = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY if source.shape[2] == 3 else cv2.COLOR_BGRA2GRAY)
    scale = min(1.0, 4000 / max(source.shape[:2]))
    working = cv2.resize(source, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else source
    parameters = _aruco().DetectorParameters()
    parameters.cornerRefinementMethod = _aruco().CORNER_REFINE_SUBPIX
    parameters.errorCorrectionRate = 0.4
    corners, identifiers, _rejected = _aruco().ArucoDetector(dictionary, parameters).detectMarkers(working)
    if identifiers is None:
        return []
    registry = {}
    for marker_id, marker in config.get("markers", {}).items():
        registry.setdefault(marker.get("aruco_id"), []).append(marker_id)
    counts = Counter(int(value) for value in identifiers.flatten())
    result = []
    for detected_corners, identifier in zip(corners, identifiers.flatten()):
        aruco_id = int(identifier)
        candidates = registry.get(aruco_id, [])
        warnings = []
        if len(candidates) != 1:
            warnings.append("unregistered_marker_id" if not candidates else "ambiguous_registry_id")
        if counts[aruco_id] > 1:
            warnings.append("duplicate_marker_id")
        result.append({
            "marker_id": candidates[0] if len(candidates) == 1 else "",
            "aruco_id": aruco_id,
            "corners": (detected_corners.reshape(4, 2) / scale).astype(float).tolist(),
            "warnings": warnings,
        })
    return sorted(result, key=lambda detection: (detection["aruco_id"], detection["corners"][0]))


def read_photo(path):
    """Decode JPG/PNG with EXIF transposed, returning a writable uint8 BGR array.

    The unchanged original is managed by the asset store. Every stored photo
    coordinate refers to this orientation-normalized pixel basis, not raw EXIF.
    """
    import numpy as np
    from PIL import Image, ImageOps

    source = Path(path)
    if source.stat().st_size > MAX_PHOTO_BYTES:
        raise ValueError("Photo exceeds the 256 MiB encoded size limit")
    try:
        with Image.open(source) as image:
            if image.format not in {"JPEG", "PNG"}:
                raise ValueError("Only JPEG and PNG photos are supported")
            if image.width * image.height > MAX_PHOTO_PIXELS:
                raise ValueError("Photo exceeds the 80 megapixel decoded size limit")
            oriented = ImageOps.exif_transpose(image)
            return np.asarray(oriented.convert("RGB"), dtype=np.uint8)[:, :, ::-1].copy()
    except (Image.DecompressionBombError, OSError) as error:
        raise ValueError(f"Cannot decode photo: {error}") from error
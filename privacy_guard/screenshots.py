"""Transient selective screenshot artifacts and an exact-byte outbound allowlist.

This module performs deterministic pixel masking. It does not run a CV model or
claim complete PII detection. Original images are decoded only in local memory;
the store retains only sanitized, metadata-free PNGs and non-content reports.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import threading
from collections import OrderedDict
from dataclasses import dataclass
from uuid import uuid4

from PIL import Image, ImageDraw

from .privacy_geometry import finite_number, pixel_rect

PNG_PREFIX = "data:image/png;base64,"
MAX_IMAGE_BYTES = 24 * 1024 * 1024
MAX_PIXELS = 12_000_000
MAX_DIMENSION = 8192
MAX_MASKS = 4000


@dataclass(frozen=True)
class _Artifact:
    id: str
    sha256: str
    width: int
    height: int
    png: bytes
    report_json: str

    def public(self) -> dict:
        encoded = base64.b64encode(self.png).decode("ascii")
        return {
            "id": self.id,
            "sha256": self.sha256,
            "width": self.width,
            "height": self.height,
            "mask_report": json.loads(self.report_json),
            "base64": encoded,
            "data_url": PNG_PREFIX + encoded,
        }


def _decode(encoded: str) -> bytes:
    if not isinstance(encoded, str):
        raise ValueError("Screenshot data must be PNG base64 text.")
    if encoded.startswith("data:"):
        if not encoded.startswith(PNG_PREFIX):
            raise ValueError("Only PNG screenshot data URLs are supported.")
        encoded = encoded[len(PNG_PREFIX) :]
    if len(encoded) > (MAX_IMAGE_BYTES * 4 // 3 + 8):
        raise ValueError("Screenshot exceeds the supported byte limit.")
    try:
        data = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError) as exc:
        raise ValueError("Screenshot is not valid base64.") from exc
    if not data or len(data) > MAX_IMAGE_BYTES:
        raise ValueError("Screenshot exceeds the supported byte limit.")
    return data


def _image(data: bytes) -> Image.Image:
    try:
        with Image.open(io.BytesIO(data)) as source:
            if source.format != "PNG" or getattr(source, "n_frames", 1) != 1:
                raise ValueError("Only single-frame PNG screenshots are supported.")
            width, height = source.size
            if width > MAX_DIMENSION or height > MAX_DIMENSION or width * height > MAX_PIXELS:
                raise ValueError("Screenshot exceeds the supported pixel limit.")
            source.load()
            # Flatten transparency, discard all metadata, and avoid copying any
            # invisible RGB data from fully transparent source pixels.
            with source.convert("RGBA") as rgba, Image.new("RGBA", source.size, "white") as background:
                background.alpha_composite(rgba)
                result = Image.new("RGB", source.size)
                result.paste(background.convert("RGB"))
            return result
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("Screenshot decoding failed; no image may be sent.") from exc


def _encode(image: Image.Image) -> bytes:
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _draw(image: Image.Image, masks: list[dict]) -> float:
    drawing = ImageDraw.Draw(image)
    with Image.new("1", image.size, 0) as coverage:
        coverage_drawing = ImageDraw.Draw(coverage)
        for mask in masks:
            bounds = (mask["x"], mask["y"], mask["x"] + mask["width"] - 1, mask["y"] + mask["height"] - 1)
            drawing.rectangle(bounds, fill=(0, 0, 0))
            coverage_drawing.rectangle(bounds, fill=1)
        histogram = coverage.histogram()
        masked_pixels = sum(histogram[1:])
    return round(masked_pixels / (image.width * image.height), 6)


class SanitizedImageStore:
    """Bounded, memory-only artifacts. Editing can add protection, never remove it."""

    def __init__(self, max_images: int = 16, max_bytes: int = 32 * 1024 * 1024):
        if max_images < 1 or max_bytes < 1:
            raise ValueError("Image store limits must be positive.")
        self.max_images, self.max_bytes = max_images, max_bytes
        self._artifacts: OrderedDict[str, _Artifact] = OrderedDict()
        self._mutex = threading.RLock()

    def _save(self, image: Image.Image, report: dict) -> dict:
        data = _encode(image)
        if len(data) > self.max_bytes:
            raise ValueError("Sanitized image exceeds the local artifact budget.")
        artifact = _Artifact(
            "img_" + uuid4().hex,
            hashlib.sha256(data).hexdigest(),
            image.width,
            image.height,
            data,
            json.dumps(report, ensure_ascii=False, allow_nan=False),
        )
        with self._mutex:
            self._artifacts[artifact.id] = artifact
            while (
                len(self._artifacts) > self.max_images
                or sum(len(a.png) for a in self._artifacts.values()) > self.max_bytes
            ):
                self._artifacts.popitem(last=False)
        return artifact.public()

    def create(self, raw_base64: str, geometry: dict, extra_masks: list[dict] | None = None) -> dict:
        if not isinstance(geometry, dict) or geometry.get("complete") is not True:
            raise ValueError("Screenshot privacy collection is incomplete; image sending is blocked.")
        if geometry.get("coordinate_space", "viewport-css") != "viewport-css":
            raise ValueError("Unsupported screenshot coordinate space.")
        viewport = geometry.get("viewport")
        if not isinstance(viewport, dict):
            raise ValueError("Screenshot viewport geometry is missing.")
        css_width = finite_number(viewport.get("width"), "viewport width")
        css_height = finite_number(viewport.get("height"), "viewport height")
        if css_width <= 0 or css_height <= 0:
            raise ValueError("Screenshot viewport dimensions must be positive.")
        if (
            abs(finite_number(geometry.get("visual_scale", 1), "visual scale") - 1) > 0.01
            or abs(finite_number(geometry.get("visual_offset_x", 0), "visual offset")) > 0.01
            or abs(finite_number(geometry.get("visual_offset_y", 0), "visual offset")) > 0.01
        ):
            raise ValueError("Pinch-zoomed screenshots are not supported; reset page pinch zoom and retry.")
        regions = geometry.get("regions")
        if not isinstance(regions, list) or len(regions) > MAX_MASKS:
            raise ValueError("Screenshot privacy regions are missing or exceed the supported limit.")
        manual = extra_masks if extra_masks is not None else []
        if not isinstance(manual, list) or len(regions) + len(manual) > MAX_MASKS:
            raise ValueError("Too many screenshot masks.")
        with _image(_decode(raw_base64)) as image:
            sx, sy = image.width / css_width, image.height / css_height
            if sx <= 0 or sy <= 0 or abs(image.height - css_height * sx) > 2:
                raise ValueError("Screenshot dimensions do not match the captured viewport.")
            masks = []
            reasons = {
                "password_field",
                "populated_field",
                "embedded_frame",
                "uninspected_media",
                "background_media",
                "uninspectable_component",
                "known_value",
                "email_pattern",
                "identifier_pattern",
                "number_pattern",
                "date_pattern",
                "amount_pattern",
                "labeled_private_text",
            }
            for region in regions:
                converted = pixel_rect(
                    region, scale_x=sx, scale_y=sy, width=image.width, height=image.height, padding=3
                )
                if converted is not None:
                    reason = region.get("reason", "private_region")
                    masks.append(
                        {
                            **converted,
                            "reason": reason if reason in reasons else "private_region",
                            "source": "automatic",
                        }
                    )
            for region in manual:
                converted = pixel_rect(
                    region, scale_x=1, scale_y=1, width=image.width, height=image.height, padding=1
                )
                if converted is not None:
                    masks.append({**converted, "reason": "manual_mask", "source": "manual"})
            coverage = _draw(image, masks)
            report = {
                "mode": "selective_dom_rules",
                "detector": "DOM fields, known values and patterns; no CV model",
                "automatic_masks": sum(m["source"] == "automatic" for m in masks),
                "manual_masks": sum(m["source"] == "manual" for m in masks),
                "masks": masks,
                "masked_pixel_fraction": coverage,
                "complete_pii_coverage_guaranteed": False,
                "geometry_validated": True,
                "metadata_stripped": True,
                "warnings": [
                    "Unknown personal text may be missed. Inspect the exact image before approving each upload.",
                    "Media and embedded frames are masked conservatively; no screenshot OCR or face detection model is used.",
                ],
            }
            return self._save(image, report)

    def get(self, artifact_id: str) -> dict:
        with self._mutex:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None:
                raise ValueError("Screenshot artifact expired; capture and approve a new image.")
            return artifact.public()

    def add_masks(self, artifact_id: str, boxes: list[dict]) -> dict:
        if not isinstance(boxes, list) or not boxes:
            raise ValueError("Draw at least one additional mask.")
        with self._mutex:
            artifact = self._artifacts.get(artifact_id)
            if artifact is None:
                raise ValueError("Screenshot artifact expired; capture and approve a new image.")
            report = json.loads(artifact.report_json)
            if len(report["masks"]) + len(boxes) > MAX_MASKS:
                raise ValueError("Too many screenshot masks.")
            masks = list(report["masks"])
            with _image(artifact.png) as image:
                for box in boxes:
                    converted = pixel_rect(
                        box, scale_x=1, scale_y=1, width=image.width, height=image.height, padding=1
                    )
                    if converted is not None:
                        masks.append({**converted, "reason": "manual_mask", "source": "manual"})
                if len(masks) == len(report["masks"]):
                    raise ValueError("Additional masks must intersect the screenshot.")
                report["masked_pixel_fraction"] = _draw(image, masks)
                report["masks"] = masks
                report["manual_masks"] = sum(mask["source"] == "manual" for mask in masks)
                return self._save(image, report)

    def verify(self, base64_or_data_url: str) -> dict:
        """Validate exact registered bytes; this is not user permission to upload."""
        data = _decode(base64_or_data_url)
        digest = hashlib.sha256(data).hexdigest()
        with self._mutex:
            for artifact in self._artifacts.values():
                if artifact.sha256 == digest and artifact.png == data:
                    return artifact.public()
        raise ValueError("Image was not created by the local screenshot sanitizer or has expired.")

    def clear(self) -> None:
        with self._mutex:
            self._artifacts.clear()

"""Exact pixel, geometry and provenance guarantees for selective screenshots."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os

import pytest
from PIL import Image, PngImagePlugin

from privacy_guard.privacy_geometry import PRIVACY_REGIONS_JS, pixel_rect
from privacy_guard.screenshots import PNG_PREFIX, SanitizedImageStore


def _png(width=200, height=100, *, metadata=False, alpha=False):
    with Image.new(
        "RGBA" if alpha else "RGB", (width, height), (37, 127, 211, 0) if alpha else (37, 127, 211)
    ) as image:
        output = io.BytesIO()
        info = PngImagePlugin.PngInfo()
        if metadata:
            info.add_text("private", "CANARY_PRIVATE_METADATA")
        image.save(output, format="PNG", pnginfo=info)
        return base64.b64encode(output.getvalue()).decode()


def _geometry(*regions, width=200, height=100, **kwargs):
    return {
        "complete": True,
        "coordinate_space": "viewport-css",
        "viewport": {"width": width, "height": height},
        "regions": list(regions),
        **kwargs,
    }


def _pixels(artifact):
    image = Image.open(io.BytesIO(base64.b64decode(artifact["base64"])))
    image.load()
    return image


def test_selective_masks_preserve_public_pixels_and_scale_private_edges():
    store = SanitizedImageStore()
    artifact = store.create(
        _png(400, 200),
        _geometry(
            {"x": 10.25, "y": 20.5, "width": 15.25, "height": 10.25, "reason": "known_value"},
        ),
    )
    mask = artifact["mask_report"]["masks"][0]
    assert mask == {
        "x": 14,
        "y": 35,
        "width": 43,
        "height": 33,
        "reason": "known_value",
        "source": "automatic",
    }
    with _pixels(artifact) as image:
        assert image.mode == "RGB"
        assert image.getpixel((14, 35)) == image.getpixel((56, 67)) == (0, 0, 0)
        assert image.getpixel((13, 35)) == image.getpixel((57, 67)) == (37, 127, 211)
        assert image.getpixel((300, 150)) == (37, 127, 211)
    assert artifact["mask_report"]["masked_pixel_fraction"] == round(43 * 33 / 80_000, 6)
    assert artifact["mask_report"]["complete_pii_coverage_guaranteed"] is False


def test_clipping_overlapping_masks_and_fraction_measure_union():
    store = SanitizedImageStore()
    artifact = store.create(
        _png(20, 20),
        _geometry(
            {"x": -3, "y": -3, "width": 10, "height": 10},
            {"x": -3, "y": -3, "width": 10, "height": 10},
            width=20,
            height=20,
        ),
    )
    assert artifact["mask_report"]["masks"][0]["width"] == 10
    assert artifact["mask_report"]["masked_pixel_fraction"] == 0.25
    assert (
        pixel_rect({"x": 100, "y": 100, "w": 1, "h": 1}, scale_x=1, scale_y=1, width=20, height=20, padding=1)
        is None
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"complete": False},
        {"complete": 1},
        {"coordinate_space": "document-css"},
        {"viewport": None},
        {"viewport": {"width": 0, "height": 100}},
        {"viewport": {"width": 200, "height": float("nan")}},
        {"regions": None},
        {"visual_scale": 1.1},
        {"visual_offset_x": 2},
        {"visual_offset_y": 1},
        {"regions": [{"x": 1, "y": 1, "width": -2, "height": 2}]},
        {"regions": [{"x": float("inf"), "y": 1, "width": 2, "height": 2}]},
        {"regions": [{"x": True, "y": 1, "width": 2, "height": 2}]},
    ],
)
def test_incomplete_or_invalid_geometry_never_registers_image(changes):
    store = SanitizedImageStore()
    original = _png()
    with pytest.raises(ValueError):
        store.create(original, _geometry(**changes))
    with pytest.raises(ValueError):
        store.verify(original)


def test_screenshot_aspect_mismatch_is_blocked():
    with pytest.raises(ValueError, match="dimensions"):
        SanitizedImageStore().create(_png(200, 105), _geometry())


def test_png_reencoding_removes_metadata_trailing_bytes_and_transparency():
    original = _png(metadata=True, alpha=True)
    original = base64.b64encode(base64.b64decode(original) + b"CANARY_TRAILING_BYTES").decode()
    artifact = SanitizedImageStore().create(original, _geometry())
    encoded = base64.b64decode(artifact["base64"])
    assert b"CANARY" not in encoded
    with _pixels(artifact) as image:
        assert image.info == {}
        assert image.getpixel((100, 50)) == (255, 255, 255)
    assert artifact["data_url"] == PNG_PREFIX + artifact["base64"]
    assert artifact["sha256"] == hashlib.sha256(encoded).hexdigest()


def test_only_exact_registered_sanitized_bytes_are_accepted():
    store = SanitizedImageStore()
    original = _png(metadata=True)
    artifact = store.create(original, _geometry({"x": 10, "y": 10, "width": 20, "height": 20}))
    assert store.verify(artifact["base64"])["id"] == artifact["id"]
    assert store.verify(artifact["data_url"])["id"] == artifact["id"]
    for candidate in (
        original,
        base64.b64encode(base64.b64decode(artifact["base64"]) + b"private").decode(),
        "https://example.com/image.png",
        "data:image/jpeg;base64," + artifact["base64"],
    ):
        with pytest.raises(ValueError):
            store.verify(candidate)


def test_artifact_is_immutable_and_manual_masks_only_add_protection():
    store = SanitizedImageStore()
    first = store.create(_png(), _geometry({"x": 10, "y": 10, "width": 10, "height": 10}))
    first["mask_report"]["masks"].clear()
    second = store.add_masks(first["id"], [{"x": 60, "y": 20, "width": 20, "height": 20}])
    assert first["id"] != second["id"] and first["sha256"] != second["sha256"]
    assert store.get(first["id"])["mask_report"]["automatic_masks"] == 1
    assert len(store.get(first["id"])["mask_report"]["masks"]) == 1
    assert second["mask_report"]["manual_masks"] == 1
    with _pixels(second) as image:
        assert image.getpixel((10, 10)) == image.getpixel((60, 20)) == (0, 0, 0)
        assert image.getpixel((100, 80)) == (37, 127, 211)
    with pytest.raises(ValueError):
        store.add_masks(second["id"], [{"x": 300, "y": 300, "width": 5, "height": 5}])


def test_store_expiration_and_lock_clear_revoke_artifact_provenance():
    store = SanitizedImageStore(max_images=1)
    first = store.create(_png(), _geometry({"x": 10, "y": 10, "width": 10, "height": 10}))
    second = store.create(_png(), _geometry({"x": 40, "y": 10, "width": 10, "height": 10}))
    with pytest.raises(ValueError, match="expired"):
        store.get(first["id"])
    with pytest.raises(ValueError):
        store.verify(first["base64"])
    store.clear()
    with pytest.raises(ValueError):
        store.verify(second["base64"])


def test_private_source_strings_are_not_copied_into_mask_reports():
    artifact = SanitizedImageStore().create(
        _png(),
        _geometry(
            {"x": 10, "y": 10, "width": 10, "height": 10, "reason": "CANARY_PRIVATE_REASON"},
            warnings=["CANARY_PRIVATE_WARNING"],
            url="https://example.test/CANARY_PRIVATE_URL",
        ),
    )
    assert "CANARY" not in json.dumps(artifact)
    assert artifact["mask_report"]["masks"][0]["reason"] == "private_region"


@pytest.mark.skipif(
    os.environ.get("GUARD_BROWSER_TESTS") != "1",
    reason="Set GUARD_BROWSER_TESTS=1 for the real DOM/PNG collector test",
)
async def test_real_dom_collector_masks_fields_echoes_patterns_and_unknown_media():
    from playwright.async_api import async_playwright

    from privacy_guard.browser import BrowserDriver
    from privacy_guard.config import DATA_DIR

    executable = BrowserDriver(DATA_DIR, DATA_DIR)._browser_executable()
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(executable_path=str(executable), headless=True)
        try:
            page = await browser.new_page(viewport={"width": 800, "height": 650}, device_scale_factor=2)
            await page.set_content("""<style>body {font: 18px sans-serif; margin:20px} p {margin:12px 0} input{width:240px;height:25px}</style>
                <h1 id="public">Application details</h1><label>Full name <input id="field" value="Mira Sen"></label>
                <p><span id="first">Mira </span><b id="last">Sen</b></p>
                <p id="regex">a+b(123)[z]</p><p id="unknown">unlisted.person@example.test</p>
                <p id="pan">ABCTY1234D</p><p id="amount">INR 24,550.25</p>
                <input id="password" type="password" value="my password">
                <canvas id="canvas" width="90" height="40"></canvas>
                <iframe id="frame" width="90" height="40" srcdoc="<p>private frame</p>"></iframe>
                <p id="public-end">Continue reviewing the application.</p>""")
            geometry = await page.evaluate(PRIVACY_REGIONS_JS, ["a+b(123)[z]"])
            assert geometry["complete"], geometry["warnings"]
            assert "Mira Sen" not in json.dumps(geometry)
            reasons = {box["reason"] for box in geometry["regions"]}
            assert {
                "populated_field",
                "password_field",
                "known_value",
                "email_pattern",
                "identifier_pattern",
                "amount_pattern",
                "uninspected_media",
                "embedded_frame",
            } <= reasons
            artifact = SanitizedImageStore().create(
                base64.b64encode(await page.screenshot()).decode(), geometry
            )
            with _pixels(artifact) as image:
                for selector in (
                    "#field",
                    "#first",
                    "#last",
                    "#regex",
                    "#unknown",
                    "#pan",
                    "#amount",
                    "#password",
                    "#canvas",
                    "#frame",
                ):
                    bounds = await page.locator(selector).bounding_box()
                    x = int(2 * (bounds["x"] + min(4, bounds["width"] / 2)))
                    y = int(2 * (bounds["y"] + bounds["height"] / 2))
                    assert image.getpixel((x, y)) == (0, 0, 0), selector
                bounds = await page.locator("#public").bounding_box()
                assert image.getpixel((int(2 * (bounds["x"] + 1)), int(2 * (bounds["y"] + 1)))) == (
                    255,
                    255,
                    255,
                )
            await page.add_style_tag(
                content='h1::before {content:"unlisted.person@example.test";position:absolute;left:0;top:0}'
            )
            assert (await page.evaluate(PRIVACY_REGIONS_JS, []))["complete"] is False
            await page.set_content(
                '<style>div::after {content:""; display:block; width:100px;height:100px; background-image:url("data:image/png;base64,iVBORw0KGgo=");}</style><div>Public text</div>'
            )
            assert (await page.evaluate(PRIVACY_REGIONS_JS, []))["complete"] is False
            await page.set_content('<div id="moving">Mira Sen</div>')
            await page.locator("#moving").evaluate(
                "element => element.animate([{transform:'translateX(0)'}, {transform:'translateX(100px)'}], {duration:10000, iterations:Infinity})"
            )
            assert (await page.evaluate(PRIVACY_REGIONS_JS, ["Mira Sen"]))["complete"] is False
        finally:
            await browser.close()

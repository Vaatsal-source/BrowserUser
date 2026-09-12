"""Conservative, offline sanitization for model-bound observations.

Known-value matching and heuristics are not a guarantee of complete PII recall.
The legacy observation path below fully obscures screenshots. The separately
approved v0.2 selective path lives in screenshots.py and privacy_geometry.py.
"""

from __future__ import annotations

import base64
import html
import io
import json
import math
import re
import unicodedata
from urllib.parse import unquote, urlsplit, urlunsplit

REDACTED = "[REDACTED]"
MAX_TEXT = 60_000

_PATTERNS = [
    re.compile(r"(?<![\w.+-])[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Z0-9-]+(?:\.[A-Z0-9-]+)+", re.I),
    re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b", re.I),
    re.compile(r"(?<!\w)(?:\+?\d[\s().-]*){8,19}(?!\w)"),
    re.compile(r"\b\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}\b"),
]
_LABELED = re.compile(
    r"(?im)(\b(?:full\s+name|name|email(?:\s+address)?|phone(?:\s+number)?|mobile|"
    r"address|date\s+of\s+birth|dob|password|passcode|otp|pan|aadhaar|aadhar|"
    r"account\s+number|bank\s+account|ifsc|api[_ -]?key|access[_ -]?token)\s*[:=]\s*)"
    r"([^\n\r;|]+)"
)
_AMOUNT = re.compile(r"(?i)(?:₹|\$|€|£|\b(?:INR|USD|EUR|GBP|Rs\.?))\s*[+-]?\d[\d,]*(?:\.\d+)?")
_JSON_STRING = re.compile(r'"(?:[^"\\\x00-\x1f]|\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))*"')
_JSON_ESCAPE_RUN = re.compile(r'(?:\\(?:["\\/bfnrt]|u[0-9a-fA-F]{4}))+')


def _known_pattern(secrets: list[str]) -> re.Pattern | None:
    sensitive = sorted(
        {unicodedata.normalize("NFKC", item) for item in secrets if isinstance(item, str) and item},
        key=len,
        reverse=True,
    )
    pattern = None
    if sensitive:
        # One replacement pass avoids shorter secrets corrupting replacement markers.
        pattern = re.compile(
            "|".join(
                re.escape(item) if len(item) >= 3 else r"(?<!\w)" + re.escape(item) + r"(?!\w)"
                for item in sensitive
            ),
            re.I,
        )
    return pattern


def _normalize_text(text: str) -> str:
    value = unicodedata.normalize("NFKC", html.unescape(text))
    for _ in range(2):
        decoded = unquote(value)
        if decoded == value:
            break
        value = decoded
    return value


def contains_private_text(text: str, private: list[str], *, min_length: int = 3) -> bool:
    """Check known values through JSON/Unicode/URL escapes without changing text.

    The final transport scan defaults to values of at least three characters:
    short values such as "1" are indistinguishable from public indices/schema
    constants. Field masking still handles them; value-only callers may pass 1.
    No generic PII heuristics are applied to schema descriptions or metadata.
    Excessive encoding depth raises rather than allowing unchecked text.
    """
    if not isinstance(text, str):
        return False
    pattern = _known_pattern(
        [value for value in private if isinstance(value, str) and len(value.strip()) >= min_length]
    )
    if pattern is None:
        return False
    value = text
    for _ in range(17):
        previous = value
        value = _normalize_text(value)
        if pattern.search(value):
            return True
        decoded = _JSON_ESCAPE_RUN.sub(lambda match: json.loads('"' + match.group(0) + '"'), value)
        if decoded == previous:
            return False
        value = decoded
    raise ValueError("Encoded model text is too deeply nested to inspect.")


def sanitize_text(text: str, secrets: list[str]) -> str:
    if not isinstance(text, str):
        return ""
    return _sanitize_text(text, _known_pattern(secrets), 0)


def _sanitize_text(text: str, pattern: re.Pattern | None, depth: int) -> str:
    if depth > 16:
        raise ValueError("Encoded model text is too deeply nested to sanitize.")
    # Sanitize JSON values before serializing again. Replacing across the source
    # syntax can corrupt quotes/newlines or accidentally consume the next field.
    if text.lstrip().startswith(("{", "[")):
        try:
            structured = json.loads(text)
        except (ValueError, RecursionError):
            structured = None
        if isinstance(structured, (dict, list)):

            def clean(item, level):
                if level > 40:
                    raise ValueError("Structured model text is too deeply nested to sanitize.")
                if isinstance(item, str):
                    return _sanitize_text(item, pattern, depth + 1)
                if isinstance(item, list):
                    return [clean(value, level + 1) for value in item]
                if isinstance(item, dict):
                    return {key: clean(value, level + 1) for key, value in item.items()}
                return item

            return json.dumps(clean(structured, 0), ensure_ascii=False)
    # Normalize common encodings before matching; this also catches encoded URLs.
    value = _normalize_text(text)
    if pattern:
        value = pattern.sub(REDACTED, value)

    def clean_token(match):
        # Native history mixes prose with JSON. Decode valid string tokens so
        # escaped quotes, Unicode escapes (including ASCII), surrogate pairs and
        # nested escaped JSON cannot disguise a known value. Re-encode any change.
        original = json.loads(match.group(0))
        cleaned = _sanitize_text(original, pattern, depth + 1)
        return json.dumps(cleaned, ensure_ascii=False) if cleaned != original else match.group(0)

    value = _JSON_STRING.sub(clean_token, value)
    value = _LABELED.sub(lambda match: match.group(1) + REDACTED, value)
    for pattern in _PATTERNS:
        value = pattern.sub(REDACTED, value)
    value = _AMOUNT.sub(REDACTED, value)
    return value


def _safe_url(value: str, secrets: list[str]) -> str:
    try:
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"}:
            return "[LOCAL_OR_UNSUPPORTED_URL]"
        # Never send credentials, query parameters, or fragments to the model.
        hostname = parts.hostname or ""
        if ":" in hostname:
            hostname = "[" + hostname + "]"
        if parts.port is not None:
            hostname += ":" + str(parts.port)
        return sanitize_text(urlunsplit((parts.scheme, hostname, parts.path, "", "")), secrets)
    except (ValueError, TypeError):
        return "[REDACTED_URL]"


def _geometry(rect: dict) -> dict:
    if not isinstance(rect, dict):
        return {}
    output = {}
    for key in ("x", "y", "w", "h", "width", "height"):
        value = rect.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
            output[key] = value
    return output


def _dimension(value) -> int:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return max(1, min(4096, int(value)))
    return 1


def sanitize_observation(raw: dict, secrets: list[str], vision: bool = False) -> dict:
    if not isinstance(raw, dict):
        raise ValueError("Browser observation must be an object.")
    fields = []
    redacted_values = 0
    all_fields = raw.get("fields", [])
    if not isinstance(all_fields, list) or len(all_fields) > 2_000:
        raise ValueError("Browser field extraction failed or exceeded the supported size.")
    # A value already visible in a form is a known private candidate, even before
    # it is saved in the vault. Repeated copies elsewhere on the page also need masking.
    observed_values = []
    for field in all_fields:
        if not isinstance(field, dict) or not isinstance(field.get("index"), int):
            raise ValueError("Browser observation contains an invalid field index.")
        value = field.get("value")
        if (
            field.get("tag") in {"input", "textarea", "select"}
            and field.get("input_type") not in {"button", "submit", "reset", "image"}
            and isinstance(value, str)
            and value.strip()
        ):
            observed_values.append(value)
    secrets = list(secrets) + observed_values
    # Field values are withheld even when they were not detected as personal data.
    for field in all_fields:
        if not isinstance(field, dict) or not isinstance(field.get("index"), int):
            raise ValueError("Browser observation contains an invalid field index.")
        value = field.get("value")
        populated = value is not None and value != ""
        if populated:
            redacted_values += 1
        sanitized = {
            "index": field["index"],
            "value": REDACTED if populated else "",
            "filled": bool(field.get("filled", populated)),
            "in_viewport": bool(field.get("in_viewport", True)),
            "rect": _geometry(field.get("rect", {})),
        }
        sanitized["label"] = sanitize_text(field.get("label", ""), secrets)[:2_000]
        # Structural enums describe controls, not user data. Whitelist them so a
        # short value such as "a" cannot corrupt an anchor's tag or opaque refs.
        tags = {"input", "textarea", "select", "button", "a"}
        input_types = {
            "",
            "text",
            "email",
            "tel",
            "url",
            "search",
            "number",
            "date",
            "month",
            "week",
            "time",
            "datetime-local",
            "password",
            "checkbox",
            "radio",
            "file",
            "hidden",
            "submit",
            "button",
            "reset",
            "range",
            "color",
            "image",
            "select-one",
            "select-multiple",
            "textarea",
        }
        sanitized["tag"] = field.get("tag", "") if field.get("tag", "") in tags else ""
        sanitized["input_type"] = (
            field.get("input_type", "") if field.get("input_type", "") in input_types else ""
        )
        options = field.get("options", [])
        if isinstance(options, list):
            safe_options = []
            for option_index, option in enumerate(options[:200]):
                if isinstance(option, str):
                    safe_options.append(sanitize_text(option, secrets)[:1_000])
                elif isinstance(option, dict):
                    # Display labels only; raw option values can contain identifiers.
                    safe_options.append(
                        {"index": option_index,
                         "label": sanitize_text(option.get("label", option.get("text", "")), secrets)[:1_000],
                         "disabled": bool(option.get("disabled", False)),
                         "selected": bool(option.get("selected", False))}
                    )
            sanitized["options"] = safe_options
        fields.append(sanitized)
    title = sanitize_text(raw.get("title", ""), secrets)[:2_000]
    text = sanitize_text(raw.get("text", ""), secrets)[:MAX_TEXT]
    screenshot = None
    image_mode = "not_sent"
    width = _dimension(raw.get("width"))
    height = _dimension(raw.get("height"))
    if vision and raw.get("screenshot"):
        from PIL import Image

        # Do not decode or transform the raw image: build a separate opaque image.
        if width * height > 4_000_000:
            image_mode = "omitted_size_limit"
        else:
            output = io.BytesIO()
            with Image.new("RGB", (width, height), (0, 0, 0)) as masked:
                masked.save(output, format="PNG")
            screenshot = base64.b64encode(output.getvalue()).decode("ascii")
            image_mode = "fully_masked"
    report = {
        "mode": "known_values_and_patterns",
        "redacted_field_values": redacted_values,
        "text_redaction_markers": text.count(REDACTED) + title.count(REDACTED),
        "image_mode": image_mode,
        "original_screenshot_sent": False,
        "complete_pii_coverage_guaranteed": False,
        "unsupported_frames": bool(raw.get("unsupported_frames", False)),
        "warnings": [
            "Text matching is heuristic and may miss unknown personal information. Review outgoing context.",
            "Vision is unavailable in this MVP: screenshots are omitted or fully masked, not selectively redacted.",
        ],
    }
    return {
        "title": title,
        "url": _safe_url(raw.get("url", ""), secrets),
        "text": text,
        "fields": fields,
        "screenshot": screenshot,
        "width": width,
        "height": height,
        "report": report,
    }

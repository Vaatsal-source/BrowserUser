import base64
import copy
import io
import json

import pytest
from PIL import Image

from privacy_guard.privacy import contains_private_text, sanitize_observation, sanitize_text


def observation():
    return {
        "title": "Application for Aria Example",
        "url": "https://user:pass@example.com/form?token=private#secret",
        "text": "Applicant Aria Example email aria@example.com PAN ABCDE1234F",
        "width": 300,
        "height": 200,
        "fields": [
            {
                "index": 1,
                "label": "Full name",
                "value": "Aria Example",
                "tag": "input",
                "input_type": "text",
                "filled": True,
                "rect": {"x": 12, "y": 30, "width": 150, "height": 20},
            }
        ],
        "screenshot": "raw-image-private-sentinel",
        "unknown_private_key": "raw-secret",
    }


def test_known_secrets_and_common_pii_patterns_are_removed():
    result = sanitize_text(
        "Aria Example; ARIA EXAMPLE; aria%40example.com; PAN ABCDE1234F; phone +91 98765 43210; ₹1,200.00",
        ["Aria Example"],
    )
    for secret in ("Aria", "aria@example", "ABCDE1234F", "98765", "1,200"):
        assert secret not in result
    assert "[REDACTED]" in result


def test_text_observation_is_a_whitelist_copy_and_suppresses_all_field_values():
    raw = observation()
    original = copy.deepcopy(raw)
    result = sanitize_observation(raw, ["Aria Example"])
    assert raw == original
    assert result["screenshot"] is None
    assert result["url"] == "https://example.com/form"
    assert result["fields"][0]["value"] == "[REDACTED]"
    assert result["fields"][0]["filled"] is True
    assert result["fields"][0]["rect"]["width"] == 150
    assert "unknown_private_key" not in result
    serialized = json.dumps(result)
    for secret in (
        "Aria Example",
        "aria@example.com",
        "ABCDE1234F",
        "raw-image-private-sentinel",
        "raw-secret",
    ):
        assert secret not in serialized


def test_vision_returns_only_an_independently_created_opaque_black_image():
    raw = observation()
    raw["unsupported_frames"] = True
    result = sanitize_observation(raw, ["Aria Example"], vision=True)
    assert result["report"]["image_mode"] == "fully_masked"
    assert result["report"]["unsupported_frames"] is True
    with Image.open(io.BytesIO(base64.b64decode(result["screenshot"]))) as image:
        assert image.size == (300, 200)
        assert image.getextrema() == ((0, 0), (0, 0), (0, 0))
    assert result["report"]["original_screenshot_sent"] is False


def test_invalid_or_excessive_geometry_never_falls_back_to_original_image():
    raw = observation()
    raw["width"], raw["height"] = 4000, 4000
    result = sanitize_observation(raw, [], vision=True)
    assert result["screenshot"] is None
    assert result["report"]["image_mode"] == "omitted_size_limit"
    raw["width"] = float("nan")
    raw["fields"][0]["rect"]["x"] = float("inf")
    result = sanitize_observation(raw, [], vision=True)
    assert result["width"] == 1 and "x" not in result["fields"][0]["rect"]


def test_unknown_populated_fields_and_option_values_are_not_transmitted():
    raw = observation()
    raw["fields"] = [
        {"index": 4, "value": "UNKNOWN-PII-CANARY", "options": [{"label": "Work", "value": "private-id-123"}]}
    ]
    result = sanitize_observation(raw, [])
    assert "UNKNOWN-PII-CANARY" not in json.dumps(result)
    assert "private-id-123" not in json.dumps(result)


def test_redaction_reapplies_after_a_fill_and_matches_unicode_and_encoded_values():
    raw = observation()
    raw["text"] = "Aria%20Example submitted ＡＢＣＤＥ1234F"
    result = sanitize_observation(raw, ["Aria Example"])
    assert "Aria" not in result["text"] and "1234F" not in result["text"]


def test_invalid_fields_block_observation_processing():
    raw = observation()
    raw["fields"] = [{"index": "unsafe"}]
    with pytest.raises(ValueError):
        sanitize_observation(raw, [])


@pytest.mark.parametrize(
    "secret", ['Mira "Sen"', "Mira\\Sen", "Mira\nSen", "Míra Sen", "Mira 🐈", "Mira / Sen"]
)
def test_json_escaped_secrets_are_redacted_without_breaking_structure(secret):
    raw = json.dumps({"nested": [{"extracted_content": secret, "next": "Continue"}], "index": 3})
    parsed = json.loads(sanitize_text(raw, [secret]))
    assert parsed == {"nested": [{"extracted_content": "[REDACTED]", "next": "Continue"}], "index": 3}


def test_arbitrary_unicode_escapes_in_mixed_history_do_not_hide_known_values():
    encoded = r'{"extracted_content":"\u004d\u0069\u0072\u0061\u0020\u0053\u0065\u006e"}'
    sanitized = sanitize_text("Previous result: " + encoded, ["Mira Sen"])
    assert json.loads(sanitized.removeprefix("Previous result: ")) == {"extracted_content": "[REDACTED]"}


def test_nested_escaped_history_and_patterns_are_sanitized():
    secret = 'Mira "Sen"'
    nested = json.dumps({"history": json.dumps({"result": secret, "contact": "new.person@example.test"})})
    outer = json.loads(sanitize_text(nested, [secret]))
    assert json.loads(outer["history"]) == {"result": "[REDACTED]", "contact": "[REDACTED]"}


def test_labeled_json_values_keep_neighboring_properties_and_literal_escapes():
    original = {"message": "Name: Mira Sen", "next": "Continue", "path": r"C:\\public\\file"}
    assert json.loads(sanitize_text(json.dumps(original), [])) == {**original, "message": "Name: [REDACTED]"}


@pytest.mark.parametrize(
    "encoded",
    [
        r'{"value":"Mira \"Sen\""}',
        r"Mira \"Sen\"",
        r'{"value":"\u004d\u0069\u0072\u0061\u0020\u0022Sen\u0022"}',
        r"Previous: \u004d\u0069\u0072\u0061\u0020\u0022Sen\u0022",
        "Mira%20%22Sen%22",
        "Mira &quot;Sen&quot;",
    ],
)
def test_final_private_scan_detects_fresh_secret_in_encoded_payload(encoded):
    # The value was not known when the request was prepared; a fresh scan must
    # reject it after the user adds the record while an approval is pending.
    assert not contains_private_text(encoded, [])
    assert contains_private_text(encoded, ['Mira "Sen"'])


def test_final_private_scan_handles_surrogates_nested_json_and_preserves_schema():
    secret = "Mira 🐈"
    encoded = json.dumps({"history": json.dumps({"result": secret})})
    assert contains_private_text(encoded, [secret])
    assert not contains_private_text(sanitize_text(encoded, [secret]), [secret])
    schema = '{"minimum":1,"type":"string","description":"Name: field label"}'
    assert not contains_private_text(schema, ["1", "a", "Mira 🐈"])
    assert contains_private_text('"1"', ["1"], min_length=1)
    assert not contains_private_text('"ref_a123"', ["1"], min_length=1)

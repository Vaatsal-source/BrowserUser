import io
import shutil
from decimal import Decimal

import pytest

from privacy_guard.documents import calculate_total, extract_document


def test_labeled_text_is_extracted_as_unreviewed_candidates():
    result = extract_document(
        "sample.txt", b"Name: Aria Example\nEmail: aria@example.com\nPAN: ABCDE1234F\nTotal: 123.45\n"
    )
    fields = {item["field_type"]: item for item in result["candidates"]}
    assert fields["full_name"]["value"] == "Aria Example"
    assert fields["pan"]["value"] == "ABCDE1234F"
    assert all(item["reviewed"] is False for item in result["candidates"])
    assert fields["full_name"]["source"] == "document:line:1"
    assert result["warnings"] and not result["used_ocr"]


def test_csv_does_not_guess_transaction_signs_or_compute_unreviewed_totals():
    result = extract_document(
        "statement.csv", b"date,description,amount\n2025-01-01,transfer,20.30\n2025-01-02,refund,3.00\n"
    )
    assert not any(item["field_type"] == "amount" for item in result["candidates"])
    assert "not automatically classified" in " ".join(result["warnings"])


def test_key_value_csv_and_utf8_bom_are_supported():
    result = extract_document("profile.csv", b"\xef\xbb\xbfName,Aria Example\nEmail,aria@example.com\n")
    assert any(item["field_type"] == "full_name" for item in result["candidates"])


@pytest.mark.parametrize(
    "filename,data",
    [
        ("bad.exe", b"x"),
        ("empty.txt", b""),
        ("binary.txt", b"\xff"),
        ("binary.txt", b"a\x00b"),
        ("long.txt", b"a" * 150_001),
    ],
)
def test_unsupported_or_invalid_documents_fail(filename, data):
    with pytest.raises(ValueError):
        extract_document(filename, data)


def test_corrupt_pdf_fails_in_isolated_worker():
    with pytest.raises(ValueError):
        extract_document("broken.pdf", b"%PDF-1.7\nnot a PDF file")


def test_encrypted_pdf_is_rejected_without_trying_a_cloud_fallback():
    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("test-password")
    buffer = io.BytesIO()
    writer.write(buffer)
    with pytest.raises(ValueError, match="Password-protected"):
        extract_document("encrypted.pdf", buffer.getvalue())


def test_text_pdf_is_extracted_in_the_local_worker():
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    page = writer.add_blank_page(width=600, height=400)
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
    )
    stream = DecodedStreamObject()
    stream.set_data(b"BT /F1 12 Tf 40 350 Td (Name: Aria Example) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = io.BytesIO()
    writer.write(buffer)
    result = extract_document("profile.pdf", buffer.getvalue())
    assert "Aria Example" in result["text"]
    assert any(item["field_type"] == "full_name" for item in result["candidates"])
    assert not result["used_ocr"]


@pytest.mark.skipif(shutil.which("tesseract") is None, reason="Local Tesseract executable is not installed")
def test_image_ocr_runs_locally_and_yields_reviewable_fields():
    from PIL import Image, ImageDraw, ImageFont

    image = Image.new("RGB", (1100, 160), "white")
    draw = ImageDraw.Draw(image)
    draw.text((30, 40), "Name: Aria Example", fill="black", font=ImageFont.load_default(size=42))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    result = extract_document("profile.png", buffer.getvalue())
    assert "Aria Example" in result["text"]
    assert result["used_ocr"]
    assert all(item["confidence"] <= 0.65 and not item["reviewed"] for item in result["candidates"])


def test_local_decimal_total_is_exact_and_supports_explicit_negative_amounts():
    assert calculate_total(["0.10", "0.20", "-0.05"]) == "0.25"
    assert calculate_total([Decimal("1.23"), 2]) == "3.23"


@pytest.mark.parametrize(
    "values",
    [[], ["NaN"], ["Infinity"], [0.1], [True], ["₹1,200"], ["1,200.00"], ["1e4"], ["unknown"], [None]],
)
def test_ambiguous_or_nonfinite_amounts_fail(values):
    with pytest.raises(ValueError):
        calculate_total(values)

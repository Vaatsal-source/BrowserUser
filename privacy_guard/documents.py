"""Bounded offline document extraction and exact, explicit local arithmetic.

OCR uses the installed Tesseract executable through stdin/stdout: documents are
not uploaded and plaintext image intermediates are not written to disk.
"""

from __future__ import annotations

import csv
import io
import multiprocessing
import re
import shutil
import subprocess
import sys
from decimal import Decimal, InvalidOperation, localcontext
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
MAX_PAGES = 20
MAX_PIXELS = 24_000_000
MAX_TEXT = 150_000
EXTRACTION_TIMEOUT = 45
SUPPORTED_EXTENSIONS = {".txt", ".csv", ".pdf", ".png", ".jpg", ".jpeg", ".webp"}


def calculate_total(values: list[str | int | Decimal]) -> str:
    """Sum explicit amounts; no currency guessing, float rounding, or silent skips."""
    if not values or len(values) > 10_000:
        raise ValueError("Supply between 1 and 10,000 reviewed amounts.")
    with localcontext() as context:
        context.prec = 40
        total = Decimal("0")
        for value in values:
            if isinstance(value, (bool, float)):
                raise ValueError("Amounts must be decimal strings, integers, or Decimal values.")
            raw = str(value).strip()
            if not re.fullmatch(r"[+-]?\d{1,24}(?:\.\d{1,8})?", raw):
                raise ValueError("Use explicit decimal amounts without currency symbols or separators.")
            try:
                amount = Decimal(raw)
            except InvalidOperation as exc:
                raise ValueError("Invalid decimal amount.") from exc
            if not amount.is_finite():
                raise ValueError("Amounts must be finite.")
            total += amount
        return format(total, "f")


_LABELS = {
    "full_name": ("full name", "name", "account holder", "applicant name"),
    "email": ("email", "email address", "e-mail"),
    "phone": ("phone", "phone number", "mobile", "mobile number", "telephone"),
    "address": ("address", "residential address", "current address"),
    "pan": ("pan", "pan number", "permanent account number"),
    "aadhaar": ("aadhaar", "aadhaar number", "aadhar", "aadhar number"),
    "date_of_birth": ("date of birth", "dob", "birth date"),
    "bank_account": ("account number", "bank account", "bank account number"),
    "ifsc": ("ifsc", "ifsc code"),
    "amount": ("total", "total amount", "statement total", "amount", "balance", "closing balance"),
    "postal_code": ("postal code", "postcode", "pin code", "zip code"),
}


def _candidates(text: str, *, ocr: bool) -> list[dict]:
    candidates: list[dict] = []
    seen: set[tuple[str, str]] = set()
    aliases = {label.casefold(): field_type for field_type, labels in _LABELS.items() for label in labels}

    def add(label: str, field_type: str, value: str, line: int, confidence: float):
        value = value.strip()
        if not value or len(value) > 2_000 or (field_type, value) in seen:
            return
        seen.add((field_type, value))
        candidates.append(
            {
                "label": label,
                "field_type": field_type,
                "value": value,
                "confidence": min(confidence, 0.65) if ocr else confidence,
                "source": f"document:line:{line}",
                "reviewed": False,
            }
        )

    for line_number, line in enumerate(text.splitlines(), 1):
        match = re.match(r"^\s*([^:\t=]{1,50})\s*[:\t=]\s*(.+?)\s*$", line)
        if match:
            field_type = aliases.get(match[1].strip().casefold())
            if field_type:
                add(match[1].strip(), field_type, match[2], line_number, 0.9)
        for email in re.findall(
            r"(?<![\w.+-])[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+", line
        ):
            add("Email", "email", email, line_number, 0.8)
        for pan in re.findall(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b", line):
            add("PAN", "pan", pan, line_number, 0.8)
        if len(candidates) > 500:
            raise ValueError("Document contains too many candidate fields; split it into smaller documents.")
    return candidates


def _ocr_image(image) -> str:
    executable = shutil.which("tesseract")
    if executable is None:
        raise ValueError("Local OCR requires Tesseract. Install it and retry; no document was uploaded.")
    width, height = image.size
    if width <= 0 or height <= 0 or width * height > MAX_PIXELS:
        raise ValueError("Image exceeds the 24 megapixel extraction limit.")
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="PNG")
    try:
        result = subprocess.run(
            [executable, "stdin", "stdout", "-l", "eng", "--psm", "6"],
            input=buffer.getvalue(),
            capture_output=True,
            timeout=20,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError("Local OCR timed out. Try a smaller or clearer document.") from exc
    if result.returncode:
        raise ValueError("Local OCR failed. Check that Tesseract and English language data are installed.")
    text = result.stdout.decode("utf-8", errors="replace")
    if len(text) > MAX_TEXT:
        raise ValueError("Extracted text exceeds the supported size.")
    return text


def _extract_inline(filename: str, data: bytes) -> dict:
    extension = Path(filename).suffix.lower()
    warnings: list[str] = [
        "Extracted fields are suggestions. Review values against the original before using or saving them."
    ]
    used_ocr = False
    if extension in {".txt", ".csv"}:
        try:
            text = data.decode("utf-8-sig", errors="strict")
        except UnicodeError as exc:
            raise ValueError("Text documents must use UTF-8 encoding.") from exc
        if "\x00" in text:
            raise ValueError("Binary content is not supported as a text document.")
    elif extension == ".pdf":
        if not data.lstrip().startswith(b"%PDF-"):
            raise ValueError("The document is not a valid PDF.")
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data), strict=True)
            if reader.is_encrypted:
                raise ValueError("Password-protected PDFs are not supported. Upload an unlocked copy.")
            if len(reader.pages) > MAX_PAGES:
                raise ValueError("PDFs may contain at most 20 pages in this prototype.")
            parts: list[str] = []
            readable_pages = 0
            for page_number, page in enumerate(reader.pages):
                page_text = page.extract_text() or ""
                if not page_text.strip():
                    import pypdfium2 as pdfium

                    # Renderer is opened and closed within the isolated worker.
                    with pdfium.PdfDocument(data) as rendered:
                        rendered_page = rendered[page_number]
                        try:
                            width, height = rendered_page.get_size()
                            if width <= 0 or height <= 0 or width * height * 4 > MAX_PIXELS:
                                raise ValueError("PDF page exceeds the supported rendering size.")
                            bitmap = rendered_page.render(scale=2)
                            try:
                                page_text = _ocr_image(bitmap.to_pil())
                            finally:
                                bitmap.close()
                        finally:
                            rendered_page.close()
                    used_ocr = True
                readable_pages += bool(page_text.strip())
                parts.append(f"--- Page {page_number + 1} ---\n{page_text}")
                if sum(map(len, parts)) > MAX_TEXT:
                    raise ValueError("Extracted text exceeds the supported size.")
            if not readable_pages:
                raise ValueError("No readable text was found in the PDF. Try a clearer document.")
            text = "\n".join(parts)
            warnings.append(
                "PDF text layers and image-only pages are supported. Mixed text/image pages may contain additional text that needs manual review."
            )
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("PDF parsing failed. Try a valid, smaller, unlocked PDF.") from exc
    else:
        from PIL import Image, ImageOps

        try:
            with Image.open(io.BytesIO(data)) as image:
                if image.width * image.height > MAX_PIXELS:
                    raise ValueError("Image exceeds the 24 megapixel extraction limit.")
                text = _ocr_image(ImageOps.exif_transpose(image))
            used_ocr = True
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError("Image parsing failed. Upload a valid PNG, JPEG, or WebP image.") from exc
    if len(text) > MAX_TEXT:
        raise ValueError("Extracted text exceeds the 150,000 character limit.")
    if not text.strip():
        raise ValueError("No readable text was found. Try a clearer document or enter the details manually.")
    candidates = _candidates(text, ocr=used_ocr)
    if extension == ".csv":
        rows = list(csv.reader(io.StringIO(text)))
        # Explicit key/value CSV only; transaction totals need reviewed inputs.
        for line_number, row in enumerate(rows, 1):
            if len(row) == 2:
                extracted = _candidates(f"{row[0]}: {row[1]}", ocr=False)
                for candidate in extracted:
                    candidate["source"] = f"document:line:{line_number}"
                candidates.extend(extracted)
        unique = {}
        for item in candidates:
            unique.setdefault((item["field_type"], item["value"]), item)
        candidates = list(unique.values())
        warnings.append(
            "Transaction columns are not automatically classified or totaled. Select reviewed amounts for local calculation."
        )
    if used_ocr:
        warnings.append(
            "OCR was performed locally. Candidate confidence is a conservative heuristic, not a calibrated accuracy score."
        )
    return {"text": text, "candidates": candidates, "warnings": warnings, "used_ocr": used_ocr}


def _worker(connection, filename: str, data: bytes):
    try:
        # Bound CPU in a separate process; address-space limits are unreliable on macOS.
        try:
            import resource

            resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
            if sys.platform.startswith("linux"):
                resource.setrlimit(resource.RLIMIT_AS, (1024**3, 1024**3))
        except (ImportError, OSError, ValueError):
            pass
        result = _extract_inline(filename, data)
        connection.send({"ok": True, "result": result})
    except ValueError as exc:
        connection.send({"ok": False, "error": str(exc)})
    except Exception:
        connection.send({"ok": False, "error": "Document extraction failed in the isolated local worker."})
    finally:
        connection.close()


def extract_document(filename: str, data: bytes) -> dict:
    if not isinstance(filename, str) or Path(filename).suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError("Supported documents: TXT, CSV, PDF, PNG, JPEG, and WebP.")
    if not isinstance(data, bytes) or not data or len(data) > MAX_BYTES:
        raise ValueError("Documents must contain between 1 byte and 10 MiB.")
    if Path(filename).suffix.lower() in {".txt", ".csv"}:
        return _extract_inline(filename, data)
    context = multiprocessing.get_context("spawn")
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(child, filename, data), daemon=True)
    try:
        process.start()
        child.close()
        if not parent.poll(EXTRACTION_TIMEOUT):
            raise ValueError("Document extraction timed out. Split the document or use a smaller file.")
        try:
            message = parent.recv()
        except EOFError as exc:
            raise ValueError("Document extraction stopped before completion.") from exc
        if not message.get("ok"):
            raise ValueError(message.get("error", "Document extraction failed."))
        return message["result"]
    finally:
        parent.close()
        child.close()
        if process.pid is not None:
            if process.is_alive():
                process.terminate()
            process.join(timeout=3)
            if process.is_alive():
                process.kill()
                process.join(timeout=1)

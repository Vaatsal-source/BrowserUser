"""Authenticated loopback API and local dashboard hosting."""

import asyncio
import re
import secrets
import time
from contextlib import asynccontextmanager
from email import policy
from email.parser import BytesParser
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .audio import MAX_AUDIO_BYTES, WhisperTranscriber, audio_format
from .config import DATA_DIR, DEMO_PORT, PORT, ROOT
from .diagnostics import logger, request_id
from .documents import calculate_total, extract_document
from .gateway import ModelGateway, validate_endpoint
from .models import (
    ApprovalRequest,
    CalculationRequest,
    ControlRequest,
    MaskRequest,
    PairRequest,
    RecordRequest,
    ReviewRequest,
    SettingsRequest,
    TaskRequest,
    UnlockRequest,
)
from .tasks import TaskManager
from .vault import Vault


def create_app(data_dir: Path = DATA_DIR, browser=None, pairing_code=None, testing=False) -> FastAPI:
    data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    vault = Vault(data_dir)
    if browser is None:
        from .browser import BrowserDriver

        browser = BrowserDriver(data_dir, ROOT / "apps/extension")
    gateway = ModelGateway()
    transcriber = WhisperTranscriber()
    gateway.extra_secrets = lambda: [transcriber.api_key] if transcriber.api_key else []
    manager = TaskManager(vault, browser, gateway, DEMO_PORT, PORT)
    sessions = {}
    code = pairing_code or secrets.token_urlsafe(12)
    pair_expires = time.time() + 1800
    attempts: list[float] = []
    origins = {f"http://127.0.0.1:{PORT}", f"http://localhost:{PORT}"}
    if testing:
        origins.add("http://testserver")

    @asynccontextmanager
    async def lifespan(app):
        if not testing:
            print(
                f"\nDev Privacy Guard {__version__}\nDashboard: http://127.0.0.1:{PORT}\n"
                f"Pairing code (valid 30 minutes): {code}\n"
                "Use this code in the dashboard and extension. Keep this terminal running.\n",
                flush=True,
            )
        try:
            yield
        finally:
            try:
                await manager.stop_all()
            finally:
                try:
                    await browser.shutdown()
                finally:
                    transcriber.clear()
                    vault.lock()

    app = FastAPI(
        title="Dev Privacy Guard",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.vault, app.state.manager, app.state.gateway, app.state.browser = (
        vault,
        manager,
        gateway,
        browser,
    )
    app.state.transcriber = transcriber

    def provider_settings():
        return {**gateway.settings(), "whisper_configured": transcriber.configured}

    @app.middleware("http")
    async def guard(request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0]
        if host not in ({"127.0.0.1", "localhost", "testserver"} if testing else {"127.0.0.1", "localhost"}):
            return JSONResponse({"detail": "Invalid local host"}, status_code=403)
        path, source = request.url.path, request.headers.get("origin")
        extension_source = request.headers.get("x-guard-origin")
        if extension_source is not None:
            if not re.fullmatch(r"chrome-extension://[a-p]{32}", extension_source):
                return JSONResponse({"detail": "Invalid extension identity"}, status_code=403)
            if source is not None and source != extension_source:
                return JSONResponse({"detail": "Origin identity mismatch"}, status_code=403)
            source = extension_source
        permitted_origin = source in origins or bool(
            re.fullmatch(r"chrome-extension://[a-p]{32}", source or "")
        )
        if source and not permitted_origin:
            return JSONResponse({"detail": "Origin not authorized"}, status_code=403)
        if request.method == "OPTIONS":
            response = JSONResponse({"ok": True})
        else:
            if path.startswith("/api/") and path != "/api/v1/pair":
                token = request.headers.get("authorization", "").removeprefix("Bearer ")
                session = sessions.get(token)
                same_origin_browser = bool(
                    session
                    and session["role"] == "dashboard"
                    and source is None
                    and session["origin"] in origins
                    and request.headers.get("sec-fetch-site") == "same-origin"
                )
                if (
                    not session
                    or session["expires"] < time.time()
                    or (session["origin"] != source and not same_origin_browser)
                ):
                    return JSONResponse(
                        {"detail": "Pair this interface with the local companion"}, status_code=401
                    )
                request.state.interface_role = session["role"]
                # Extension can control tasks, but cannot export or edit the user's vault/settings.
                if session["role"] == "extension" and (
                    path.startswith("/api/v1/records")
                    or path.startswith("/api/v1/documents")
                    or path.startswith("/api/v1/vault")
                    or path.startswith("/api/v1/calculations")
                    or path.endswith("/image-preview")
                    or path.endswith("/masks")
                    or (request.method != "GET" and path in ("/api/v1/settings", "/api/v1/demo/seed"))
                ):
                    return JSONResponse(
                        {"detail": "Use the paired dashboard for private data management"}, status_code=403
                    )
            try:
                length = int(request.headers.get("content-length", "0"))
            except ValueError:
                return JSONResponse({"detail": "Invalid content length"}, status_code=400)
            if length > 11 * 1024 * 1024:
                return JSONResponse({"detail": "Upload exceeds 10 MiB plus form overhead"}, status_code=413)
            response = await call_next(request)
        if source and permitted_origin:
            response.headers["Access-Control-Allow-Origin"] = source
            response.headers["Vary"] = "Origin"
            response.headers["Access-Control-Allow-Methods"] = "GET,POST,DELETE,OPTIONS"
            response.headers["Access-Control-Allow-Headers"] = "Authorization,Content-Type,X-Guard-Origin"
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; media-src 'self' blob:; connect-src 'self'; font-src 'self'; "
            "object-src 'none'; frame-ancestors 'none'"
        )
        return response

    @app.middleware("http")
    async def diagnostics(request: Request, call_next):
        identifier = secrets.token_hex(8)
        request.state.request_id = identifier
        token = request_id.set(identifier)
        started = time.monotonic()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = identifier
            return response
        except Exception:
            logger.error("request.failed", exc_info=True)
            raise
        finally:
            route = request.scope.get("route")
            # Route templates omit private IDs; unmatched paths are never recorded.
            path = getattr(route, "path", "<unmatched>")
            level = 40 if status >= 500 else 30 if status >= 400 else 10 if request.method == "GET" else 20
            logger.log(level, "request.completed method=%s route=%s status=%s duration_ms=%.1f",
                       request.method, path, status, (time.monotonic() - started) * 1000)
            request_id.reset(token)

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, exc):
        logger.warning("request.invalid_request", exc_info=(type(exc), exc, exc.__traceback__))
        # Pydantic's default validation response includes the original private input.
        return JSONResponse({"detail": "Invalid request fields or values"}, status_code=422)

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        logger.warning("request.value_error", exc_info=(type(exc), exc, exc.__traceback__))
        # Only local application ValueErrors carry user-facing safe messages.
        return JSONResponse({"detail": str(exc)}, status_code=400)

    @app.exception_handler(PermissionError)
    async def locked(request, exc):
        logger.warning("request.locked", exc_info=(type(exc), exc, exc.__traceback__))
        return JSONResponse({"detail": "Unlock the local vault to continue"}, status_code=423)

    @app.exception_handler(KeyError)
    async def missing(request, exc):
        logger.warning("request.missing", exc_info=(type(exc), exc, exc.__traceback__))
        return JSONResponse({"detail": "Item not found"}, status_code=404)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        return JSONResponse(
            {"detail": "The local operation failed safely. Check connection and try again."}, status_code=500,
            headers={"X-Request-ID": getattr(request.state, "request_id", "-")},
        )

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": __version__}

    @app.post("/api/v1/pair")
    async def pair(body: PairRequest, request: Request):
        attempts[:] = [stamp for stamp in attempts if stamp > time.time() - 60]
        if len(attempts) >= 10:
            raise HTTPException(429, "Too many pairing attempts; wait a minute")
        attempts.append(time.time())
        if time.time() > pair_expires or not secrets.compare_digest(body.code, code):
            raise HTTPException(
                401, "Invalid or expired pairing code; restart the companion to generate another"
            )
        source = request.headers.get("origin") or request.headers.get("x-guard-origin")
        token = secrets.token_urlsafe(32)
        sessions[token] = {
            "origin": source,
            "expires": time.time() + 12 * 3600,
            "role": "extension" if (source or "").startswith("chrome-extension:") else "dashboard",
        }
        return {"token": token}

    @app.get("/api/v1/status")
    async def status():
        browser_state = browser.status()
        browser_state["connected"] = browser_state.get("running", False)
        return {
            "vault": {"initialized": vault.initialized, "unlocked": vault.unlocked},
            "browser": browser_state,
            "provider": provider_settings(),
            "task": manager.current() if vault.unlocked else None,
        }

    @app.post("/api/v1/vault/initialize")
    async def initialize(body: UnlockRequest):
        vault.initialize(body.passphrase)
        return {"unlocked": True}

    @app.post("/api/v1/vault/unlock")
    async def unlock(body: UnlockRequest):
        vault.unlock(body.passphrase)
        manager.restore()
        saved = vault.load_blob("provider_settings", {})
        for key in ("mode", "model", "base_url", "api_key", "fallback_model", "fallback_api_key"):
            if key in saved:
                setattr(gateway, key, saved[key])
        transcriber.api_key = saved.get("whisper_api_key", "")
        return {"unlocked": True}

    @app.post("/api/v1/vault/lock")
    async def lock():
        await manager.stop_all()
        manager.persist()
        gateway.api_key = ""
        gateway.fallback_api_key = ""
        transcriber.clear()
        if hasattr(gateway, "image_store"):
            gateway.image_store.clear()
        gateway.sent.clear()
        browser.invalidate()
        manager.tasks.clear()
        vault.lock()
        return {"unlocked": False}

    def require_unlock():
        if not vault.unlocked:
            raise PermissionError

    @app.post("/api/v1/audio/transcribe")
    async def transcribe_audio(request: Request):
        require_unlock()
        content_type = request.headers.get("content-type", "")
        audio_format(content_type)
        if not transcriber.configured:
            raise ValueError("Add your OpenAI Whisper API key in dashboard Settings first")
        recording = bytearray()
        async for chunk in request.stream():
            recording.extend(chunk)
            if len(recording) > MAX_AUDIO_BYTES:
                raise HTTPException(413, "Recording exceeds 10 MiB; record a shorter command")
        require_unlock()
        try:
            result = await transcriber.transcribe(bytes(recording), content_type)
        finally:
            recording.clear()
        require_unlock()
        return result

    @app.get("/api/v1/records")
    async def records():
        return {"records": vault.records()}

    @app.get("/api/v1/record-catalog")
    async def record_catalog():
        from .privacy import sanitize_text

        private = vault.secrets()
        return {
            "records": [
                {
                    "id": record["id"],
                    "label": sanitize_text(record["label"], private),
                    "field_type": sanitize_text(record["field_type"], private),
                    "scope": record["scope"],
                }
                for record in vault.records()
                if record.get("reviewed", True)
            ]
        }

    @app.post("/api/v1/records")
    async def put_record(body: RecordRequest):
        return vault.put_record(**body.model_dump())

    @app.delete("/api/v1/records/{record_id}")
    async def delete_record(record_id: str):
        vault.delete_record(record_id)
        return {"ok": True}

    @app.get("/api/v1/documents")
    async def documents():
        docs = vault.documents()
        for document in docs:
            analysis = vault.load_blob("document_" + document["id"], {})
            document["candidates"] = analysis.get("candidates", [])
            document["warnings"] = analysis.get("warnings", [])
            document["status"] = "reviewed" if analysis.get("reviewed") else "needs_review"
        return {"documents": docs}

    @app.post("/api/v1/documents")
    async def upload(request: Request):
        require_unlock()
        # Parse bounded multipart bytes in memory: no UploadFile spooling private originals to disk.
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 11 * 1024 * 1024:
                raise HTTPException(413, "Document exceeds the size limit")
        content_type = request.headers.get("content-type", "")
        if not content_type.startswith("multipart/form-data;"):
            raise ValueError("Use a multipart file upload")
        message = BytesParser(policy=policy.default).parsebytes(
            ("Content-Type: " + content_type + "\r\nMIME-Version: 1.0\r\n\r\n").encode() + content
        )
        parts = [p for p in message.iter_parts() if p.get_filename()]
        if len(parts) != 1:
            raise ValueError("Upload one document at a time")
        filename = Path(parts[0].get_filename()).name
        data = parts[0].get_payload(decode=True)
        if not data or len(data) > 10 * 1024 * 1024:
            raise ValueError("Documents must be between 1 byte and 10 MiB")
        analysis = await asyncio.to_thread(extract_document, filename, data)
        require_unlock()  # Lock may have occurred while OCR was running.
        document = vault.put_document(filename, data)
        vault.save_blob("document_" + document["id"], analysis)
        return {
            "document": document,
            "candidates": analysis["candidates"],
            "warnings": analysis.get("warnings", []),
        }

    @app.post("/api/v1/documents/{document_id}/review")
    async def review(document_id: str, body: ReviewRequest):
        return {
            "records": vault.confirm_document(
                document_id, [candidate.model_dump() for candidate in body.candidates]
            )
        }

    @app.delete("/api/v1/documents/{document_id}")
    async def delete_document(document_id: str):
        vault.delete_document(document_id)
        vault.save_blob("document_" + document_id, {})
        return {
            "ok": True,
            "notice": "Original and extraction removed. Confirmed profile records remain until you delete them.",
        }

    @app.get("/api/v1/settings")
    async def settings():
        return provider_settings()

    @app.post("/api/v1/calculations/sum")
    async def sum_records(body: CalculationRequest):
        require_unlock()
        if len(set(body.record_ids)) != len(body.record_ids):
            raise ValueError("Select each record once to avoid double counting")
        records_by_id = {record["id"]: record for record in vault.records()}
        selected = [records_by_id[identifier] for identifier in body.record_ids]
        if any(
            not record.get("reviewed", True)
            or record["field_type"] not in ("money", "amount", "statement_total", "total")
            for record in selected
        ):
            raise ValueError("Only reviewed amount records may be summed")
        total = calculate_total([record["value"] for record in selected])
        record = vault.put_record(
            body.label,
            "money",
            total,
            scope="calculation",
            source="local sum (" + body.currency + "): " + ",".join(body.record_ids),
        )
        return {"record": record, "currency": body.currency}

    @app.post("/api/v1/settings")
    async def update_settings(body: SettingsRequest):
        require_unlock()
        if any(not worker.done() for worker in manager.workers.values()):
            raise ValueError("Pause the active task before changing model settings")
        gateway.base_url = validate_endpoint(body.base_url)
        gateway.mode, gateway.model = body.mode, body.model.strip()
        if body.api_key is not None:
            gateway.api_key = body.api_key.strip()
        if "fallback_model" in body.model_fields_set:
            gateway.fallback_model = body.fallback_model.strip()
        if "fallback_api_key" in body.model_fields_set:
            gateway.fallback_api_key = (body.fallback_api_key or "").strip()
        if "whisper_api_key" in body.model_fields_set:
            transcriber.clear()
            transcriber.api_key = (body.whisper_api_key or "").strip()
        vault.save_blob(
            "provider_settings",
            {**gateway.settings(), "api_key": gateway.api_key,
             "fallback_api_key": gateway.fallback_api_key, "whisper_api_key": transcriber.api_key},
        )
        return provider_settings()

    @app.post("/api/v1/browser/launch")
    async def launch_browser():
        return await browser.launch()

    @app.get("/api/v1/browser/tabs")
    async def tabs():
        return {"tabs": await browser.tabs()}

    @app.post("/api/v1/browser/demo")
    async def open_demo():
        await browser.launch()
        return await browser.new_page(f"http://127.0.0.1:{DEMO_PORT}/")

    @app.post("/api/v1/browser/portal")
    async def open_portal():
        await browser.launch()
        return await browser.new_page(f"http://127.0.0.1:{DEMO_PORT}/portal.html")

    @app.post("/api/v1/demo/seed")
    async def seed(partial: bool = False):
        require_unlock()
        existing = vault.records()
        seeds = [
            ("Full name", "person_name", "Aarav Example"),
            ("Email", "email", "aarav@example.test"),
            ("Phone", "phone", "9000012345"),
            ("PAN", "pan", "ABCDE1234F"),
            ("Address", "address", "42 Sample Lane, Demo City"),
            ("Statement total", "money", "125000.00"),
        ]
        if partial:
            seeds = seeds[:3]
        for label, kind, value in seeds:
            if not any(r["label"] == label for r in existing):
                vault.put_record(label, kind, value, source="synthetic demo")
        records = vault.records()
        labels = {label for label, _, _ in seeds}
        return {
            "records": records,
            "suggested_record_ids": [record["id"] for record in records if record["label"] in labels],
        }

    @app.post("/api/v1/tasks")
    async def start_task(body: TaskRequest):
        return await manager.start(body)

    @app.get("/api/v1/tasks")
    async def list_tasks():
        require_unlock()
        return {"tasks": [manager.public(t) for t in reversed(list(manager.tasks.values()))]}

    @app.get("/api/v1/tasks/{task_id}")
    async def get_task(task_id: str):
        require_unlock()
        return manager.public(manager.tasks[task_id])

    @app.post("/api/v1/tasks/{task_id}/control")
    async def control(task_id: str, body: ControlRequest):
        require_unlock()
        return await manager.control(task_id, body.action, body.record_ids)

    @app.post("/api/v1/tasks/{task_id}/approve")
    async def approve(task_id: str, body: ApprovalRequest, request: Request):
        require_unlock()
        pending = manager.tasks[task_id].get("pending")
        if (
            request.state.interface_role == "extension"
            and pending
            and pending["kind"] == "image"
        ):
            raise HTTPException(403, "Review and approve the exact screenshot in the dashboard")
        return manager.approve(task_id, body.approval_id, body.approved)

    @app.get("/api/v1/tasks/{task_id}/image-preview")
    async def image_preview(task_id: str):
        require_unlock()
        return manager.image_preview(task_id)

    @app.post("/api/v1/tasks/{task_id}/masks")
    async def add_masks(task_id: str, body: MaskRequest):
        require_unlock()
        return manager.update_masks(
            task_id, body.approval_id, [mask.model_dump() for mask in body.masks]
        )

    @app.get("/api/v1/schema")
    async def schema():
        return app.openapi()

    frontend = ROOT / "apps/dashboard/dist"
    if (frontend / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=frontend / "assets"), name="assets")

    @app.get("/{path:path}")
    async def dashboard(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "Unknown API endpoint")
        if not (frontend / "index.html").exists():
            return JSONResponse(
                {"detail": "Build the dashboard: npm --prefix apps/dashboard run build"}, status_code=503
            )
        return FileResponse(frontend / "index.html")

    return app

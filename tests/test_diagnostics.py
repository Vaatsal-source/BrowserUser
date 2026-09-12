"""Diagnostics remain useful without exposing private inputs or exception text."""

import json
import logging

import httpx
import pytest

from privacy_guard.api import create_app
from privacy_guard.diagnostics import DiagnosticFormatter, request_id, traced


def test_exception_formatter_omits_private_values():
    try:
        raise ValueError("secret-api-key and private page text")
    except ValueError:
        import sys

        record = logging.LogRecord("privacy_guard", logging.ERROR, __file__, 1,
                                   "operation.failed", (), sys.exc_info())
    entry = json.loads(DiagnosticFormatter().format(record))
    assert entry["error_type"] == "ValueError"
    assert entry["stack"][-1]["function"] == "test_exception_formatter_omits_private_values"
    assert "secret-api-key" not in json.dumps(entry)
    assert "private page text" not in json.dumps(entry)


@pytest.mark.asyncio
async def test_traced_preserves_failure_and_omits_arguments(caplog):
    @traced("test.operation")
    async def operation(private):
        raise RuntimeError(private)

    with caplog.at_level(logging.INFO, logger="privacy_guard"):
        with pytest.raises(RuntimeError, match="private-input"):
            await operation("private-input")
    assert [r.getMessage().split()[0] for r in caplog.records] == [
        "test.operation.started", "test.operation.failed",
    ]
    assert all("private-input" not in DiagnosticFormatter().format(r) for r in caplog.records)


@pytest.mark.asyncio
async def test_requests_correlate_errors_and_hide_url_inputs(tmp_path, caplog):
    app = create_app(tmp_path, browser=object(), testing=True)

    @app.get("/explode/{private_id}")
    async def explode(private_id: str):
        raise RuntimeError("private-exception")

    app.router.routes.insert(0, app.router.routes.pop())

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    with caplog.at_level(logging.DEBUG, logger="privacy_guard"):
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            denied = await client.get("/api/v1/records?token=secret-query")
            response = await client.get("/explode/secret-path?token=secret-query")
    assert denied.status_code == 401
    assert denied.headers["X-Request-ID"]
    assert response.status_code == 500
    assert response.headers["X-Request-ID"] != denied.headers["X-Request-ID"]
    assert request_id.get() == "-"
    messages = "\n".join(DiagnosticFormatter().format(r) for r in caplog.records)
    assert "/explode/{private_id}" in messages
    assert "request.failed" in messages
    for private in ("secret-query", "secret-path", "private-exception"):
        assert private not in messages


def test_file_logging_rotation_configuration_and_correlation(tmp_path, monkeypatch):
    from privacy_guard.diagnostics import configure_logging, logger

    old_handlers, old_level, old_propagate = logger.handlers[:], logger.level, logger.propagate
    monkeypatch.setenv("GUARD_LOG_LEVEL", "DEBUG")
    token = request_id.set("correlation-test")
    try:
        path = configure_logging(tmp_path)
        configure_logging(tmp_path)
        logger.info("test.saved")
        entries = [json.loads(line) for line in path.read_text().splitlines()]
        saved = [entry for entry in entries if entry["event"] == "test.saved"]
        assert len(saved) == 1
        assert saved[0]["request_id"] == "correlation-test"
        assert path.stat().st_mode & 0o777 == 0o600
        file_handler = next(h for h in logger.handlers if hasattr(h, "maxBytes"))
        assert file_handler.maxBytes == 5_000_000
        assert file_handler.backupCount == 3
    finally:
        request_id.reset(token)
        for handler in list(logger.handlers):
            if handler not in old_handlers:
                logger.removeHandler(handler)
                handler.close()
        logger.handlers = old_handlers
        logger.setLevel(old_level)
        logger.propagate = old_propagate

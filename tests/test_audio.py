"""Whisper uploads are explicit, unredacted, bounded, and independent of task execution."""

import asyncio
import json

import httpx
import pytest
from test_workflow import client_for

from privacy_guard import audio
from privacy_guard.audio import MAX_AUDIO_BYTES, WHISPER_ENDPOINT, WhisperTranscriber


def transport_client(monkeypatch, handler):
    original = httpx.AsyncClient

    class ClientWithFixtureTransport(original):
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            assert kwargs["follow_redirects"] is False
            super().__init__(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(audio.httpx, "AsyncClient", ClientWithFixtureTransport)


@pytest.mark.asyncio
async def test_audio_and_transcript_are_unredacted_and_upload_has_fixed_provider(monkeypatch):
    transcriber = WhisperTranscriber()
    transcriber.api_key = "synthetic-api-secret"
    audio_bytes = b"SYNTHETIC PRIVATE SPOKEN INFORMATION"
    requests = []

    async def handler(request):
        requests.append(request)
        assert str(request.url) == WHISPER_ENDPOINT
        assert request.headers["authorization"] == "Bearer synthetic-api-secret"
        assert audio_bytes in await request.aread()
        assert b"whisper-1" in request.content
        assert b'filename="recording.webm"' in request.content
        return httpx.Response(200, json={"text": "  My name is Private Person, PAN ABCDE1234F.  "})

    transport_client(monkeypatch, handler)
    result = await transcriber.transcribe(audio_bytes, "audio/webm;codecs=opus")
    assert result == {
        "text": "My name is Private Person, PAN ABCDE1234F.",
        "provider": "OpenAI",
        "model": "whisper-1",
    }
    assert len(requests) == 1
    assert transcriber._pending is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [302, 401, 403, 429, 500])
async def test_audio_errors_never_echo_provider_body_or_retry(monkeypatch, status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status, json={"error": "PRIVATE RAW AUDIO AND API KEY"}, headers={"location": "https://evil.test"}
        )

    transport_client(monkeypatch, handler)
    transcriber = WhisperTranscriber()
    transcriber.api_key = "secret"
    with pytest.raises(ValueError) as error:
        await transcriber.transcribe(b"audio", "audio/wav")
    assert "PRIVATE" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("data,mime", [(b"", "audio/webm"), (b"audio", "text/plain")])
async def test_invalid_recording_blocked_before_network(monkeypatch, data, mime):
    async def forbidden(*args):
        pytest.fail("Invalid recording reached network")

    transcriber = WhisperTranscriber()
    transcriber.api_key = "secret"
    monkeypatch.setattr(transcriber, "_request", forbidden)
    with pytest.raises(ValueError):
        await transcriber.transcribe(data, mime)


@pytest.mark.asyncio
async def test_lock_cancels_transcription_and_blocks_overlapping_upload(monkeypatch):
    started = asyncio.Event()

    async def delayed(*args):
        started.set()
        await asyncio.Future()

    transcriber = WhisperTranscriber()
    transcriber.api_key = "secret"
    monkeypatch.setattr(transcriber, "_request", delayed)
    first = asyncio.create_task(transcriber.transcribe(b"audio", "audio/webm"))
    await started.wait()
    with pytest.raises(ValueError, match="already"):
        await transcriber.transcribe(b"audio", "audio/webm")
    transcriber.clear()
    with pytest.raises(ValueError, match="locked"):
        await first
    assert not transcriber.configured


@pytest.mark.asyncio
async def test_transcription_route_returns_draft_only_and_key_stays_encrypted(tmp_path, monkeypatch):
    app, driver, client = await client_for(tmp_path)
    phrase = "Sensitive Person ABCDE1234F"
    key = "PRIVATE_WHISPER_KEY_CANARY_4489"
    received = []

    async def request(data, mime, extension, api_key):
        received.append((data, mime, extension, api_key))
        return phrase

    monkeypatch.setattr(app.state.transcriber, "_request", request)
    async with client:
        saved = await client.post("/api/v1/settings", json={"whisper_api_key": key})
        assert saved.status_code == 200
        assert saved.json()["whisper_configured"] is True
        assert key not in saved.text
        response = await client.post(
            "/api/v1/audio/transcribe", content=b"original audio", headers={"Content-Type": "audio/webm"}
        )
        assert response.status_code == 200, response.text
        assert response.json()["text"] == phrase
        assert received == [(b"original audio", "audio/webm", "webm", key)]
        assert not app.state.manager.tasks
        assert not driver.executions
        assert phrase not in json.dumps(app.state.vault.records())
        database = (tmp_path / "vault.sqlite3").read_bytes()
        assert phrase.encode() not in database and key.encode() not in database
        await client.post("/api/v1/vault/lock")
        assert not app.state.transcriber.configured
        locked = await client.post(
            "/api/v1/audio/transcribe", content=b"audio", headers={"Content-Type": "audio/webm"}
        )
        assert locked.status_code == 423
        await client.post("/api/v1/vault/unlock", json={"passphrase": "testing-long-passphrase"})
        assert app.state.transcriber.api_key == key
        assert (await client.get("/api/v1/settings")).json()["whisper_configured"] is True
        await client.post("/api/v1/settings", json={"model": "test-model"})
        assert app.state.transcriber.api_key == key
        cleared = await client.post("/api/v1/settings", json={"whisper_api_key": None})
        assert cleared.json()["whisper_configured"] is False
        assert not app.state.transcriber.api_key


@pytest.mark.asyncio
async def test_transcription_requires_pairing_and_size_limit(tmp_path, monkeypatch):
    app, _, client = await client_for(tmp_path)
    app.state.transcriber.api_key = "secret"

    async def forbidden(*args):
        pytest.fail("Rejected request reached Whisper")

    monkeypatch.setattr(app.state.transcriber, "_request", forbidden)
    async with client:
        rejected = await client.post(
            "/api/v1/audio/transcribe", content=b"audio",
            headers={"Content-Type": "audio/webm", "Authorization": "Bearer invalid"},
        )
        assert rejected.status_code == 401
        rejected = await client.post(
            "/api/v1/audio/transcribe", content=b"x" * (MAX_AUDIO_BYTES + 1),
            headers={"Content-Type": "audio/webm"},
        )
        assert rejected.status_code == 413


@pytest.mark.asyncio
async def test_paired_extension_can_transcribe_but_not_read_original_preview(tmp_path, monkeypatch):
    app, _, client = await client_for(tmp_path)
    app.state.transcriber.api_key = "secret"

    async def request(*args):
        return "Open the application"

    monkeypatch.setattr(app.state.transcriber, "_request", request)
    async with client:
        extension = "chrome-extension://" + "a" * 32
        paired = await client.post(
            "/api/v1/pair", json={"code": "test-code-123"}, headers={"Origin": extension}
        )
        headers = {"Origin": extension, "Authorization": "Bearer " + paired.json()["token"]}
        result = await client.post(
            "/api/v1/audio/transcribe", content=b"audio",
            headers={**headers, "Content-Type": "audio/webm"},
        )
        assert result.status_code == 200
        assert (await client.get("/api/v1/tasks/test/image-preview", headers=headers)).status_code == 403
        assert (await client.post("/api/v1/tasks/test/masks", json={}, headers=headers)).status_code == 403

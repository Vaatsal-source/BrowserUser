"""Explicit cloud transcription. Audio is unredacted, transient, and never starts a task."""

import asyncio
import json

import httpx

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_TRANSCRIPT_BYTES = 128 * 1024
WHISPER_ENDPOINT = "https://api.openai.com/v1/audio/transcriptions"
FORMATS = {
    "audio/webm": "webm",
    "video/webm": "webm",
    "audio/ogg": "ogg",
    "audio/wav": "wav",
    "audio/wave": "wav",
    "audio/x-wav": "wav",
    "audio/mpeg": "mp3",
    "audio/mp3": "mp3",
    "audio/mp4": "m4a",
    "video/mp4": "mp4",
    "audio/m4a": "m4a",
    "audio/x-m4a": "m4a",
    "audio/flac": "flac",
}


def audio_format(content_type: str) -> tuple[str, str]:
    mime = content_type.split(";", 1)[0].strip().lower()
    extension = FORMATS.get(mime)
    if not extension:
        raise ValueError("Use a WebM, WAV, MP3, MP4, M4A, OGG, or FLAC recording")
    return mime, extension


class WhisperTranscriber:
    """One bounded upload at a time; credentials and pending work are cleared on lock."""

    def __init__(self):
        self.api_key = ""
        self._pending: asyncio.Task | None = None
        self._generation = 0

    @property
    def configured(self) -> bool:
        return bool(self.api_key)

    def clear(self):
        self._generation += 1
        self.api_key = ""
        if self._pending and not self._pending.done():
            self._pending.cancel()

    async def transcribe(self, data: bytes, content_type: str) -> dict:
        if not self.api_key:
            raise ValueError("Add your OpenAI Whisper API key in dashboard Settings first")
        mime, extension = audio_format(content_type)
        if not data or len(data) > MAX_AUDIO_BYTES:
            raise ValueError("Recordings must be between 1 byte and 10 MiB")
        if self._pending and not self._pending.done():
            raise ValueError("A recording is already being transcribed; wait or discard it first")
        key = self.api_key
        generation = self._generation
        work = asyncio.create_task(self._request(data, mime, extension, key))
        self._pending = work
        try:
            text = await work
            # A lock or key change during an upload invalidates its result.
            if self.api_key != key or self._generation != generation:
                raise ValueError("Transcription was discarded because the voice session changed")
            return {"text": text, "provider": "OpenAI", "model": "whisper-1"}
        except asyncio.CancelledError:
            if not self.api_key or self._generation != generation:
                raise ValueError("Transcription was discarded because the vault was locked or voice settings changed") from None
            raise
        finally:
            if self._pending is work:
                self._pending = None

    @staticmethod
    async def _request(data: bytes, mime: str, extension: str, key: str) -> str:
        try:
            # Fixed provider, generated filename, no proxy, redirects, SDK retries, or audio logs.
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(60, connect=15), follow_redirects=False, trust_env=False
            ) as client:
                async with client.stream(
                    "POST",
                    WHISPER_ENDPOINT,
                    headers={"Authorization": "Bearer " + key},
                    data={"model": "whisper-1", "response_format": "json"},
                    files={"file": ("recording." + extension, data, mime)},
                ) as response:
                    if response.status_code in (401, 403):
                        raise ValueError("OpenAI rejected the Whisper API key; check dashboard Settings")
                    if response.status_code == 429:
                        raise ValueError("Whisper is rate-limited or out of API credit; retry when available")
                    if response.status_code != 200:
                        raise ValueError("Whisper could not transcribe this recording; retry or type your task")
                    result = bytearray()
                    async for chunk in response.aiter_bytes():
                        result.extend(chunk)
                        if len(result) > MAX_TRANSCRIPT_BYTES:
                            raise ValueError("Whisper returned an oversized transcript")
            parsed = json.loads(result)
            text = parsed.get("text") if isinstance(parsed, dict) else None
            if not isinstance(text, str) or not text.strip():
                raise ValueError("No speech was recognized; record again or type your task")
            if len(text) > 20000:
                raise ValueError("The transcript is too long; record a shorter task")
            # Intentionally no redaction: the user reviews/edits this local draft before task creation.
            return text.strip()
        except httpx.TimeoutException:
            raise ValueError("Whisper timed out; your task has not started") from None
        except httpx.HTTPError:
            raise ValueError("Could not reach OpenAI Whisper; check your connection or type your task") from None
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("Whisper returned an invalid transcript response") from None

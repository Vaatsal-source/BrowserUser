"""Provider switches preserve credentials, request review and bounded attempts."""
import json
from types import SimpleNamespace

import httpx
import pytest

from privacy_guard.agent_runtime import BrowserAgentRuntime, ReferenceInput
from privacy_guard.gateway import ModelGateway, digest
from privacy_guard.tasks import TaskManager
from privacy_guard.vault import Vault


def make_runtime(tmp_path):
    vault = Vault(tmp_path)
    vault.initialize("synthetic-long-passphrase")
    gateway = ModelGateway()
    gateway.api_key = "synthetic-gemini-key"
    gateway.fallback_api_key = "synthetic-openai-key"
    manager = TaskManager(vault, SimpleNamespace(), gateway)
    task = {"_generation": 0, "_goal": "Inspect", "_origin": "https://example.test",
            "_ref_ids": {}, "_refs": {}, "events": []}
    return BrowserAgentRuntime(manager, task)


def transport(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: original(transport=httpx.MockTransport(handler), **kw))


@pytest.mark.parametrize("review", [False, True])
async def test_switch_uses_separate_key_and_fresh_text_review(tmp_path, monkeypatch, review):
    runtime = make_runtime(tmp_path)
    runtime.task["_review_text"] = review
    calls, approvals = [], []

    async def approve(task, generation, kind, title, payload):
        approvals.append(payload)
        assert len(calls) == 1
        assert payload["destination"] == "https://api.openai.com/v1"
        assert payload["request"]["model"] == "gpt-4.1-mini"

    runtime.manager.approval = approve

    def handle(request):
        calls.append(request)
        if request.url.host == "generativelanguage.googleapis.com":
            assert request.headers["authorization"] == "Bearer synthetic-gemini-key"
            return httpx.Response(400)
        assert request.headers["authorization"] == "Bearer synthetic-openai-key"
        assert "synthetic-gemini-key" not in request.content.decode()
        assert "synthetic-openai-key" not in request.content.decode()
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"index":1,"value_ref":"ref"}'}}]})

    transport(monkeypatch, handle)
    payload = runtime.llm.prepare([{"role": "user", "content": "Inspect"}], ReferenceInput, [])
    await runtime.llm.send(payload, digest(payload), [])
    assert len(calls) == 2
    assert len(approvals) == int(review)
    assert payload["model"] == "gemini-2.5-flash"
    assert runtime.manager.gateway.model == "gemini-2.5-flash"
    assert runtime.llm.model == "gpt-4.1-mini"


@pytest.mark.parametrize("fallback_key,status,expected", [("", 400, 1), ("key", 500, 2), ("key", 307, 1)])
async def test_no_fallback_without_key_and_no_retry_loop(tmp_path, monkeypatch, fallback_key, status, expected):
    runtime = make_runtime(tmp_path)
    runtime.manager.gateway.fallback_api_key = fallback_key
    from privacy_guard.agent_llm import GuardedChatModel
    runtime.llm = GuardedChatModel(runtime)
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(status)

    transport(monkeypatch, handle)
    payload = runtime.llm.prepare([{"role": "user", "content": "Inspect"}], ReferenceInput, [])
    with pytest.raises(ValueError, match="HTTP"):
        await runtime.llm.send(payload, digest(payload), [])
    assert len(calls) == expected


async def test_denied_fallback_review_never_contacts_openai(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    runtime.task["_review_text"] = True
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(400)

    async def deny(*args):
        raise PermissionError("Denied")

    runtime.manager.approval = deny
    transport(monkeypatch, handle)
    payload = runtime.llm.prepare([{"role": "user", "content": "Inspect"}], ReferenceInput, [])
    with pytest.raises(PermissionError):
        await runtime.llm.send(payload, digest(payload), [])
    assert len(calls) == 1


async def test_changed_fallback_settings_cannot_reuse_approval(tmp_path, monkeypatch):
    runtime = make_runtime(tmp_path)
    payload = runtime.llm.prepare([{"role": "user", "content": "Inspect"}], ReferenceInput, [])
    runtime.manager.gateway.fallback_api_key = "changed-key"
    transport(monkeypatch, lambda request: pytest.fail("No request allowed"))
    with pytest.raises(ValueError, match="settings changed"):
        await runtime.llm.send(payload, digest(payload), [])


async def test_image_fallback_requires_new_destination_review_and_sends_updated_masks(tmp_path, monkeypatch):
    import base64
    import io

    from PIL import Image

    runtime = make_runtime(tmp_path)
    pixels = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(pixels, format="PNG")
    raw = base64.b64encode(pixels.getvalue()).decode()
    store = runtime.manager.gateway.image_store
    artifact = store.create(raw, {"complete": True, "viewport": {"width": 32, "height": 32},
                                  "regions": []})
    runtime.image_state = {"original": "data:image/png;base64," + raw, "artifact": artifact,
                           "messages": [{"role": "user", "content": "Inspect layout"}], "private": []}
    runtime._prepare_image_payload()
    primary = runtime.image_state["payload"]
    calls = []
    reviewed = []

    async def approve(task, generation, kind, title, payload):
        assert len(calls) == 1
        assert kind == "image"
        assert payload["destination"] == "https://api.openai.com/v1"
        assert payload["sha256"] != digest(primary)
        runtime.image_state["artifact"] = store.add_masks(artifact["id"], [{"x": 0, "y": 0, "width": 12, "height": 12}])
        runtime._prepare_image_payload()
        reviewed.append(runtime.image_state["payload"])

    async def check_target():
        runtime.check()

    runtime.check_target = check_target
    runtime.manager.approval = approve

    def handle(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(429)
        assert reviewed and json.loads(request.content) == reviewed[0]
        return httpx.Response(200, json={"choices": [{"message": {"content": '{}'}}]})

    transport(monkeypatch, handle)
    await runtime.llm.send(primary, digest(primary), [], artifact)
    assert len(calls) == 2


async def test_fallback_key_is_encrypted_restored_and_never_returned(tmp_path):
    from privacy_guard.api import create_app

    app = create_app(tmp_path, browser=SimpleNamespace(invalidate=lambda: None), pairing_code="test-pair-code", testing=True)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver") as client:
        response = await client.post("/api/v1/pair", json={"code": "test-pair-code"})
        client.headers["Authorization"] = "Bearer " + response.json()["token"]
        password = "synthetic-long-passphrase"
        await client.post("/api/v1/vault/initialize", json={"passphrase": password})
        response = await client.post("/api/v1/settings", json={"fallback_api_key": "private-fallback-canary"})
        assert response.status_code == 200
        assert response.json()["fallback_configured"]
        assert "private-fallback-canary" not in response.text
        await client.post("/api/v1/vault/lock")
        assert not (await client.get("/api/v1/settings")).json()["fallback_configured"]
        await client.post("/api/v1/vault/unlock", json={"passphrase": password})
        assert (await client.get("/api/v1/settings")).json()["fallback_configured"]
        await client.post("/api/v1/settings", json={"whisper_api_key": None})
        assert (await client.get("/api/v1/settings")).json()["fallback_configured"]
        await client.post("/api/v1/settings", json={"fallback_api_key": None})
        assert not (await client.get("/api/v1/settings")).json()["fallback_configured"]
    for file in tmp_path.rglob("*"):
        if file.is_file():
            assert b"private-fallback-canary" not in file.read_bytes()

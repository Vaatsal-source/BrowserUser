"""Cross-module privacy/execution tests with a deterministic browser boundary."""

import asyncio
import copy
import json

import httpx
import pytest

from privacy_guard.api import create_app
from privacy_guard.gateway import ModelGateway, digest
from privacy_guard.models import ProposedAction
from privacy_guard.privacy import sanitize_observation


class FakeBrowser:
    def __init__(self):
        self.value = ""
        self.executions = []
        self.can_execute = lambda: True
        self.rev = 0

    def status(self):
        return {"running": True}

    def invalidate(self):
        self.rev += 1

    async def shutdown(self):
        pass

    async def tabs(self):
        return [{"target_id": "exact-tab", "url": "https://example.test/form", "title": "Application"}]

    async def observe(self, target_id):
        return {
            "target_id": target_id,
            "url": "https://example.test/form",
            "title": "Application",
            "epoch": str(self.rev),
            "fields": [
                {
                    "index": 1,
                    "label": "Full name",
                    "tag": "input",
                    "input_type": "text",
                    "value": self.value,
                    "options": [],
                    "rect": {},
                }
            ],
            "text": "Full name: " + self.value,
            "screenshot": None,
            "width": 800,
            "height": 600,
        }

    async def execute(self, target_id, observation, action, resolved_value=None):
        assert self.can_execute()
        assert observation["epoch"] == str(self.rev)
        self.executions.append(copy.deepcopy(action))
        self.value = resolved_value or self.value
        self.rev += 1
        return {"ok": True}


async def client_for(tmp_path):
    driver = FakeBrowser()
    app = create_app(tmp_path, browser=driver, pairing_code="test-code-123", testing=True)
    client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    response = await client.post("/api/v1/pair", json={"code": "test-code-123"})
    client.headers["Authorization"] = "Bearer " + response.json()["token"]
    await client.post("/api/v1/vault/initialize", json={"passphrase": "testing-long-passphrase"})
    # These executor/privacy workflows intentionally use the deterministic planner.
    response = await client.post("/api/v1/settings", json={"mode": "demo"})
    assert response.status_code == 200
    headers = dict(client.headers)
    await client.aclose()
    return (
        app,
        driver,
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver", headers=headers
        ),
    )


async def pending(client, task_id, kind=None):
    for _ in range(150):
        task = (await client.get("/api/v1/tasks/" + task_id)).json()
        if task.get("pending") and (kind is None or task["pending"]["kind"] == kind):
            return task
        if task.get("status") in ("failed", "blocked", "completed", "outcome_unknown"):
            return task
        await asyncio.sleep(0.01)
    raise AssertionError("Task did not reach an approval or terminal state")


async def approve(client, task):
    return await client.post(
        f"/api/v1/tasks/{task['id']}/approve", json={"approval_id": task["pending"]["id"], "approved": True}
    )


@pytest.mark.asyncio
async def test_real_values_only_reach_executor_and_next_observation_is_redacted(tmp_path):
    app, driver, client = await client_for(tmp_path)
    async with client:
        canary = "PRIVATE_CANARY_7319"
        await client.post(
            "/api/v1/records", json={"label": "Full name", "field_type": "person_name", "value": canary}
        )
        response = await client.post(
            "/api/v1/tasks", json={"goal": "Fill the form", "target_id": "exact-tab"}
        )
        assert response.status_code == 200, response.text
        task_id = response.json()["id"]
        task = await pending(client, task_id)
        assert task["pending"]["kind"] == "model"
        assert canary not in json.dumps(task)
        await approve(client, task)
        task = await pending(client, task_id, "disclosure")
        assert task["pending"]["kind"] == "disclosure"
        assert driver.value == ""
        await approve(client, task)
        task = await pending(client, task_id, "model")
        assert driver.value == canary
        assert canary not in json.dumps(task["request"])
        assert canary not in json.dumps(driver.executions)
        await approve(client, task)
        await asyncio.sleep(0.03)
        task = (await client.get("/api/v1/tasks/" + task_id)).json()
        assert task["status"] == "completed", task
        assert canary.encode() not in (tmp_path / "vault.sqlite3").read_bytes()


@pytest.mark.asyncio
async def test_edit_invalidates_private_reference_approval(tmp_path):
    app, driver, client = await client_for(tmp_path)
    async with client:
        record = (
            await client.post(
                "/api/v1/records",
                json={"label": "Full name", "field_type": "person_name", "value": "Old Private Person"},
            )
        ).json()
        task_id = (
            await client.post("/api/v1/tasks", json={"goal": "Fill form", "target_id": "exact-tab"})
        ).json()["id"]
        await approve(client, await pending(client, task_id))
        task = await pending(client, task_id, "disclosure")
        await client.post(
            "/api/v1/records",
            json={
                "label": "Full name",
                "field_type": "person_name",
                "value": "New Private Person",
                "record_id": record["id"],
            },
        )
        await approve(client, task)
        await asyncio.sleep(0.03)
        assert not driver.executions
        assert (await client.get("/api/v1/tasks/" + task_id)).json()["status"] == "failed"


@pytest.mark.asyncio
async def test_lock_revokes_pending_approval_and_does_not_replay_after_unlock(tmp_path):
    app, driver, client = await client_for(tmp_path)
    async with client:
        await client.post("/api/v1/demo/seed")
        task_id = (
            await client.post("/api/v1/tasks", json={"goal": "Fill form", "target_id": "exact-tab"})
        ).json()["id"]
        task = await pending(client, task_id)
        await client.post("/api/v1/vault/lock")
        assert not driver.executions
        assert (await client.get("/api/v1/tasks/" + task_id)).status_code == 423
        assert (await approve(client, task)).status_code == 423
        await client.post("/api/v1/vault/unlock", json={"passphrase": "testing-long-passphrase"})
        task = (await client.get("/api/v1/tasks/" + task_id)).json()
        assert task["status"] == "stopped"
        assert task["pending"] is None
        assert not driver.executions


@pytest.mark.asyncio
@pytest.mark.parametrize("shutdown_fails", [False, True])
async def test_lifespan_failure_closes_browser_and_locks_vault(tmp_path, shutdown_fails):
    app, driver, client = await client_for(tmp_path)
    shutdowns = []

    async def shutdown():
        shutdowns.append(True)
        if shutdown_fails:
            raise RuntimeError("Synthetic shutdown failure")

    driver.shutdown = shutdown
    async with client:
        with pytest.raises(RuntimeError):
            async with app.router.lifespan_context(app):
                assert app.state.vault.unlocked
                raise RuntimeError("Synthetic application failure")
        assert shutdowns == [True]
        assert not app.state.vault.unlocked


@pytest.mark.asyncio
async def test_pairing_origin_scope_and_unrelated_webpage_denied(tmp_path):
    app, driver, client = await client_for(tmp_path)
    async with client:
        response = await client.get("/api/v1/records", headers={"Origin": "https://evil.example"})
        assert response.status_code == 403
        extension_origin = "chrome-extension://" + "a" * 32
        response = await client.post(
            "/api/v1/pair", json={"code": "test-code-123"}, headers={"Origin": extension_origin}
        )
        token = response.json()["token"]
        headers = {"Origin": extension_origin, "Authorization": "Bearer " + token}
        assert (await client.get("/api/v1/records", headers=headers)).status_code == 403
        assert (await client.get("/api/v1/status", headers=headers)).status_code == 200
        assert (
            await client.get("/api/v1/status", headers={"Authorization": "Bearer " + token})
        ).status_code == 401


@pytest.mark.asyncio
async def test_document_review_scope_and_private_calculation(tmp_path):
    app, driver, client = await client_for(tmp_path)
    async with client:
        response = await client.post(
            "/api/v1/documents",
            files={"file": ("sample.txt", b"Full name: Synthetic Person\nAmount: 10.25\n", "text/plain")},
        )
        assert response.status_code == 200, response.text
        document_id = response.json()["document"]["id"]
        response = await client.post(
            f"/api/v1/documents/{document_id}/review",
            json={"candidates": [{"label": "Amount", "field_type": "money", "value": "10.25"}]},
        )
        first = response.json()["records"][0]
        assert first["scope"] == "document:" + document_id
        second = (
            await client.post(
                "/api/v1/records", json={"label": "Amount 2", "field_type": "money", "value": "0.10"}
            )
        ).json()
        response = await client.post(
            "/api/v1/calculations/sum", json={"record_ids": [first["id"], second["id"]], "currency": "INR"}
        )
        assert response.json()["record"]["value"] == "10.35"
        assert response.json()["record"]["scope"] == "calculation"
        assert (
            await client.post("/api/v1/calculations/sum", json={"record_ids": [first["id"], first["id"]]})
        ).status_code == 400


@pytest.mark.asyncio
async def test_gateway_prepared_json_and_approval_integrity():
    gateway = ModelGateway()
    private = ["Secret Example Person"]
    raw = {
        "title": "Name: Secret Example Person",
        "url": "https://example.test/?email=secret@example.test",
        "text": "Name: Secret Example Person",
        "fields": [],
        "width": 800,
        "height": 600,
    }
    obs = sanitize_observation(raw, private)
    payload = gateway.prepare("Fill", obs, [], [], private)
    parsed = json.loads(payload["messages"][1]["content"][0]["text"])
    assert parsed["page"]["text"].endswith("[REDACTED]")
    stamp = digest(payload)
    payload["model"] = "altered"
    with pytest.raises(ValueError, match="changed after approval"):
        await gateway.call(payload, stamp, private, "demo")


def test_model_cannot_supply_literal_secret_or_trusted_approval():
    with pytest.raises(ValueError):
        ProposedAction.model_validate(
            {"action": "input_ref", "element_index": 1, "value_ref": "ref_1", "value": "secret"}
        )
    with pytest.raises(ValueError):
        ProposedAction.model_validate({"action": "click", "element_index": 1, "_approved": True})

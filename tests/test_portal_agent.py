"""The shipped portal, real APIs/Chromium/Browser Use, synthetic model transport."""

import asyncio
import json
import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from privacy_guard.agent_runtime import quiet_browser_use
from privacy_guard.api import create_app
from privacy_guard.browser import BrowserDriver
from privacy_guard.config import DATA_DIR, ROOT
from privacy_guard.models import Candidate


class QuietFiles(SimpleHTTPRequestHandler):
    def log_message(self, *_args):
        pass


@pytest.mark.skipif(
    os.environ.get("GUARD_BROWSER_TESTS") != "1",
    reason="Set GUARD_BROWSER_TESTS=1 for the shipped portal workflow",
)
async def test_shipped_portal_api_document_image_review_and_resume(tmp_path, monkeypatch):
    quiet_browser_use()
    import browser_use  # noqa: F401 -- load HTTP annotations before mocking its factory

    finder = BrowserDriver(DATA_DIR, ROOT / "apps/extension", headless=True)
    monkeypatch.setenv("GUARD_BROWSER_EXECUTABLE", str(finder._browser_executable()))
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietFiles, directory=str(ROOT / "demo")))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    driver = BrowserDriver(tmp_path / "browser", ROOT / "apps/extension", headless=True)
    app = create_app(tmp_path / "app", browser=driver, pairing_code="portal-test-pair", testing=True)
    manager = app.state.manager
    manager.demo_port = server.server_port
    native_client = httpx.AsyncClient
    client = native_client(transport=httpx.ASGITransport(app=app), base_url="http://testserver")
    requests, actions = [], []
    expected = {
        "full_name": ("person_name", "Aarav Example"),
        "email": ("email", "aarav@example.test"),
        "phone": ("phone", "9000012345"),
        "pan": ("pan", "ABCDE1234F"),
        "address": ("address", "42 Sample Lane, Demo City"),
        "statement_total": ("amount", "3050.00"),
    }

    def fake_hosted_model(request):
        """Use native Browser Use indices, never dispatch page actions directly."""
        payload = json.loads(request.content)
        requests.append(payload)
        assert all(value not in json.dumps(payload) for _kind, value in expected.values())
        runtime = next(iter(manager.tasks.values()))["_runtime"]
        if any(isinstance(message["content"], list) for message in payload["messages"]):
            assert runtime.task["pending"] is None
            output = {
                "summary": "Contact details are filled. The identity, address, and statement total still need reviewed information.",
                "missing_fields": ["PAN", "Address", "Statement total"],
                "document_requests": ["Fictional application statement"],
            }
        else:
            raw = runtime.raw
            controls = {field["id"]: field["index"] for field in raw["fields"] if field.get("id")}
            fields = {field.get("name"): field for field in raw["fields"] if field.get("name")}

            def reveal_or_click(identifier, label):
                field = next(field for field in raw["fields"] if label in field["label"])
                rect = field["rect"]
                if identifier not in controls or rect["y"] < 0 or rect["y"] + rect["height"] > raw["height"]:
                    return {"scroll": {"down": rect["y"] >= 0, "pages": 0.7}}
                return {"click": {"index": controls[identifier]}}

            if "Review before submission" in raw["title"]:
                action = {
                    "done": {
                        "text": "The application is ready for your final review; nothing was submitted.",
                        "success": True,
                    }
                }
            elif not fields:
                action = reveal_or_click("start-application", "Start application")
            else:
                catalog = manager.catalog(runtime.task)
                action = None
                for name, (kind, _value) in expected.items():
                    field = fields.get(name)
                    if not field or field["value"]:
                        continue
                    ref = next(
                        (
                            ref
                            for ref in catalog
                            if ref["type"] == kind or (name == "statement_total" and ref["type"] == "money")
                        ),
                        None,
                    )
                    if not ref:
                        continue
                    if name not in controls:
                        action = {"scroll": {"down": field["rect"]["y"] >= 0, "pages": 0.7}}
                    else:
                        action = {"input_ref": {"index": controls[name], "value_ref": ref["id"]}}
                    break
                if not action:
                    if any(not field["value"] for field in fields.values()):
                        action = {
                            "request_information": {
                                "message": "Please upload the fictional statement to provide PAN, address and statement total."
                            }
                        }
                    else:
                        action = reveal_or_click("review-application", "Review application")
            actions.append(action)
            output = {
                "evaluation_previous_goal": "Inspected current page",
                "memory": "Continue this synthetic application",
                "next_goal": "Complete the next supported step",
                "action": [action],
            }
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(output)}}]})

    monkeypatch.setattr(
        "privacy_guard.agent_llm.httpx.AsyncClient",
        lambda **kwargs: native_client(transport=httpx.MockTransport(fake_hosted_model), **kwargs),
    )

    async def tick_until(task_id, wanted):
        for _ in range(600):
            response = await client.get(f"/api/v1/tasks/{task_id}")
            assert response.status_code == 200, response.text
            task = response.json()
            pending = task.get("pending")
            if pending:
                assert pending["kind"] in ("image", "submit")
                approval_id = pending["id"]
                if pending["kind"] == "image":
                    preview = await client.get(f"/api/v1/tasks/{task_id}/image-preview")
                    assert preview.status_code == 200, preview.text
                    assert preview.json()["original"] != preview.json()["redacted"]
                    assert preview.json()["report"]["automatic_masks"] >= 3
                    sent_before = len(requests)
                    revised = await client.post(
                        f"/api/v1/tasks/{task_id}/masks",
                        json={
                            "approval_id": approval_id,
                            "masks": [{"x": 0, "y": 0, "width": 20, "height": 20}],
                        },
                    )
                    assert revised.status_code == 200, revised.text
                    assert len(requests) == sent_before
                    expired = await client.post(
                        f"/api/v1/tasks/{task_id}/approve",
                        json={"approval_id": approval_id, "approved": True},
                    )
                    assert expired.status_code == 400
                    approval_id = revised.json()["approval_id"]
                else:
                    assert "Submit demo application" not in pending["payload"].get("control", "")
                approved = await client.post(
                    f"/api/v1/tasks/{task_id}/approve", json={"approval_id": approval_id, "approved": True}
                )
                assert approved.status_code == 200, approved.text
            if task["status"] == wanted:
                return task
            if task["status"] in ("failed", "blocked", "outcome_unknown", "completed"):
                pytest.fail(
                    str({key: task.get(key) for key in ("status", "error", "result", "events", "step")})
                )
            await asyncio.sleep(0.1)
        pytest.fail("The shipped portal did not reach the expected state")

    try:
        pair = await client.post("/api/v1/pair", json={"code": "portal-test-pair"})
        assert pair.status_code == 200, pair.text
        client.headers["Authorization"] = "Bearer " + pair.json()["token"]
        initialized = await client.post(
            "/api/v1/vault/initialize", json={"passphrase": "synthetic-long-passphrase"}
        )
        assert initialized.status_code == 200, initialized.text
        configured = await client.post(
            "/api/v1/settings",
            json={"mode": "remote", "model": "test-model", "api_key": "sk-synthetic-http-peer"},
        )
        assert configured.status_code == 200, configured.text
        seed = await client.post("/api/v1/demo/seed?partial=true")
        assert seed.status_code == 200 and len(seed.json()["suggested_record_ids"]) == 3
        selected = seed.json()["suggested_record_ids"]
        started = await client.post(
            "/api/v1/tasks",
            json={
                "goal": "Open the portal, complete the application using my profile, request missing documents, and reach review before submitting.",
                "start_url": f"http://127.0.0.1:{server.server_port}/portal.html",
                "mode": "remote",
                "vision": True,
                "record_ids": selected,
            },
        )
        assert started.status_code == 200, started.text
        task_id = started.json()["id"]
        paused = await tick_until(task_id, "waiting_input")
        assert paused["metrics"]["image_calls"] == 1
        uploaded = await client.post(
            "/api/v1/documents",
            files={
                "file": (
                    "portal-statement.txt",
                    (ROOT / "demo/documents/portal-statement.txt").read_bytes(),
                    "text/plain",
                )
            },
        )
        assert uploaded.status_code == 200, uploaded.text
        document_id = uploaded.json()["document"]["id"]
        candidates = [
            {key: value for key, value in candidate.items() if key in Candidate.model_fields}
            for candidate in uploaded.json()["candidates"]
            if candidate["field_type"] in ("pan", "address", "amount", "money")
        ]
        assert len(candidates) == 3, uploaded.json()
        confirmed = await client.post(
            f"/api/v1/documents/{document_id}/review", json={"candidates": candidates}
        )
        assert confirmed.status_code == 200, confirmed.text
        reviewed = confirmed.json()["records"]
        assert all(record["scope"] == "document:" + document_id for record in reviewed)
        resumed = await client.post(
            f"/api/v1/tasks/{task_id}/control",
            json={"action": "resume", "record_ids": selected + [record["id"] for record in reviewed]},
        )
        assert resumed.status_code == 200, resumed.text
        completed = await tick_until(task_id, "completed")
        assert completed["step"] <= 20
        # Final assertions use DOM values only. Do not take an unrelated image
        # after the agent has finished: compositor/viewport updates can invalidate
        # an image pair even though the review page itself is correct.
        raw = await driver.observe(completed["target_id"], include_screenshot=False)
        assert "Review before submission" in raw["title"]
        assert all(value in raw["text"] for _kind, value in expected.values())
        assert "SYNTHETIC CONFIRMATION" not in raw["text"]
        assert len([action for action in actions if "input_ref" in action]) == 6
        assert completed["metrics"]["image_calls"] == 1
        assert completed["metrics"]["model_calls"] == len(requests)
    finally:
        await manager.stop_all()
        await client.aclose()
        await driver.shutdown()
        server.shutdown()

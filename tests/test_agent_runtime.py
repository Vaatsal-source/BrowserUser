"""Real Browser Use integration with synthetic pages and a deterministic HTTP peer."""

import asyncio
import base64
import io
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from privacy_guard.agent_llm import clean_strings
from privacy_guard.agent_runtime import BrowserAgentRuntime, ReferenceInput, build_agent, quiet_browser_use
from privacy_guard.browser import BrowserDriver
from privacy_guard.config import DATA_DIR
from privacy_guard.gateway import ModelGateway, digest
from privacy_guard.models import TaskRequest
from privacy_guard.screenshots import SanitizedImageStore
from privacy_guard.tasks import TaskManager
from privacy_guard.vault import Vault


def runtime_stub(tmp_path):
    vault = Vault(tmp_path)
    vault.initialize("synthetic-long-passphrase")
    gateway = ModelGateway()
    gateway.model, gateway.api_key = "test-model", "sk-test-no-network-key"
    gateway.image_store = SanitizedImageStore()
    manager = TaskManager(vault, SimpleNamespace(), gateway)
    task = {
        "_generation": 0,
        "_goal": "Fill the form",
        "_origin": "https://example.test",
        "_ref_ids": {},
        "_refs": {},
    }
    return BrowserAgentRuntime(manager, task)


def test_agent_transport_drops_native_images_and_preserves_json(tmp_path):
    runtime = runtime_stub(tmp_path)
    secret = "PRIVATE_CANARY_921714"
    image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,RAW_UNREVIEWED"}}
    payload = runtime.llm.prepare(
        [
            {"role": "user", "content": [{"type": "text", "text": "Name: " + secret}, image]},
            {"role": "assistant", "content": "Echo " + secret},
        ],
        ReferenceInput,
        [secret],
    )
    encoded = json.dumps(payload)
    assert secret not in encoded and "RAW_UNREVIEWED" not in encoded
    assert payload["response_format"] == {"type": "json_object"}
    cleaned = clean_strings({"nested": {"message": "Name: " + secret}}, [secret])
    assert isinstance(cleaned["nested"], dict)
    assert "message" in cleaned["nested"]
    with_url = runtime.llm.prepare(
        [
            {
                "role": "user",
                "content": "Visit https://example.test/form?session=UNKNOWN_TOKEN#PRIVATE_FRAGMENT",
            }
        ],
        ReferenceInput,
        [],
    )
    assert "UNKNOWN_TOKEN" not in json.dumps(with_url) and "PRIVATE_FRAGMENT" not in json.dumps(with_url)


async def test_model_endpoint_changes_and_late_masks_cannot_reuse_approval(tmp_path):
    runtime = runtime_stub(tmp_path)
    payload = runtime.llm.prepare([{"role": "user", "content": "Return JSON"}], ReferenceInput, [])
    runtime.manager.gateway.base_url = "https://different.example/v1"
    with pytest.raises(ValueError, match="settings changed"):
        await runtime.llm.send(payload, digest(payload), [])
    future = asyncio.get_running_loop().create_future()
    future.set_result(True)
    runtime.task["pending"] = {"id": "accepted-id", "kind": "image", "expires_at": 10**12}
    runtime.manager.approvals["accepted-id"] = future
    runtime.image_state = {"artifact": {"id": "immutable-original"}}
    with pytest.raises(ValueError, match="no longer valid"):
        runtime.update_masks("accepted-id", [{"x": 0, "y": 0, "width": 10, "height": 10}])
    assert runtime.image_state["artifact"]["id"] == "immutable-original"


@pytest.mark.parametrize(
    "encoded",
    [r'Mira \"Sen\"', r"Mira \u0022Sen\u0022", "Mira%20%22Sen%22", "Mira &quot;Sen&quot;"],
)
async def test_send_rechecks_newly_reviewed_private_values_before_http(tmp_path, monkeypatch, encoded):
    runtime = runtime_stub(tmp_path)
    payload = runtime.llm.prepare(
        [{"role": "user", "content": "Previous page text: " + encoded}], ReferenceInput, []
    )
    approved_hash = digest(payload)
    # A document review can add a value while a screenshot/text approval is open.
    # The immutable payload must then be rejected rather than sent or rewritten
    # under the original approval receipt.
    runtime.manager.vault.put_record("Full name", "person_name", 'Mira "Sen"')

    def no_transport(**_kwargs):
        pytest.fail("A fresh private value crossed the HTTP boundary")

    monkeypatch.setattr("privacy_guard.agent_llm.httpx.AsyncClient", no_transport)
    with pytest.raises(ValueError, match="known private value"):
        await runtime.llm.send(payload, approved_hash, [])


def test_mixed_artifact_cannot_send_original_pixels(tmp_path):
    runtime = runtime_stub(tmp_path)
    pixels = io.BytesIO()
    Image.new("RGB", (32, 32), "white").save(pixels, format="PNG")
    raw = base64.b64encode(pixels.getvalue()).decode()
    artifact = runtime.manager.gateway.image_store.create(
        raw,
        {
            "complete": True,
            "viewport": {"width": 32, "height": 32},
            "regions": [{"x": 0, "y": 0, "width": 10, "height": 10, "reason": "known_value"}],
        },
    )
    artifact["data_url"] = "data:image/png;base64," + raw
    with pytest.raises(ValueError, match="artifact was altered"):
        runtime.llm.prepare([{"role": "user", "content": "Return JSON"}], ReferenceInput, [], artifact)


def test_native_agent_has_only_guarded_tools_and_no_persistence(tmp_path, monkeypatch):
    quiet_browser_use()
    import browser_use.browser.profile as profile

    monkeypatch.setattr(profile, "get_display_size", lambda: None)
    from browser_use import BrowserSession

    runtime = runtime_stub(tmp_path)
    session = BrowserSession(cdp_url="http://127.0.0.1:9222", use_cloud=False, captcha_solver=False)
    agent = build_agent(runtime, session)
    assert agent.file_system is None
    assert not agent.agent_directory.exists()
    assert not agent.settings.use_vision and not agent.settings.use_judge
    assert not agent.settings.message_compaction.enabled
    assert not agent.enable_signal_handler
    assert agent.settings.page_extraction_llm is runtime.llm
    assert set(agent.tools.registry.registry.actions) == {
        "navigate",
        "click",
        "input_ref",
        "search_text",
        "select_option",
        "scroll",
        "wait",
        "visual_checkpoint",
        "request_information",
        "done",
    }
    agent.tools.set_coordinate_clicking(True)
    assert "coordinate_x" not in agent.tools.registry.registry.actions["click"].param_model.model_fields


class _Portal(BaseHTTPRequestHandler):
    def do_GET(self):
        page = (
            '<!doctype html><html><body><h1>Application home</h1><a href="/form">Open application</a></body></html>'
            if self.path != "/form"
            else """<!doctype html><html><body><h1>Application form</h1>
                <label>Full name<input id="full_name" required></label>
                <label>Email<input id="email" type="email" required></label>
                <p>Please supply your contact document if email is missing.</p>
                <button type="submit">Submit application</button></body></html>"""
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(page.encode())

    def log_message(self, *_args):
        pass


@pytest.mark.skipif(
    os.environ.get("GUARD_BROWSER_TESTS") != "1", reason="Set GUARD_BROWSER_TESTS=1 for Chromium integration"
)
async def test_stock_agent_navigation_ref_fill_image_review_missing_resume(tmp_path, monkeypatch):
    # Load native HTTP type annotations before replacing the transport factory.
    quiet_browser_use()
    import browser_use  # noqa: F401

    finder = BrowserDriver(DATA_DIR, tmp_path, headless=True)
    monkeypatch.setenv("GUARD_BROWSER_EXECUTABLE", str(finder._browser_executable()))
    extension = tmp_path / "extension"
    extension.mkdir()
    (extension / "manifest.json").write_text(
        json.dumps(
            {
                "manifest_version": 3,
                "name": "Guard integration",
                "version": "0.0.1",
                "permissions": ["activeTab"],
                "action": {},
            }
        )
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Portal)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    driver = BrowserDriver(tmp_path / "browser", extension, headless=True)
    vault = Vault(tmp_path / "vault")
    vault.initialize("synthetic-long-passphrase")
    canary = "SYNTHETIC_PRIVATE_PERSON_194825"
    name_record = vault.put_record("Full name", "person_name", canary)
    gateway = ModelGateway()
    gateway.model, gateway.api_key = "test-model", "sk-test-no-network-key"
    gateway.image_store = SanitizedImageStore()
    manager = TaskManager(vault, driver, gateway, demo_port=server.server_port)
    origin = f"http://127.0.0.1:{server.server_port}"
    sent, stages = [], {"navigated": False, "asked": False}
    original_client = httpx.AsyncClient

    def respond(request):
        payload = json.loads(request.content)
        sent.append(payload)
        assert canary not in json.dumps(payload)
        runtime = next(iter(manager.tasks.values()))["_runtime"]
        if any(isinstance(message["content"], list) for message in payload["messages"]):
            assert runtime.task.get("pending") is None
            response = {
                "summary": "The email is missing. Add a reviewed contact record.",
                "missing_fields": ["Email"],
                "document_requests": ["Contact document"],
            }
        else:
            schema = runtime.agent.AgentOutput.model_json_schema()
            catalog = manager.catalog(runtime.task)
            name_ref = next(ref["id"] for ref in catalog if ref["type"] == "person_name")
            if not stages["navigated"]:
                action = {"navigate": {"url": origin + "/form", "new_tab": False}}
                stages["navigated"] = True
            else:
                name_index = next(f["index"] for f in runtime.raw["fields"] if f.get("id") == "full_name")
                email_index = next(f["index"] for f in runtime.raw["fields"] if f.get("id") == "email")
                local_fields = runtime.raw["fields"]
                name_field = next(
                    field
                    for field in local_fields
                    if field.get("name") == "" and field["label"] == "Full name"
                )
                email_field = next(field for field in local_fields if field["label"] == "Email")
                if not name_field["value"]:
                    action = {"input_ref": {"index": name_index, "value_ref": name_ref}}
                elif not stages["asked"]:
                    stages["asked"] = True
                    action = {
                        "request_information": {
                            "message": "Please provide the missing email or contact document."
                        }
                    }
                elif not email_field["value"]:
                    email_ref = next(ref["id"] for ref in catalog if ref["type"] == "email")
                    action = {"input_ref": {"index": email_index, "value_ref": email_ref}}
                else:
                    action = {
                        "done": {
                            "text": "The fields are filled and ready for review. Submission was withheld.",
                            "success": True,
                        }
                    }
            response = {
                "evaluation_previous_goal": "Checked the page",
                "memory": "Synthetic test",
                "next_goal": "Continue the requested form",
                "action": [action],
            }
            # Additional planning fields are optional in the pinned schema.
            assert "action" in schema["properties"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(response)}}]})

    monkeypatch.setattr(
        "privacy_guard.agent_llm.httpx.AsyncClient",
        lambda **kwargs: original_client(transport=httpx.MockTransport(respond), **kwargs),
    )
    try:
        await driver.launch()
        request = TaskRequest(
            goal="Open this website and fill the application",
            start_url=origin,
            mode="remote",
            vision=True,
            record_ids=[name_record["id"]],
        )
        task = await manager.start(request)
        task_id = task["id"]
        for _ in range(600):
            task = manager.tasks[task_id]
            if task.get("pending"):
                assert task["pending"]["kind"] == "image", manager.public(task)
                before = manager.image_preview(task_id)
                outgoing_before = len(sent)
                old_id = before["approval_id"]
                after = manager.update_masks(task_id, old_id, [{"x": 0, "y": 0, "width": 12, "height": 12}])
                assert after["approval_id"] != old_id and len(sent) == outgoing_before
                with pytest.raises(ValueError, match="no longer valid"):
                    manager.approve(task_id, old_id, True)
                assert after["original"] != after["redacted"]
                picture = Image.open(io.BytesIO(base64.b64decode(after["redacted"].split(",", 1)[1])))
                assert any(hi > 0 for _lo, hi in picture.convert("RGB").getextrema())
                manager.approve(task_id, after["approval_id"], True)
            if task["status"] == "waiting_input":
                break
            if task["status"] in ("failed", "blocked", "outcome_unknown", "completed"):
                pytest.fail(str(manager.public(task)))
            await asyncio.sleep(0.1)
        assert task["status"] == "waiting_input", manager.public(task)
        assert len(sent) == 4
        assert task["metrics"]["image_calls"] == 1
        raw = await driver.observe(task["target_id"])
        assert raw["fields"][0]["value"] == canary
        email = vault.put_record("Email", "email", "private-person@example.test")
        await manager.control(task_id, "resume", [name_record["id"], email["id"]])
        await asyncio.wait_for(manager.workers[task_id], timeout=45)
        assert task["status"] == "completed", manager.public(task)
        raw = await driver.observe(task["target_id"])
        assert raw["fields"][1]["value"] == "private-person@example.test"
        assert len(sent) == 6
        assert all("private-person@example.test" not in json.dumps(payload) for payload in sent)
        assert not task["_runtime"].agent.agent_directory.exists()
    finally:
        await manager.stop_all()
        await driver.shutdown()
        server.shutdown()


def test_native_frame_text_is_replaced_with_verified_local_controls(tmp_path):
    runtime = runtime_stub(tmp_path)
    runtime.raw = {
        "url": "https://example.test/form", "title": "Form", "text": "Full name",
        "width": 800, "height": 600, "unsupported_frames": 1,
        "fields": [{"index": 7, "label": "Full name", "tag": "input", "input_type": "text", "value": "LOCAL_SECRET_CANARY"}],
    }
    payload = runtime.llm.prepare([
        {"role": "user", "content": "native metadata <browser_state>UNINSPECTED_FRAME_SECRET</browser_state>"},
    ], ReferenceInput, runtime.private())
    outgoing = json.dumps(payload)
    assert "UNINSPECTED_FRAME_SECRET" not in outgoing
    assert "LOCAL_SECRET_CANARY" not in outgoing
    assert 'Full name' in outgoing and 'unavailable_frames' in outgoing


async def test_destination_change_needs_approval_and_blocks_local_services(tmp_path):
    runtime = runtime_stub(tmp_path)
    approvals = []

    async def approve(*args):
        approvals.append(args)

    runtime.manager.approval = approve
    runtime.check = lambda: None
    await runtime.authorize_destination("https://another.example/form")
    await runtime.authorize_destination("https://another.example/next")
    assert len(approvals) == 1
    with pytest.raises(PermissionError, match="local service"):
        await runtime.authorize_destination("http://127.0.0.1:8765")
    assert len(approvals) == 1


async def test_public_search_tool_cannot_type_arbitrary_or_private_values(tmp_path, monkeypatch):
    from privacy_guard.agent_runtime import SearchInput

    quiet_browser_use()
    import browser_use.browser.profile as profile
    from browser_use import BrowserSession

    monkeypatch.setattr(profile, "get_display_size", lambda: None)
    runtime = runtime_stub(tmp_path)
    runtime.task.update(_goal="Search for wireless headphones", target_id="owned")
    runtime.raw = {"fields": [{"index": 1, "tag": "input", "input_type": "search", "label": "Search"}]}

    async def check_target():
        pass

    calls = []

    async def execute(*args, **kwargs):
        calls.append((args, kwargs))
        return {"ok": True}

    runtime.check_target = check_target
    runtime.manager.browser = SimpleNamespace(execute=execute)
    session = BrowserSession(cdp_url="http://127.0.0.1:9222", use_cloud=False, captcha_solver=False)
    agent = build_agent(runtime, session)
    search = agent.tools.registry.registry.actions["search_text"].function
    assert (await search(params=SearchInput(index=1, text="unrequested query"))).error
    assert not calls
    assert not (await search(params=SearchInput(index=1, text="wireless headphones"))).error
    assert calls[0][1]["resolved_value"] == "wireless headphones"
    runtime.manager.vault.put_record("Private phrase", "text", "wireless headphones")
    assert (await search(params=SearchInput(index=1, text="wireless headphones"))).error
    assert len(calls) == 1


@pytest.mark.parametrize("code", ["option_not_found", "option_ambiguous", "option_disabled"])
async def test_dropdown_rejection_is_recoverable_before_mutation(tmp_path, code):
    from privacy_guard.browser import BrowserError

    runtime = runtime_stub(tmp_path)
    runtime.task.update(id="test-task", step=1, status="observing", events=[], target_id="tab")
    runtime.raw = {"url": "https://example.test/form"}

    async def field(index):
        return {"index": index, "tag": "select", "options": [{"label": "INDIVIDUAL"}]}

    async def approve(*args):
        pass

    async def execute(*args, **kwargs):
        raise BrowserError(code)

    runtime.field = field
    runtime.manager.approval = approve
    runtime.manager.browser.execute = execute
    result = await runtime.execute_element("select_option", 1, option_index=0)
    assert result.error and "No option was selected" in result.error
    assert runtime.task["status"] == "observing"
    assert runtime.fatal is None


async def test_dropdown_selects_observed_index_after_review(tmp_path):
    runtime = runtime_stub(tmp_path)
    runtime.task.update(id="test-task", step=1, status="observing", events=[], target_id="tab")
    runtime.raw = {"url": "https://example.test/form"}
    approvals, actions = [], []

    async def field(index):
        return {"index": index, "tag": "select", "label": "Application type", "options": [
            {"label": "Citizen", "value": "49A"}, {"label": "Entity", "value": "49A"},
        ]}

    async def approve(*args):
        approvals.append(args[-1])

    async def execute(target, raw, action, resolved_value=None):
        assert approvals and approvals[0]["option"] == "Entity"
        assert "49A" not in json.dumps(approvals)
        assert resolved_value is None
        actions.append(action)
        return {"ok": True}

    runtime.field = field
    runtime.manager.approval = approve
    runtime.manager.browser.execute = execute
    await runtime.execute_element("select_option", 1, option_index=1)
    assert actions == [{"action": "select_option", "element_index": 1, "option_index": 1, "_approved": True}]
    assert runtime.task["status"] == "observing"


async def test_selection_changed_by_page_remains_uncertain(tmp_path):
    from privacy_guard.browser import BrowserError

    runtime = runtime_stub(tmp_path)
    runtime.task.update(id="test-task", step=1, status="observing", events=[], target_id="tab")
    runtime.raw = {"url": "https://example.test/form"}

    async def field(index):
        return {"index": index, "tag": "select", "options": [{"label": "Individual"}]}

    async def approve(*args):
        pass

    async def execute(*args, **kwargs):
        raise BrowserError("selection_not_accepted")

    runtime.field = field
    runtime.manager.approval = approve
    runtime.manager.browser.execute = execute
    with pytest.raises(BrowserError, match="selection_not_accepted"):
        await runtime.execute_element("select_option", 1, option_index=0)
    assert runtime.task["status"] == "executing"
    assert runtime.fatal is not None

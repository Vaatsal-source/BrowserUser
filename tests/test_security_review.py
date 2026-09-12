"""Independent integration regressions for privacy boundaries and local roles."""

import base64
import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from PIL import Image, PngImagePlugin

from privacy_guard.api import create_app
from privacy_guard.documents import extract_document
from privacy_guard.gateway import ModelGateway
from privacy_guard.privacy import sanitize_observation, sanitize_text
from privacy_guard.tasks import TaskManager
from privacy_guard.vault import Vault


class LocalBrowserStub:
    def status(self):
        return {"connected": False}

    def invalidate(self):
        pass

    async def shutdown(self):
        pass


@pytest.fixture
def local_api(tmp_path):
    app = create_app(
        tmp_path / "app-data", browser=LocalBrowserStub(), pairing_code="local-pairing-code", testing=True
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/pair", headers={"Origin": "http://testserver"}, json={"code": "local-pairing-code"}
        )
        token = response.json()["token"]
        headers = {"Origin": "http://testserver", "Authorization": "Bearer " + token}
        response = client.post(
            "/api/v1/vault/initialize", headers=headers, json={"passphrase": "correct horse battery staple"}
        )
        assert response.status_code == 200
        yield app, client, headers


def outgoing_request():
    observation = {
        "title": "Form",
        "url": "https://example.test/form",
        "text": "Public instructions",
        "fields": [],
    }
    return ModelGateway().prepare("Fill the form", observation, [], [], [])


def test_gateway_sanitizes_string_leaves_without_destroying_json():
    observation = {
        "title": "Form",
        "url": "https://example.test/form",
        "text": "Name: Aria Example",
        "fields": [],
    }
    gateway = ModelGateway()
    request = gateway.prepare("Fill the form", observation, [], [], ["Aria Example"])
    context = json.loads(request["messages"][1]["content"][0]["text"])
    assert context["page"]["text"] == "Name: [REDACTED]"
    assert context["references"] == [] and context["history"] == []


def test_gateway_rejects_private_data_in_unexpected_message_envelope_fields():
    payload = outgoing_request()
    payload["messages"][1]["name"] = "ENVELOPE-PRIVATE-CANARY"
    with pytest.raises(ValueError):
        ModelGateway.check(payload, ["ENVELOPE-PRIVATE-CANARY"])


def test_gateway_rejects_private_png_metadata_even_when_all_pixels_are_black():
    image = Image.new("RGB", (3, 3), "black")
    metadata = PngImagePlugin.PngInfo()
    metadata.add_text("private_note", "IMAGE-METADATA-PRIVATE-CANARY")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", pnginfo=metadata)
    payload = outgoing_request()
    payload["messages"][1]["content"].append(
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()},
        }
    )
    with pytest.raises(ValueError):
        ModelGateway.check(payload, ["IMAGE-METADATA-PRIVATE-CANARY"])


def test_gateway_rejects_private_trailing_bytes_in_an_otherwise_valid_black_png():
    image = Image.new("RGB", (3, 3), "black")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    binary = buffer.getvalue() + b"TRAILING-PRIVATE-PNG-CANARY"
    payload = outgoing_request()
    payload["messages"][1]["content"].append(
        {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64," + base64.b64encode(binary).decode()},
        }
    )
    with pytest.raises(ValueError):
        ModelGateway.check(payload, ["TRAILING-PRIVATE-PNG-CANARY"])


def test_unrelated_page_origin_cannot_pair_even_with_the_correct_code(local_api):
    _, client, _ = local_api
    response = client.post(
        "/api/v1/pair", headers={"Origin": "https://unrelated.example"}, json={"code": "local-pairing-code"}
    )
    assert response.status_code == 403


def test_extension_session_cannot_manage_private_vault_or_settings(local_api):
    _, client, _ = local_api
    source = "chrome-extension://" + "a" * 32
    token = client.post(
        "/api/v1/pair", headers={"Origin": source}, json={"code": "local-pairing-code"}
    ).json()["token"]
    headers = {"Origin": source, "Authorization": "Bearer " + token}
    for method, path, body in [
        ("GET", "/api/v1/records", None),
        ("GET", "/api/v1/documents", None),
        ("POST", "/api/v1/vault/lock", None),
        ("POST", "/api/v1/settings", {}),
    ]:
        assert client.request(method, path, headers=headers, json=body).status_code == 403
    assert client.get("/api/v1/status", headers=headers).status_code == 200


def test_session_token_cannot_be_reused_from_another_extension_origin(local_api):
    _, client, _ = local_api
    source = "chrome-extension://" + "a" * 32
    token = client.post(
        "/api/v1/pair", headers={"Origin": source}, json={"code": "local-pairing-code"}
    ).json()["token"]
    response = client.get(
        "/api/v1/status",
        headers={"Origin": "chrome-extension://" + "b" * 32, "Authorization": "Bearer " + token},
    )
    assert response.status_code == 401


def test_locked_vault_cannot_export_task_context_through_control_endpoint(local_api):
    app, client, headers = local_api
    app.state.manager.tasks["old-task"] = {
        "id": "old-task",
        "status": "paused",
        "events": [],
        "pending": None,
        "goal": "Previously private task context",
        "request": {"context": "prior-reviewed-page"},
    }
    assert client.post("/api/v1/vault/lock", headers=headers).status_code == 200
    assert client.get("/api/v1/tasks/old-task", headers=headers).status_code == 423
    response = client.post("/api/v1/tasks/old-task/control", headers=headers, json={"action": "pause"})
    # Clearing task memory during lock is also a valid way to prevent this export.
    assert response.status_code in {401, 403, 404, 423}
    assert "prior-reviewed-page" not in response.text


def test_document_candidates_default_to_document_scope_instead_of_profile(local_api):
    _, client, headers = local_api
    uploaded = client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("statement.txt", b"Total: 250.00", "text/plain")},
    )
    assert uploaded.status_code == 200
    document_id = uploaded.json()["document"]["id"]
    reviewed = client.post(
        f"/api/v1/documents/{document_id}/review",
        headers=headers,
        json={
            "candidates": [
                {"label": "Statement total", "field_type": "money", "value": "250.00", "selected": True}
            ]
        },
    )
    assert reviewed.status_code == 200
    assert reviewed.json()["records"][0]["scope"] == "document:" + document_id


@pytest.fixture
def local_manager(tmp_path):
    vault = Vault(tmp_path / "manager-vault")
    vault.initialize("correct horse battery staple")
    return TaskManager(vault, LocalBrowserStub(), ModelGateway())


def test_default_reference_catalog_excludes_document_and_unreviewed_records(local_manager):
    profile = local_manager.vault.put_record("Full name", "person_name", "Aria Example")
    local_manager.vault.put_record(
        "Other person's name", "person_name", "Another Person", scope="document:other"
    )
    local_manager.vault.put_record("Unconfirmed phone", "phone", "9000011111", reviewed=False)
    task = {"_record_ids": None, "_ref_ids": {}, "_refs": {}}
    catalog = local_manager.catalog(task)
    assert len(catalog) == 1
    assert local_manager.resolve(task, catalog[0]["id"])["id"] == profile["id"]


def test_reference_from_another_task_and_old_record_version_are_rejected(local_manager):
    record = local_manager.vault.put_record("Full name", "person_name", "Aria Example")
    task_a = {"_record_ids": None, "_ref_ids": {}, "_refs": {}}
    task_b = {"_record_ids": None, "_ref_ids": {}, "_refs": {}}
    ref = local_manager.catalog(task_a)[0]["id"]
    local_manager.catalog(task_b)
    with pytest.raises(ValueError, match="out-of-task"):
        local_manager.resolve(task_b, ref)
    local_manager.vault.put_record("Full name", "person_name", "Changed Person", record_id=record["id"])
    with pytest.raises(ValueError, match="changed"):
        local_manager.resolve(task_a, ref)


async def test_permission_errors_cannot_publish_private_browser_values(local_manager):
    secret = "PRIVATE-PERMISSION-ERROR-CANARY"
    local_manager.vault.put_record("Private canary", "text", secret)

    async def observe(_):
        raise PermissionError("Browser access denied for " + secret)

    local_manager.browser.observe = observe
    task = {
        "id": "error-task",
        "step": 0,
        "status": "created",
        "target_id": "test-target",
        "events": [],
        "pending": None,
    }
    local_manager.tasks[task["id"]] = task
    await local_manager.run(task, local_manager.generation)
    assert secret not in json.dumps(local_manager.public(task))


async def test_restart_clears_pending_approval_and_never_resumes_old_actions(local_manager):
    local_manager.tasks["interrupted"] = {
        "id": "interrupted",
        "status": "awaiting_approval",
        "events": [],
        "pending": {"id": "stale-approval"},
        "_goal": "private task goal",
    }
    local_manager.persist()
    restored = TaskManager(local_manager.vault, LocalBrowserStub(), ModelGateway())
    restored.restore()
    assert restored.tasks["interrupted"]["pending"] is None
    assert restored.tasks["interrupted"]["status"] == "stopped"
    assert "_goal" not in restored.tasks["interrupted"] and not restored.workers
    with pytest.raises(ValueError):
        await restored.control("interrupted", "resume")


def test_configured_provider_key_is_redacted_even_when_not_a_profile_record():
    gateway = ModelGateway()
    key = "sk-provider-credential-canary-ABCDEFGHIJ"
    gateway.api_key = key
    observation = {
        "title": "Form",
        "url": "https://example.test/form",
        "text": "Public instructions",
        "fields": [],
    }
    payload = gateway.prepare("Fill this form; my provider credential is " + key, observation, [], [], [])
    assert key not in json.dumps(payload)


def test_unknown_populated_field_value_is_redacted_when_repeated_in_page_text():
    value = "OpaquePersonalCanaryZebra"
    raw = {
        "title": "Welcome " + value,
        "url": "https://example.test/form",
        "text": "The applicant shown here is " + value,
        "fields": [{"index": 1, "label": "Applicant", "tag": "input", "input_type": "text", "value": value}],
    }
    sanitized = sanitize_observation(raw, [], vision=False)
    assert value not in json.dumps(sanitized)


def test_setup_document_example_extracts_its_statement_total():
    result = extract_document(
        "setup-example.txt",
        b"Full name: Aarav Example\nEmail: aarav@example.test\nStatement total: 125000.00\n",
    )
    assert any(
        item["value"] == "125000.00" and item["field_type"] in {"amount", "money", "statement_total"}
        for item in result["candidates"]
    )


def test_invalid_later_review_candidate_cannot_leave_partial_confirmed_records(local_api):
    app, client, headers = local_api
    uploaded = client.post(
        "/api/v1/documents",
        headers=headers,
        files={"file": ("statement.txt", b"Name: Aria Example\nTotal: 250.00", "text/plain")},
    )
    assert uploaded.status_code == 200
    document_id = uploaded.json()["document"]["id"]
    body = {
        "candidates": [
            {"label": "Full name", "field_type": "person_name", "value": "Aria Example", "selected": True},
            {"label": "Total", "field_type": "money", "value": "   ", "selected": True},
        ]
    }
    response = client.post(f"/api/v1/documents/{document_id}/review", headers=headers, json=body)
    assert response.status_code in {400, 422}
    assert app.state.vault.records() == []
    assert not app.state.vault.load_blob("document_" + document_id).get("reviewed", False)


def test_short_secrets_do_not_corrupt_opaque_reference_ids_or_control_types():
    assert sanitize_text("ref_1a 12 and value 1", ["1"]) == "ref_1a 12 and value [REDACTED]"
    raw = {
        "title": "Value a",
        "url": "https://example.test",
        "text": "Value a",
        "fields": [
            {"index": 1, "tag": "input", "input_type": "text", "label": "Initial", "value": "a"},
            {"index": 2, "tag": "a", "label": "Continue", "value": ""},
        ],
    }
    result = sanitize_observation(raw, [])
    assert result["fields"][1]["tag"] == "a"
    assert result["fields"][0]["input_type"] == "text"
    assert result["text"] == "Value [REDACTED]"


def test_document_confirmation_rolls_back_records_when_checkpoint_write_fails(local_manager):
    vault = local_manager.vault
    document = vault.put_document("statement.txt", b"Total: 125.00")
    vault.save_blob("document_" + document["id"], {"candidates": [], "warnings": []})
    with sqlite3.connect(vault.path) as db:
        db.execute(
            "CREATE TRIGGER fail_checkpoint BEFORE UPDATE ON items WHEN NEW.kind='blob' "
            "BEGIN SELECT RAISE(ABORT, 'synthetic checkpoint failure'); END"
        )
    with pytest.raises(sqlite3.IntegrityError):
        vault.confirm_document(document["id"], [{"label": "Total", "field_type": "money", "value": "125.00"}])
    assert vault.records() == []
    assert not vault.load_blob("document_" + document["id"]).get("reviewed", False)


async def test_uncertain_task_cannot_be_made_resumable_by_pausing_it(local_manager):
    task = {
        "id": "uncertain",
        "status": "outcome_unknown",
        "events": [],
        "pending": None,
        "_goal": "Submit the form once",
        "step": 1,
    }
    local_manager.tasks[task["id"]] = task
    try:
        await local_manager.control(task["id"], "pause")
    except ValueError:
        pass
    assert task["status"] == "outcome_unknown"


def test_extension_can_pair_without_origin_using_its_explicit_identity(local_api):
    _, client, _ = local_api
    identity = "chrome-extension://" + "a" * 32
    paired = client.post(
        "/api/v1/pair", headers={"X-Guard-Origin": identity}, json={"code": "local-pairing-code"}
    )
    assert paired.status_code == 200
    token = paired.json()["token"]
    for identity_headers in (
        {"X-Guard-Origin": identity},
        {"Origin": identity},
        {"Origin": identity, "X-Guard-Origin": identity},
    ):
        headers = {**identity_headers, "Authorization": "Bearer " + token}
        assert client.get("/api/v1/status", headers=headers).status_code == 200
        assert client.get("/api/v1/records", headers=headers).status_code == 403
        assert client.post("/api/v1/vault/lock", headers=headers).status_code == 403


@pytest.mark.parametrize(
    "actual_origin",
    ["https://unrelated.example", "http://testserver", "null", "chrome-extension://" + "b" * 32],
)
def test_extension_identity_header_cannot_override_a_different_real_origin(local_api, actual_origin):
    _, client, _ = local_api
    headers = {"Origin": actual_origin, "X-Guard-Origin": "chrome-extension://" + "a" * 32}
    response = client.post("/api/v1/pair", headers=headers, json={"code": "local-pairing-code"})
    assert response.status_code == 403


@pytest.mark.parametrize(
    "identity",
    [
        "",
        "null",
        "https://example.test",
        "chrome-extension://" + "q" * 32,
        "chrome-extension://" + "a" * 31,
        "chrome-extension://" + "a" * 32 + "/",
    ],
)
def test_malformed_extension_identity_is_rejected_including_empty_header(local_api, identity):
    _, client, _ = local_api
    response = client.post(
        "/api/v1/pair", headers={"X-Guard-Origin": identity}, json={"code": "local-pairing-code"}
    )
    assert response.status_code == 403


def test_originless_extension_token_stays_bound_and_cannot_gain_dashboard_role(local_api):
    _, client, dashboard_headers = local_api
    identity = "chrome-extension://" + "a" * 32
    paired = client.post(
        "/api/v1/pair", headers={"X-Guard-Origin": identity}, json={"code": "local-pairing-code"}
    )
    token_header = {"Authorization": "Bearer " + paired.json()["token"]}
    for identity_headers in (
        {},
        {"Sec-Fetch-Site": "same-origin"},
        {"Origin": "http://testserver"},
        {"X-Guard-Origin": "chrome-extension://" + "b" * 32},
    ):
        response = client.get("/api/v1/status", headers={**token_header, **identity_headers})
        assert response.status_code == 401
    response = client.get(
        "/api/v1/status",
        headers={"Authorization": dashboard_headers["Authorization"], "X-Guard-Origin": identity},
    )
    assert response.status_code == 401


def test_extension_preflight_permits_identity_header_for_matching_origin(local_api):
    _, client, _ = local_api
    identity = "chrome-extension://" + "a" * 32
    response = client.options(
        "/api/v1/status",
        headers={
            "Origin": identity,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,x-guard-origin",
        },
    )
    assert response.status_code == 200
    assert response.headers["Access-Control-Allow-Origin"] == identity
    assert "x-guard-origin" in response.headers["Access-Control-Allow-Headers"].lower()


def test_resume_api_accepts_explicit_new_record_selection_and_drops_old_bindings(local_api):
    app, client, headers = local_api
    manager, vault = app.state.manager, app.state.vault
    old = vault.put_record("Old name", "person_name", "Old Person")
    new = vault.put_record("Statement total", "money", "250.00", scope="document:statement")
    task = {
        "id": "reselect",
        "status": "waiting_input",
        "events": [],
        "pending": None,
        "_goal": "Fill the form",
        "_record_ids": [old["id"]],
        "_ref_ids": {},
        "_refs": {},
    }
    manager.tasks[task["id"]] = task
    old_ref = manager.catalog(task)[0]["id"]
    launched = []
    manager.launch = lambda current: launched.append(current["id"])
    response = client.post(
        "/api/v1/tasks/reselect/control",
        headers=headers,
        json={"action": "resume", "record_ids": [new["id"]]},
    )
    assert response.status_code == 200
    assert task["_record_ids"] == [new["id"]] and launched == ["reselect"]
    catalog = manager.catalog(task)
    assert len(catalog) == 1 and manager.resolve(task, catalog[0]["id"])["id"] == new["id"]
    with pytest.raises(ValueError, match="out-of-task"):
        manager.resolve(task, old_ref)


async def test_resume_rejects_deleted_selection_without_changing_existing_authorization(local_manager):
    old = local_manager.vault.put_record("Name", "person_name", "Aria Example")
    task = {
        "id": "reselect",
        "status": "waiting_input",
        "events": [],
        "pending": None,
        "_goal": "Fill the form",
        "_record_ids": [old["id"]],
    }
    local_manager.tasks[task["id"]] = task
    launched = []
    local_manager.launch = lambda current: launched.append(current["id"])
    with pytest.raises(ValueError, match="no longer available"):
        await local_manager.control(task["id"], "resume", record_ids=["deleted-record"])
    assert task["status"] == "waiting_input" and task["_record_ids"] == [old["id"]] and not launched


async def test_resume_without_reselection_preserves_the_original_record_allowlist(local_manager):
    old = local_manager.vault.put_record("Name", "person_name", "Aria Example")
    local_manager.vault.put_record("Unselected new email", "email", "new@example.test")
    task = {
        "id": "resume-original",
        "status": "paused",
        "events": [],
        "pending": None,
        "_goal": "Fill the form",
        "_record_ids": [old["id"]],
    }
    local_manager.tasks[task["id"]] = task
    local_manager.launch = lambda _: None
    await local_manager.control(task["id"], "resume")
    assert task["_record_ids"] == [old["id"]]

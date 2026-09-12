"""Hosted API compatibility checks with in-memory HTTP transport only."""

from __future__ import annotations

import json
from copy import deepcopy

import httpx
import pytest

from privacy_guard import gateway as gateway_module
from privacy_guard.agent_runtime import BrowserAgentRuntime, ReferenceInput
from privacy_guard.gateway import ModelGateway, canonical, digest
from privacy_guard.tasks import TaskManager
from privacy_guard.vault import Vault


def configured_gateway():
    gateway = ModelGateway()
    gateway.mode = "remote"
    gateway.model = "synthetic-structured-model"
    gateway.base_url = "https://provider.invalid/custom/v1/"
    gateway.api_key = "synthetic-key-only-header-849"
    private = ["Synthetic Confidential Name 712"]
    observation = {
        "title": "Application",
        "url": "https://form.invalid/",
        "text": "Name: " + private[0] + " API key: " + gateway.api_key,
        "fields": [
            {
                "index": 1,
                "label": "Full name",
                "tag": "input",
                "input_type": "text",
                "filled": False,
                "options": [],
            }
        ],
    }
    payload = gateway.prepare(
        "Fill the form",
        observation,
        [{"id": "ref_name", "label": "Full name", "type": "person_name"}],
        [],
        private,
    )
    return gateway, payload, private


def install_transport(monkeypatch, handler):
    """Retain the real httpx request encoder while replacing all network IO."""
    real_client = httpx.AsyncClient
    client_options = []

    class ClientWithFixtureTransport(real_client):
        def __init__(self, **options):
            client_options.append(deepcopy(options))
            super().__init__(transport=httpx.MockTransport(handler), **options)

    monkeypatch.setattr(gateway_module.httpx, "AsyncClient", ClientWithFixtureTransport)
    return client_options


def response_for(content):
    return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})


async def test_exact_approved_bytes_provider_url_and_key_only_in_authorization(monkeypatch):
    gateway, payload, private = configured_gateway()
    requests = []

    def handle(request):
        requests.append(request)
        return response_for(
            json.dumps(
                {
                    "action": "input_ref",
                    "element_index": 1,
                    "value_ref": "ref_name",
                    "direction": "down",
                    "message": "Use the saved name reference",
                }
            )
        )

    options = install_transport(monkeypatch, handle)
    reviewed_bytes = canonical(payload)
    proposal = await gateway.call(payload, digest(payload), private, "remote")
    assert proposal.action == "input_ref"
    assert proposal.element_index == 1 and proposal.value_ref == "ref_name"
    assert len(requests) == 1
    request = requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://provider.invalid/custom/v1/chat/completions"
    assert request.content == reviewed_bytes
    assert request.headers["authorization"] == "Bearer " + gateway.api_key
    assert request.headers["content-type"] == "application/json"
    assert gateway.api_key.encode() not in request.content
    assert all(secret.encode() not in request.content for secret in private)
    assert not request.url.query
    assert options == [{"timeout": 60, "follow_redirects": False, "trust_env": False}]
    assert gateway.sent == [payload]


@pytest.mark.parametrize(
    "content",
    [
        "not valid JSON",
        "[]",
        '{"action":"fetch","url":"https://attacker.invalid"}',
        '{"action":"input_ref","element_index":1}',
        '{"action":"input_ref","element_index":1,"value_ref":{"id":"ref_name"}}',
        '{"action":"input_ref","element_index":1,"value_ref":"ref_name","value":"literal-private-value"}',
        '{"action":"click","element_index":1,"_approved":true}',
        '{"action":"done","value_ref":"ref_name"}',
    ],
)
async def test_invalid_provider_action_or_extra_secret_fields_are_rejected(monkeypatch, content):
    gateway, payload, private = configured_gateway()
    requests = []

    def handle(request):
        requests.append(request)
        return response_for(content)

    install_transport(monkeypatch, handle)
    with pytest.raises(ValueError, match="invalid action; nothing was executed"):
        await gateway.call(payload, digest(payload), private, "remote")
    assert len(requests) == 1


@pytest.mark.parametrize("status", [307, 429, 500])
async def test_http_error_is_not_retried_and_redirect_is_never_followed(monkeypatch, status):
    gateway, payload, private = configured_gateway()
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            status,
            headers={"Location": "https://other-provider.invalid/collect"},
            text="untrusted server body with private-looking text",
        )

    install_transport(monkeypatch, handle)
    with pytest.raises(ValueError, match=rf"HTTP {status}") as error:
        await gateway.call(payload, digest(payload), private, "remote")
    assert "untrusted server body" not in str(error.value)
    assert len(requests) == 1
    assert requests[0].url.host == "provider.invalid"
    assert gateway.sent == []


async def test_payload_or_selected_model_changed_after_approval_never_opens_transport(monkeypatch):
    gateway, payload, private = configured_gateway()

    def unexpected_request(_request):
        raise AssertionError("Invalid approval must not issue a request")

    options = install_transport(monkeypatch, unexpected_request)
    approved_hash = digest(payload)
    changed = deepcopy(payload)
    changed["messages"][1]["content"][0]["text"] += " unreviewed"
    with pytest.raises(ValueError, match="changed after approval"):
        await gateway.call(changed, approved_hash, private, "remote")
    gateway.model = "another-model"
    with pytest.raises(ValueError, match="Model settings changed after approval"):
        await gateway.call(payload, approved_hash, private, "remote")
    assert not options


class NoMutationBrowser:
    """Observe a finite form; record any attempt to execute a browser action."""

    def __init__(self):
        self.executions = []
        self.observations = 0

    async def tabs(self):
        return [{"target_id": "exact-target", "url": "https://form.invalid/", "title": "Application"}]

    async def observe(self, target_id, **_kwargs):
        self.observations += 1
        return {
            "target_id": target_id,
            "url": "https://form.invalid/",
            "title": "Application",
            "epoch": "stable-epoch",
            "text": "Full name",
            "screenshot": None,
            "width": 800,
            "height": 600,
            "fields": [
                {
                    "index": 1,
                    "label": "Full name",
                    "tag": "input",
                    "input_type": "text",
                    "value": "",
                    "options": [],
                    "rect": {},
                }
            ],
        }

    async def execute(self, *args, **kwargs):
        self.executions.append((args, kwargs))
        raise AssertionError("A failed model call or unknown reference must not mutate the browser")


@pytest.mark.parametrize("failure", ["timeout", "unknown_reference"])
async def test_native_model_timeout_and_unknown_reference_stop_before_execution(monkeypatch, tmp_path, failure):
    gateway, _, _ = configured_gateway()
    requests = []

    def handle(request):
        requests.append(request)
        if failure == "timeout":
            raise httpx.ReadTimeout("Synthetic provider timeout", request=request)
        return response_for(
            json.dumps(
                {
                    "index": 1,
                    "value_ref": "ref_does_not_exist",
                }
            )
        )

    install_transport(monkeypatch, handle)
    vault = Vault(tmp_path)
    vault.initialize("synthetic integration passphrase 924")
    vault.put_record("Full name", "person_name", "Confidential Synthetic Person")
    browser = NoMutationBrowser()
    manager = TaskManager(vault, browser, gateway)
    task = {
        "id": "native-transport-test", "_generation": 0, "_goal": "Fill the form",
        "_origin": "https://form.invalid", "target_id": "exact-target",
        "_ref_ids": {}, "_refs": {},
    }
    runtime = BrowserAgentRuntime(manager, task)
    try:
        messages = [{"role": "user", "content": "Fill the form using the available reference"}]
        if failure == "timeout":
            with pytest.raises(httpx.ReadTimeout):
                await runtime.llm.ainvoke(messages, ReferenceInput)
        else:
            response = await runtime.llm.ainvoke(messages, ReferenceInput)

            async def existing_field(_index):
                return runtime.raw["fields"][0]

            monkeypatch.setattr(runtime, "field", existing_field)
            with pytest.raises(ValueError, match="unknown or out-of-task reference"):
                await runtime.execute_element(
                    "input_ref", response.completion.index, response.completion.value_ref
                )
        assert browser.observations == 1
        assert browser.executions == []
        assert len(requests) == 1
        assert all(secret.encode() not in requests[0].content for secret in vault.secrets())
    finally:
        await manager.stop_all()
        vault.lock()

"""Exercise the authenticated app, encrypted vault and real browser without a provider.

Run from the repository with ``uv run python scripts/smoke_test.py`` after browser
installation. All profile data is synthetic and all generated state is temporary.
The script grants each approval explicitly through the same API as the dashboard.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import tempfile
import threading
import time
from functools import partial
from http.server import BaseHTTPRequestHandler, SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from privacy_guard.api import create_app
from privacy_guard.browser import BrowserDriver, BrowserError
from privacy_guard.config import DATA_DIR, ROOT

_HTML = b"""<!doctype html><html><head><meta charset="utf-8">
<title>Synthetic private form</title><style>body{font:16px sans-serif;padding:20px}
label{display:block;margin:10px}input,textarea{display:block;width:300px}</style></head><body>
<h1>Synthetic private form</h1><form id="demo" onsubmit="event.preventDefault();
document.getElementById('result').textContent='Synthetic form submitted: '+this.full_name.value+' '+this.pan.value;
document.getElementById('count').textContent='Submission count: '+(++window.submissionCount);">
<label>Full name<input name="full_name" autocomplete="name"></label>
<label>Email<input name="email" type="email" autocomplete="email"></label>
<label>Phone<input name="phone" type="tel" autocomplete="tel"></label>
<label>PAN<input name="pan"></label>
<label>Address<textarea name="address" autocomplete="street-address"></textarea></label>
<label>Statement total<input name="total" type="number" step="0.01"></label>
<button type="submit">Submit synthetic form</button></form>
<p id="result"></p><p id="count">Submission count: 0</p>
<script>window.submissionCount=0;</script></body></html>"""


class FixtureHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(_HTML)))
        self.end_headers()
        self.wfile.write(_HTML)

    def log_message(self, *args):
        pass


class ProductFixtureHandler(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


async def checked(client: httpx.AsyncClient, method: str, path: str, **kwargs):
    response = await client.request(method, "/api/v1" + path, **kwargs)
    if response.status_code >= 400:
        raise AssertionError(f"{method} {path} returned HTTP {response.status_code}")
    return response.json()


def assert_private_values_absent(payload: dict, values: list[str]) -> str:
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    folded = serialized.casefold()
    if any(value.casefold() in folded for value in values):
        raise AssertionError("A seeded private value reached a model request")
    return hashlib.sha256(serialized.encode()).hexdigest()


async def stable_state(driver: BrowserDriver, target_id: str, expected_fields: int = 7):
    for _ in range(25):
        try:
            state = await driver.observe(target_id)
            if len(state["fields"]) == expected_fields:
                return state
        except BrowserError as error:
            if str(error) not in {
                "observation_failed",
                "page_changed_during_observation",
                "unsupported_origin",
            }:
                raise
        await asyncio.sleep(0.1)
    raise AssertionError("The synthetic form did not become ready")


def find_executable() -> Path:
    for data in (DATA_DIR, ROOT / ".runtime"):
        try:
            return BrowserDriver(data, ROOT / "apps/extension")._browser_executable()
        except BrowserError:
            continue
    raise BrowserError("Install the browser with uv run python scripts/browser_install.py first")


async def smoke(
    headless: bool = True,
    product_demo: bool = False,
    approval_delay: float = 0,
    resize_during_approval: bool = False,
    submit: bool = True,
) -> dict:
    executable = find_executable()
    previous_executable = os.environ.get("GUARD_BROWSER_EXECUTABLE")
    os.environ["GUARD_BROWSER_EXECUTABLE"] = str(executable)
    handler = partial(ProductFixtureHandler, directory=str(ROOT / "demo")) if product_demo else FixtureHandler
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="privacy-guard-smoke-") as temporary:
            root = Path(temporary)
            extension = ROOT / "apps/extension"
            if not (extension / "manifest.json").exists():
                extension = root / "extension"
                extension.mkdir()
                (extension / "manifest.json").write_text(
                    json.dumps(
                        {
                            "manifest_version": 3,
                            "name": "Privacy Guard smoke test",
                            "version": "0.0.1",
                            "permissions": ["activeTab"],
                            "action": {"default_title": "Privacy Guard"},
                        }
                    )
                )
            driver = BrowserDriver(root / "data", extension, headless=headless)
            original_execute = driver.execute
            driver_errors = []

            async def inspected_execute(*args, **kwargs):
                try:
                    return await original_execute(*args, **kwargs)
                except BrowserError as error:
                    driver_errors.append(str(error))
                    raise

            driver.execute = inspected_execute
            code = "synthetic-smoke-pairing-code"
            app = create_app(root / "data", browser=driver, pairing_code=code, testing=True)
            app.state.manager.demo_port = server.server_port
            transport = httpx.ASGITransport(app=app)
            # ASGITransport does not run application lifespan automatically.
            async with app.router.lifespan_context(app):
                async with httpx.AsyncClient(
                    transport=transport, base_url="http://testserver", timeout=30
                ) as client:
                    paired = await checked(client, "POST", "/pair", json={"code": code})
                    client.headers["Authorization"] = "Bearer " + paired["token"]
                    await checked(
                        client,
                        "POST",
                        "/vault/initialize",
                        json={"passphrase": "synthetic smoke passphrase 48192"},
                    )
                    records = (await checked(client, "POST", "/demo/seed"))["records"]
                    values = [record["value"] for record in records]
                    expected = {record["label"]: record["value"] for record in records}
                    assert len(expected) == 6, "The smoke fixture expects six distinct seeded fields"
                    await checked(client, "POST", "/browser/launch")
                    route = "/" if product_demo else "/form"
                    tab = await driver.new_page(f"http://127.0.0.1:{server.server_port}{route}")
                    await stable_state(driver, tab["target_id"])
                    if product_demo:
                        page, _ = await driver._context(tab["target_id"])
                        await page.evaluate(
                            "() => { window.__guardSmokeSubmissions = 0; document.addEventListener('submit', () => window.__guardSmokeSubmissions++, true); }"
                        )
                    started = await checked(
                        client,
                        "POST",
                        "/tasks",
                        json={
                            "goal": (
                                "Fill and submit this synthetic form using my saved profile."
                                if submit
                                else "Fill this synthetic form using my saved profile. Stop before submitting."
                            ),
                            "target_id": tab["target_id"],
                            "mode": "demo",
                            "vision": True,
                        },
                    )
                    task_id = started["id"]
                    approvals: dict[str, int] = {}
                    seen_approvals: set[str] = set()
                    request_hashes: set[str] = set()
                    resized = False
                    deadline = time.monotonic() + 120
                    while time.monotonic() < deadline:
                        task = await checked(client, "GET", f"/tasks/{task_id}")
                        if task.get("request"):
                            request_hashes.add(assert_private_values_absent(task["request"], values))
                        if task["status"] == "completed":
                            break
                        if task["status"] in {
                            "failed",
                            "blocked",
                            "stopped",
                            "outcome_unknown",
                            "waiting_input",
                        }:
                            raise AssertionError(
                                "Task did not complete: "
                                + json.dumps(
                                    {
                                        **{
                                            key: task.get(key)
                                            for key in ("status", "error", "result", "step")
                                        },
                                        "driver_errors": driver_errors,
                                    }
                                )
                            )
                        pending = task.get("pending")
                        if pending and pending["id"] not in seen_approvals:
                            if pending["kind"] == "model":
                                request_hashes.add(
                                    assert_private_values_absent(pending["payload"]["request"], values)
                                )
                            seen_approvals.add(pending["id"])
                            approvals[pending["kind"]] = approvals.get(pending["kind"], 0) + 1
                            if resize_during_approval and not resized and pending["kind"] == "disclosure":
                                page, _ = await driver._context(tab["target_id"])
                                await page.set_viewport_size(980, 720)
                                await asyncio.sleep(0.2)
                                resized = True
                            if approval_delay:
                                await asyncio.sleep(approval_delay)
                            await checked(
                                client,
                                "POST",
                                f"/tasks/{task_id}/approve",
                                json={
                                    "approval_id": pending["id"],
                                    "approved": True,
                                },
                            )
                        await asyncio.sleep(0.02)
                    else:
                        raise AssertionError("Task exceeded the smoke-test deadline")
                    state = await stable_state(driver, tab["target_id"], 1 if product_demo and submit else 7)
                    if product_demo:
                        page, _ = await driver._context(tab["target_id"])
                        actual = json.loads(
                            await page.evaluate(
                                "() => ({'Full name':document.getElementById('full_name').value,'Email':document.getElementById('email').value,'Phone':document.getElementById('phone').value,'PAN':document.getElementById('pan').value,'Address':document.getElementById('address').value,'Statement total':document.getElementById('statement_total').value})"
                            )
                        )
                        count = await page.evaluate("() => window.__guardSmokeSubmissions")
                        assert count == str(int(submit)), "Submission count must match the user's intent"
                    else:
                        actual = {
                            field["label"]: field["value"]
                            for field in state["fields"]
                            if field["tag"] in {"input", "textarea", "select"}
                        }
                    assert actual == expected, "Browser field readback did not match the saved profile"
                    expected_confirmation = (
                        "Application received locally." if product_demo else "Synthetic form submitted:"
                    )
                    assert (expected_confirmation in state["text"]) == submit, (
                        "The form's confirmation must match the user's submission intent"
                    )
                    if not product_demo:
                        assert f"Submission count: {int(submit)}" in state["text"], (
                            "Submission count must match the user's intent"
                        )
                    assert approvals.get("disclosure") == (7 if resized else 6), (
                        "Each private entry requires fresh approval"
                    )
                    if resized:
                        assert driver_errors == ["stale_observation"], (
                            "Viewport change must reject the stale action"
                        )
                    assert approvals.get("submit", 0) == int(submit), (
                        "Only an explicitly requested submission may be proposed for approval"
                    )
                    assert len(request_hashes) >= (8 if submit else 7), (
                        "Every observation must pass through the request boundary"
                    )
                    assert not app.state.gateway.sent, "Demo mode must not send requests to a remote provider"
                    ciphertext = app.state.vault.path.read_bytes()
                    assert not any(value.encode() in ciphertext for value in values), (
                        "Vault file contains plaintext data"
                    )
                    await checked(client, "POST", "/vault/lock")
                    assert not app.state.vault.unlocked
                    return {
                        "status": "passed",
                        "engine": "real-browser-use-cdp",
                        "planner": "local-demo",
                        "fixture": "shipped-demo" if product_demo else "minimal-form",
                        "fields_verified": len(expected),
                        "requests_checked": len(request_hashes),
                        "approvals": approvals,
                        "submissions": int(submit),
                        "stale_observation_recovery": "passed" if resized else "not_requested",
                        "remote_requests": 0,
                        "vault_plaintext_check": "passed",
                        "temporary_data": "removed on exit",
                    }
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
        if previous_executable is None:
            os.environ.pop("GUARD_BROWSER_EXECUTABLE", None)
        else:
            os.environ["GUARD_BROWSER_EXECUTABLE"] = previous_executable


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--headed", action="store_true", help="Show the dedicated synthetic test browser")
    parser.add_argument(
        "--product-demo", action="store_true", help="Serve and verify the actual shipped demo"
    )
    parser.add_argument(
        "--approval-delay", type=float, default=0, help="Seconds to wait before each test approval"
    )
    parser.add_argument(
        "--resize-during-approval",
        action="store_true",
        help="Verify stale state is recaptured and reapproved",
    )
    parser.add_argument("--no-submit", action="store_true", help="Verify filling stops before submission")
    args = parser.parse_args()
    print(
        json.dumps(
            asyncio.run(
                smoke(
                    headless=not args.headed,
                    product_demo=args.product_demo,
                    approval_delay=args.approval_delay,
                    resize_during_approval=args.resize_during_approval,
                    submit=not args.no_submit,
                )
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()

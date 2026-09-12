"""Chrome extension UI smoke test. Synthetic microphone; all companion API calls mocked.
Run: .venv/bin/python -m pytest apps/extension/tests/test_extension_smoke.py -q
"""

import json
import os
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[3]


def test_extension_voice_delivery_preserves_original_tab(tmp_path):
    from privacy_guard.browser import BrowserDriver
    from privacy_guard.config import DATA_DIR

    executable = BrowserDriver(DATA_DIR, ROOT / "apps/extension")._browser_executable()
    calls = []
    state = {
        "vault": {"initialized": True, "unlocked": True},
        "browser": {"connected": True},
        "provider": {"mode": "remote", "model": "fixture-model", "configured": True},
        "task": None,
    }

    def api(route):
        request = route.request
        path = request.url.split("/api/v1", 1)[1]
        if request.method == "POST":
            calls.append((path, request.post_data_buffer))
        if path == "/pair":
            payload = {"token": "test-only-extension-token"}
        elif path == "/status":
            payload = state
        elif path == "/record-catalog":
            payload = {"records": []}
        elif path == "/browser/tabs":
            payload = {"tabs": []}
        elif path == "/audio/transcribe":
            payload = {
                "text": "Fill this form using my selected profile.",
                "provider": "OpenAI",
                "model": "whisper-1",
            }
        elif path == "/tasks":
            payload = {
                "id": "ui-task",
                "goal": "Fill this form",
                "step": 0,
                "status": "created",
                "events": [],
            }
        else:
            payload = {}
        route.fulfill(status=200, content_type="application/json", body=json.dumps(payload))

    with sync_playwright() as pw:
        context = pw.chromium.launch_persistent_context(
            str(tmp_path / "chromium-profile"),
            executable_path=str(executable),
            headless=True,
            viewport={"width": 390, "height": 950},
            args=[
                f"--disable-extensions-except={ROOT / 'apps/extension'}",
                f"--load-extension={ROOT / 'apps/extension'}",
                "--use-fake-device-for-media-stream",
                "--use-fake-ui-for-media-stream",
            ],
        )
        try:
            context.route("http://127.0.0.1:8765/api/v1/**", api)
            worker = (
                context.service_workers[0]
                if context.service_workers
                else context.wait_for_event("serviceworker")
            )
            extension_id = worker.url.split("/")[2]
            source = context.new_page()
            source.set_content("<title>Original fictional form</title><h1>Original fictional form</h1>")
            source_tab = worker.evaluate(
                "async () => (await chrome.tabs.query({})).find(t => t.title === 'Original fictional form').id"
            )
            panel = context.new_page()
            panel.goto(f"chrome-extension://{extension_id}/sidepanel.html")
            panel.get_by_label("Terminal pairing code").fill("test-only-code")
            panel.get_by_role("button", name="Connect extension", exact=False).click()
            expect(panel.get_by_role("button", name="Start task", exact=False)).to_be_enabled()
            expect(panel.locator("#mode")).to_have_value("remote")
            expect(panel.locator("#vision")).to_be_checked()
            expect(panel.locator("#stop-before-submit")).to_be_checked()
            capture = context.new_page()
            capture.goto(f"chrome-extension://{extension_id}/capture.html?source_tab={source_tab}")
            capture.get_by_role("button", name="Start recording", exact=True).click()
            capture.get_by_role("button", name="Stop recording", exact=True).wait_for()
            capture.wait_for_timeout(300)
            capture.get_by_role("button", name="Stop recording", exact=True).click()
            expect(capture.get_by_role("button", name="Transcribe with Whisper", exact=True)).to_be_enabled()
            capture.wait_for_function("document.querySelector('#recording-playback')?.readyState >= 1")
            assert not any(path == "/audio/transcribe" for path, _ in calls)
            capture.get_by_role("button", name="Transcribe with Whisper", exact=True).click()
            expect(capture.get_by_role("textbox", name="Editable transcript")).to_have_value(
                "Fill this form using my selected profile."
            )
            assert not any(path == "/tasks" for path, _ in calls)
            capture.get_by_role("button", name="Use transcript in task", exact=True).click()
            expect(panel.locator("#goal")).to_have_value("Fill this form using my selected profile.")
            # Delivery is acknowledged before restoring the source tab, without auto-starting.
            capture.get_by_text("Transcript placed in the side panel task draft.", exact=False).wait_for()
            active = worker.evaluate(
                "async () => (await chrome.tabs.query({active:true,currentWindow:true}))[0].id"
            )
            assert active == source_tab
            assert not any(path == "/tasks" for path, _ in calls)
            assert sum(path == "/audio/transcribe" for path, _ in calls) == 1
            folder = os.getenv("GUARD_UI_CAPTURE_DIR")
            if folder:
                Path(folder).mkdir(parents=True, exist_ok=True)
                capture.screenshot(
                    path=str(Path(folder) / "extension-transcript-test-fixture.png"), full_page=True
                )
                panel.screenshot(path=str(Path(folder) / "extension-task-test-fixture.png"), full_page=True)
            panel.bring_to_front()
            panel.locator("#start-url").fill("https://example.test/application")
            panel.get_by_role("button", name="Start task", exact=False).click()
            panel.locator("#task-status").get_by_text("created", exact=True).wait_for()
            request = json.loads(next(body for path, body in calls if path == "/tasks"))
            assert request["start_url"] == "https://example.test/application"
            assert "target_id" not in request
            assert request["vision"] is True and request["stop_before_submit"] is True
        finally:
            context.close()

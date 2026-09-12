# Set up Dev Privacy Guard v0.2

This checkout targets the existing Apple M1 Mac with macOS 15 and 8 GB RAM. The dashboard, vault, document processing and Browser Use agent run locally. Reasoning uses your hosted LLM/VLM API; optional voice transcription uses OpenAI Whisper.

Already installed? Follow [startup.md](startup.md) for the short launch guide. The start script uses `.venv/bin/python` directly; activating the environment or running verification is not required to start the app.

## 1. Install prerequisites

With Homebrew:

```bash
brew install uv node tesseract
```

Use Node 22.12 or later. Python 3.12 is managed by uv; the system Python is not used. Initial dependency and Chromium downloads require internet access and a few GB of free disk space. Docker, Redis and a cloud database are not required.

```bash
uv --version
node --version
npm --version
tesseract --version
```

## 2. Install the project

```bash
cd /Users/aksh-aggarwal/Desktop/Workspace/SIH/Agent
./scripts/setup.sh
```

On another machine, use the directory where you copied this project. The script installs the locked Python environment, builds the dashboard and downloads compatible Chromium into the application's data directory. The extension is bundled HTML/CSS/JavaScript and requires no build.

## 3. Start and pair

```bash
./scripts/start.sh
```

Open [the dashboard](http://127.0.0.1:8765). Keep the terminal running.

1. Copy the pairing code printed in the terminal into the dashboard.
2. Create a vault passphrase, or unlock your existing vault. The UI asks for at least 12 characters.
3. Keep the passphrase safe: there is no password recovery or cloud account.

Pairing codes last 30 minutes. Restarting the companion generates a new code and invalidates previous paired sessions. Pairing is separate from unlocking the encrypted vault.

Default application data: `~/Library/Application Support/Dev Privacy Guard`. Documents, records and provider credentials are stored encrypted there. Your normal Chrome profile is not imported.

## 4. Configure the reasoning API

Open **Settings** in the unlocked dashboard.

- Choose **Remote** reasoning mode.
- Use the default model ID: `gemini-2.5-flash` (Gemini 2.5 Flash).
- Use the default API base URL: `https://generativelanguage.googleapis.com/v1beta/openai`.
- Enter your Gemini API key and save. Previously saved provider settings override defaults; update their model and URL to the values above.

The model must accept **Chat Completions, JSON object output and image inputs** for the screenshot workflow. A text-only model can be used only with visual checkpoints disabled. This project does not train or host an LLM, and a configured model is not a guarantee of successful automation on every website.

Keys are encrypted locally and are never placed in the extension or frontend bundle. The model adapter sends sanitized text automatically unless you enable text review. Every request containing a screenshot waits for your approval. Unnecessary auxiliary model calls and unreviewed image retries are disabled. Optionally save a separate OpenAI key under **Optional fallback · OpenAI**; remote tasks may switch once after a primary-provider error. Image requests require fresh approval for OpenAI. Without a fallback key, only Gemini is used.

## 5. Optional: enable voice input

In **Settings → Voice transcription**, enter an **OpenAI Whisper API key** and save. You may use the same OpenAI account/key as your reasoning model, but this is a separate setting because the reasoning provider may be different.

Voice uses `whisper-1` at OpenAI's audio transcription endpoint. The flow is:

1. Choose **Record task** (dashboard) or the extension's voice control.
2. Grant microphone permission when the browser asks. The extension uses a dedicated recording tab.
3. Speak, then stop. Recordings are limited to 60 seconds and 10 MiB.
4. Listen to or discard the local recording.
5. Choose **Transcribe with Whisper**. **This uploads the original, unredacted audio to OpenAI.**
6. Review and edit the returned text. In the extension, choose **Use transcript** to place it into the task draft.
7. Start the task yourself when the edited instruction and selected records are correct.

No task starts merely because transcription finished. The application does not automatically save recordings or transcripts to the vault. Editing a transcript does not undo the audio upload. The later task instruction passes through the normal model-context text filter.

If microphone access is denied, allow it in the browser's site/extension permissions and macOS **System Settings → Privacy & Security → Microphone**, then reopen the recording surface. Typed instructions always work without a Whisper key. **Remove Whisper key** clears only the voice credential.

## 6. Launch Chromium and load the extension

Choose **Launch browser** in the dashboard. The companion launches dedicated Chromium with a separate profile and local debugging connection.

If the extension icon is missing:

1. In that controlled browser, open `chrome://extensions`.
2. Enable **Developer mode**.
3. Choose **Load unpacked** and select this project's `apps/extension` directory.
4. Pin **Dev Privacy Guard**.
5. Open its side panel and enter the same current pairing code.

The extension's debugger permission is used for exact target discovery. Browser Use owns the actual browser connection. Use the extension in the controlled browser; it does not silently attach to your everyday Chrome windows.

The dashboard can be opened in a different browser. Tasks can start from either interface.

## 7. Run the new portal demonstration

Use a **Remote** model for this flow. A real API key is required for live planning and visual interpretation.

1. Open **New task → Prepare portal demo**. This adds synthetic name/email/phone records and selects only those records, leaving the document fields for the missing-information round.
2. Use `http://127.0.0.1:8766/portal.html` as the starting website. The form does not have to be open beforehand.
3. Enter or dictate: `Open this website and complete the application using my selected profile. Fill available details first, ask for missing documents, and stop before final submission.`
4. Keep **visual checkpoints** and **stop before final submission** enabled. Start the task.
5. Follow the task activity. Approve reviewed navigation clicks if requested.
6. At a screenshot checkpoint, open dashboard review. Compare the original local image with the actual redacted outgoing image. Drag rectangles or use the numeric controls to add masks, then apply them. Each edit replaces the approval.
7. Approve the latest image/context, or deny to block transmission.
8. When information is requested, upload `demo/documents/portal-statement.txt` in **Documents**. Review the PAN, address and statement total. Confirm the appropriate facts; choose profile scope only for reusable details.
9. Return to the waiting task, select the newly confirmed records alongside the original selected profile, and Resume.
10. Inspect the completed application/review page. Final submission is withheld by default.

The portal and its documents are fictional. They demonstrate the workflow and do not prepare or file a real tax return. See [startup.md](startup.md#run-the-portal-demo) for the short demo sequence.

## 8. Run the original no-key form check

The **Demo planner** is deterministic and does not use a remote AI model. It tests the original form, vault, reference mapping and approvals.

1. Add the full demo profile and open the original demo form at `http://127.0.0.1:8766/`.
2. Select **Demo** mode and that controlled tab.
3. Enter `Fill this form using my saved profile. Stop before submitting.`
4. Approve the sanitized contexts and private-value disclosures.

The old planner cannot navigate the multi-step portal. Its optional image path remains completely black; use Remote mode to demonstrate selective visual redaction.

## 9. Pause, resume and stop

- **Pause:** cancels current work. Resume observes the current page before continuing.
- **Waiting for information:** add/review facts, update the task's selected records, then Resume. It continues in the same tab.
- **Stop:** ends the task and revokes further actions; it cannot undo an action already delivered to the site.
- **Lock vault:** clears runtime access to credentials, records and image artifacts; cancels transcription and active browser work.
- **Ctrl+C:** stops the companion and its controlled browser.

After a service restart, pair and unlock again. Unfinished tasks remain stopped. If an action outcome is uncertain, inspect the website before starting another task; do not assume a timeout means nothing happened.

## 10. Verify the installation

```bash
./scripts/verify.sh
```

The checks use synthetic data. Hosted model and Whisper responses are mocked unless explicitly configured for a live run. See [docs/VALIDATION.md](docs/VALIDATION.md) for exact evidence and opt-in browser checks.

After changing dashboard sources:

```bash
npm --prefix apps/dashboard run build
```

Refresh the dashboard. After extension source changes, use **Reload** on its `chrome://extensions` card and reopen the side panel. The app trusts the dashboard served by the companion, not an arbitrary Vite development-server origin.

## Troubleshooting

| Problem | Action |
|---|---|
| Dashboard cannot connect | Keep the companion running and use `http://127.0.0.1:8765`. |
| Pairing expired | Restart the companion and pair both interfaces using the new terminal code. |
| Browser missing | Run `uv run scripts/browser_install.py` from the project directory. |
| Current tab is not controllable | Use the dedicated Chromium, or provide a starting URL to create a controlled tab. |
| Model returns an error | Check API credit, key, model ID, image support and Chat Completions JSON support. No action is authorized by a failed response. |
| Whisper is not configured | Save the separate OpenAI key in Settings. Typed tasks remain available. |
| Whisper error or empty transcript | Record a shorter command or type it. No browser task has started. |
| Image approval changed | Reload the current preview and approve its newest version. Old approval IDs cannot authorize edited images. |
| Screenshot geometry cannot be verified | Let the page settle, stop animations if possible, and retry from fresh state. No uncertain image is sent. |
| Missing information repeats | Select the confirmed record in the task's available-information list before Resume; remove ambiguous duplicates from the selection. |
| Cross-origin redirect, frame or custom control blocked | Complete that step manually, then start a task for the intended destination. |
| OCR unavailable | Install Tesseract and restart the companion. Correct OCR candidates before confirming. |
| Ports occupied | Stop the process already using 8765/8766, then restart. |
| Forgotten vault passphrase | There is no recovery. Preserve the encrypted data before creating a separate vault. |

Advanced settings: `GUARD_DATA_DIR` chooses another local data directory; use the same value when installing the browser. `GUARD_BROWSER_EXECUTABLE` can select compatible Chromium. `GUARD_PORT` and `GUARD_DEMO_PORT` are backend overrides; changing ports also requires updating the extension's fixed localhost URLs/permissions.

Keep the companion and debugging socket on loopback. No signed installer, Chrome Web Store distribution, Firefox support, local vision model or local speech model is included in this version.

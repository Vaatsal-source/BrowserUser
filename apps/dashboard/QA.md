# Interface verification

The dashboard is a local React + TypeScript client. The Chrome extension is a separately loaded, unbundled Manifest V3 side panel. Both call the loopback companion; neither calls a model provider directly.

## Build checks

```sh
npm --prefix apps/dashboard ci
npm --prefix apps/dashboard run build
node --check apps/extension/background.js
node --check apps/extension/sidepanel.js
node --check apps/extension/capture.js
node --check demo/form.js
node --check demo/portal.js
```

The production build performs strict TypeScript checking. All CSS, JavaScript, icons, and fonts are local or system resources; no CDN is required.

## v0.2 browser checks

Run from the repository root after building the dashboard:

```sh
GUARD_BROWSER_TESTS=1 .venv/bin/python -m pytest apps/dashboard/tests apps/extension/tests -q
```

These five real Chromium checks use a disposable browser profile, a synthetic microphone, and mocked companion API responses. They verify:

- A remote task can start from a URL in its goal without an existing browser tab, with visual checkpoints and stop-before-submission enabled by default.
- Dashboard audio remains local and playable until **Transcribe with Whisper** is clicked. The response only updates the editable task draft; it does not start a task.
- Screenshot masks use source-image pixel coordinates. Unsaved masks block approval, applied masks refresh the approval ID, and the outgoing text, destination, and request hash are inspectable.
- The fictional Meridian portal validates its six fields and has a separate review screen before its synthetic submission.
- Extension voice delivery updates the side-panel draft, restores the original browser tab, and never starts a task automatically. A subsequent explicit start can use a supplied URL.

Set `GUARD_UI_CAPTURE_DIR=/tmp/privacy-guard-ui` to save labelled fixture screenshots. The image previews and transcription in these interface tests are fixtures; this suite does not establish live-provider quality or redaction accuracy. Backend and real-agent transport checks are separate.

## Manual browser verification

Use a disposable companion data directory and synthetic documents only. Never run this checklist against a personal vault.

- Pair the dashboard with the terminal code. Invalid codes display an actionable error.
- Initialize the disposable vault. Reload and verify the session stays paired.
- Load the synthetic profile. Saved field counts and the vault table update.
- Confirm values are masked by default; reveal/hide works. Add and edit a synthetic field.
- Upload `demo/documents/sample-statement.txt`. Close review and reopen it from the document library.
- Confirm only selected candidates are saved, with document scope by default. Labels and values can be corrected locally.
- Calculate `1000.25 + 2049.75` from two selected records. Verify the stored result is `3050.00`. Do not include the original Total field as an operand.
- Launch the dedicated browser and open its demo form. Create a task with an exact tab target and selected records.
- Inspect the actual prepared payload, its destination, and hash. Approve context, then separately approve disclosure. No raw profile value should appear in the payload.
- Pause the task. Add or review a missing field, choose it in **Review & resume**, and resume with a fresh observation.
- Verify Stop, denied approvals, stale approvals, provider errors, and uncertain action outcomes display clearly.
- Lock the vault. Private record/document views disappear. Reopening requires the passphrase.
- Verify pairing, active-tab discovery, reference selection, progress, approvals, and task controls through the extension in the dedicated browser.
- At 390 × 844 and desktop width, inspect layout, keyboard focus, scrolling, and dialogs. The document width must not exceed the viewport.

Observed during development: pairing, initialization, profile masking/editing, upload/reopen/selected review, local decimal calculation, browser launch, exact-tab task creation, actual context/disclosure approvals, and responsive layout worked against the live synthetic companion. Browser execution and security regression coverage are maintained in the root test suite; frontend screenshots alone are not privacy verification.

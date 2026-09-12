## Website compatibility update — 9 September 2026

The browser now waits for document readiness in the exact created tab and returns the resolved startup URL. The local observer supports same-origin frames and open shadow DOM; uninspectable frames no longer block unrelated controls. Native browser-state messages are replaced with sanitized local observations before model transport. New destinations require an in-app approval. Public search queries are restricted to text from the user's task.

Added regression coverage exercises startup redirects, main-document/frame/shadow input filling, frame-navigation invalidation, same-tab links, exclusion of uninspectable frame text, destination approval, restricted public search input, and blocking screenshots with uninspectable closed shadow roots. Browser integration uses real Chromium with synthetic websites; agent responses are simulated. This does not establish successful live Amazon or ITR workflows. Cross-origin frame forms, closed components, CAPTCHAs, uploads and rapidly changing page layouts remain limitations.

The historical results below describe earlier versions.

# Version 0.1 validation

Verified on 7 September 2026 in this checkout on an Apple M1 MacBook Air with 8 GB RAM and macOS 15.6.1. Only synthetic identities, documents and forms were used. No personal browser profile or paid model credential was used.

Final checks: **120 Python tests passed in 14.26 seconds**, including the real-browser integration, with no skipped tests (`GUARD_BROWSER_TESTS=1 uv run pytest -q`). The default suite skips that one opt-in integration test. Ruff, the strict TypeScript/production build, extension/demo JavaScript syntax checks, and the dependency-lock check passed. npm reported zero known vulnerabilities at verification time.

## Reproduce the checks

Run setup first, following [setup.md](../setup.md), then:

```bash
./scripts/verify.sh
GUARD_BROWSER_TESTS=1 uv run pytest tests/test_browser.py -q
uv run python scripts/smoke_test.py --product-demo --headed --approval-delay 1 --resize-during-approval
```

`verify.sh` runs the Python regression suite, Ruff, the TypeScript/production dashboard build, and real headless-browser tests of the shipped demo with and without submission. The visible-browser command adds deliberate approval delays and changes the viewport during an approval. These tests use a temporary vault and browser profile, approve synthetic test requests automatically, and clean up afterward. Application users still approve their own requests manually.

## What has been exercised

| Area | Evidence |
|---|---|
| Vault | Authenticated encryption, wrong passphrase, record versions, lock, encrypted blobs, atomic document confirmation, deletion and restart behavior. |
| Documents | Local text/PDF/image extraction, Tesseract OCR, scanned PDF handling, invalid/oversized inputs, reviewed candidates, document scope and decimal arithmetic. |
| Privacy boundary | Known-value and observed-field redaction; repeated private values in later observations; exact outgoing request hashing; schema/envelope restrictions; independently verified canonical black images. |
| Hosted adapter | Mocked HTTP transport verifies exact request bytes, endpoint, Authorization placement, structured responses, no redirects/retries, errors, timeouts and rejection before browser execution. |
| Action control | Reference/version checks, missing values, invalid action shapes, changed approvals, task cancellation, lock and fresh-state recovery; exceptional server exit and cancelled/stalled browser teardown still clean up the owned browser. |
| Browser | Real Browser Use connection, separate same-URL tabs, standard HTML input/select/click, exact target binding, stale observation rejection and disclosure/click gates. |
| Shipped demo | Six actual saved values entered; exactly one real submit event; sanitized requests checked for every seeded private value; encrypted temporary vault inspected. |
| Interfaces | Live local dashboard and native Chrome side panel, pairing, profile/document review, local sum, exact-tab selection, progress and approvals; desktop and 390 × 844 layout checks. See [interface checklist](../apps/dashboard/QA.md). |

The visible-browser stale-page test observed exactly one `stale_observation` rejection after resizing. The task captured fresh context, requested fresh approvals, filled the six fields and submitted once. It required ten model-context approvals, seven disclosure approvals (including the rejected stale attempt) and one final click approval. A stale action was not silently authorized against a new observation.

The fill-only scenario uses the exact instruction **“Stop before submitting.”** It filled all six fields, reviewed seven model contexts and six disclosures, and completed with **zero submission proposals and zero submit events**. The submit scenario reviewed nine model contexts, six disclosures and one final click, and produced exactly one submit event.

## Device measurement

One warm headless run of the shipped demo used this command:

```bash
/usr/bin/time -l .venv/bin/python scripts/smoke_test.py --product-demo
```

It completed in **5.18 seconds** with nine context approvals, six disclosure approvals and one submit approval, all automated by the test. The operating system reported maximum resident set size **191,791,104 bytes (182.91 MiB)**, peak memory footprint **97,159,552 bytes (92.66 MiB)** and zero swaps for that command. These are per-command OS statistics, **not an aggregate Chromium process-tree peak or total application memory budget**. Dependencies/browser were already installed; this excludes downloads, human review and hosted-model latency. Broader task timing and memory benchmarks remain to be collected.

After the final shutdown improvements, the same command with `--no-submit` completed in **4.98 seconds**, with maximum RSS **193,904,640 bytes (184.92 MiB)** and zero swaps. Its temporary browser and data were removed on exit. The same measurement limits apply.

## Limits of this evidence

- No live paid model call was made. The hosted integration is implemented and tested with a simulated HTTP provider; model/account compatibility and real-model task quality still need a configured credential.
- Canary checks demonstrate the tested privacy cases. They are not a measured recall rate for arbitrary names, identifiers, screenshots or financial documents, and are not proof that all private content will be detected.
- Browser tests validate the application/model boundary. They do not assert that Chromium or an arbitrary destination website produces no network traffic. Entering a value can disclose it to that website immediately.
- Automated tests use supported standard forms. Production tax portals, CAPTCHAs, embedded frames and custom controls are outside the demonstrated scope. No real return was prepared or filed.
- Browser-owned cache/cookies and operating-system memory/backups are outside vault encryption. No forensic-erasure or protection-from-local-malware claim is made.

## Differences from the original design baseline

| Design item | Delivered behavior |
|---|---|
| Stock Browser Use agent loop | Browser Use browser runtime with a product-owned restricted loop and one model gateway. No unrestricted tool or agent feature-parity claim. |
| Keychain unlock integration | Scrypt-derived passphrase key and AES-GCM local storage. No key stored beside ciphertext; no recovery account. |
| Selective screenshot OCR/face masks | Text-only by default. Optional outbound screenshots are fully black; selective visual reasoning is deferred. Document OCR is implemented separately. |
| Extension UI framework | Native Manifest V3 HTML/CSS/JavaScript side panel; dashboard uses React and TypeScript. |
| Reconnectable events | Authenticated polling of companion-owned state; closing the UI does not own or terminate the task. |
| Multi-person financial context | One user's reviewed records with explicit document/calculation scope; ownership, currency and period require user review. |
| Packaging | Locked source checkout, local setup/start/verification scripts. No signed installer or Chrome Web Store release. |

These constraints are visible in the product and in the README. Version 0.1 is a usable supervised form-assistant prototype, not an unattended tax-filing product.

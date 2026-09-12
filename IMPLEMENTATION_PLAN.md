# Dev Privacy Guard — implementation plan

Status: historical design baseline. The revised v0.2 Browser Use agent, reviewed screenshot and Whisper scope is documented in [docs/IMPLEMENTATION_V0_2.md](docs/IMPLEMENTATION_V0_2.md). See README.md, setup.md and docs/VALIDATION.md for the delivered scope and verification. Browser-local CV is deliberately deferred; audio transcription uses the unredacted recording.

## 1. Product and outcome

Build a Chrome extension and a local dashboard around the open-source Browser Use agent. The user starts a task in the extension, supplies documents or saved profile information, and supervises progress. Local processing extracts information, replaces private values with typed references, and sanitizes browser observations. A remote model proposes actions using those references. The local executor validates each action and resolves references immediately before entering values into the intended website.

The privacy boundary is the remote reasoning service: original documents, raw observations, private values, and reference mappings stay on the device. Approved values may be disclosed to the destination website when entered, even before submission. Automatic redaction is fallible; the prototype must measure misses and use review and blocking behavior rather than claim an absolute guarantee.

**First complete demonstration:** start from the extension on a synthetic application form → use a confirmed local profile and an uploaded sample statement → review extracted facts → approve sanitized model context → let Browser Use fill fields through references → redact the resulting page again → review the completed form. Final submission is a distinct approval.

## 2. Prototype scope and assumptions

- Initial platform: the user's M1 MacBook Air with 8 GB RAM; one active task and one unlocked user vault.
- Browser: a dedicated local Chrome for Testing/compatible Chromium instance with a nondefault profile and the extension loaded. Prove compatibility in the first milestone before standardizing the distribution.
- The user starts tasks from tabs inside that controlled browser. Ordinary existing browser windows are not silently attached, copied, or substituted. A failed connection produces setup instructions.
- Initial automation: supported single-tab forms with indexed DOM targets. Navigation within an approved origin is supported; redirects outside the approved destination pause the task.
- Inputs: confirmed profile fields, text PDFs, and PNG/JPEG screenshots. Add scanned-PDF OCR after the text-PDF path works. Use synthetic documents during development.
- Initial fields: applicant name, email, phone, address, identity field examples, statement period, and reviewed decimal totals.
- Initial reasoning: mock model first, then one configurable hosted provider accessed exclusively through the privacy gateway. No provider or model performance claim is assumed.
- Manual review of model-bound payloads is on by default. Explicit destination disclosure approval precedes releasing private values. Final submission has its own approval.
- Deferred: real ITR preparation/filing, autonomous tax classification, unrestricted JavaScript, arbitrary file uploads to websites, cross-browser support, multiple users/tasks, cloud vault sync, continuous screen capture, and fully local large-model reasoning.

The architecture can expand to these features, but they are not prototype acceptance criteria.

## 3. Architecture and trust boundaries

```text
LOCAL DEVICE
  Extension (task, selected tab, progress, questions)
  Dashboard (vault, uploads, review, approvals)
           │ authenticated loopback API / event stream
           ▼
  Companion and task state machine
      ├── Vault / task-scoped reference registry
      ├── Document extraction / review / local calculations
      └── Browser Use integration
               │ raw browser state, local-only execution mappings
               ▼
          Observation and text sanitizer
               │ independent sanitized presentation copy
               ▼
          Model gateway + exact-payload review
               │ approved sanitized messages only
=============== REMOTE REASONING BOUNDARY ===============
               ▼
          Hosted model
               │ proposed typed action, references only for private input
=============== LOCAL EXECUTION BOUNDARY ================
               ▼
          Action policy + local reference resolution
               ▼
          Browser Use / CDP → intended website
               │ fresh observation
               └── repeat sanitization and reasoning
```

The extension does not run Python or the heavy processing pipeline. The companion owns task state and survives closing the extension panel. The dashboard and companion stay local; neither is publicly hosted. The raw execution map is never replaced with a redacted map. We sanitize a separate presentation copy and preserve element identities.

## 4. Proposed technology choices

| Area | Baseline | Why |
|---|---|---|
| Extension | Manifest V3, TypeScript, React, side panel | A persistent task interface alongside the website |
| Dashboard | React + TypeScript, bundled and served locally | Shared UI components; local document and profile review |
| Companion | Python 3.12, FastAPI, asyncio, uv | Runs Browser Use in the same process family; typed APIs |
| Schemas | Pydantic + generated OpenAPI/TypeScript types | One contract across Python and browser interfaces |
| Agent | Pinned Browser Use source/dependency + small isolated adapter | Reuse automation while controlling its boundaries |
| Storage | SQLite metadata and encrypted record/document payloads | Local persistence without an external database |
| Keys | macOS Keychain-backed key storage, explicit vault unlock | Keys are not saved beside the encrypted data |
| Extraction | PDF text parser, bounded PDF renderer, local Tesseract OCR | Incremental document support with local processing |
| Screenshot redaction | Local image operations + OCR boxes + a benchmarked face detector | Mask actual pixels before transmission |
| Testing | Python tests, browser integration fixtures, frontend checks | Verify privacy and execution, not only component rendering |

Select and pin exact OCR/renderer/face-detector packages after a short Apple Silicon compatibility and licensing check. Bundle required assets and avoid runtime CDN dependencies. Start with CPU processing and bounded worker concurrency; acceleration is an optimization after measurements.

## 5. Shared contracts — define before module development

These are product-owned interfaces, not claims that Browser Use natively accepts these exact objects.

### Task and observation

- `TaskRequest`: task ID, raw local goal, controlled browser ID, extension tab ID, bound CDP target ID, approved destinations, attached document IDs, selected profile subject.
- `TaskState`: status, generation/cancellation token, current step, pending question/approval ID, last event sequence, and a sanitized status message.
- `RawObservation` (local-only): target/frame IDs, page epoch, viewport, screenshot dimensions, DOM/AX state, raw screenshot, and execution map.
- `SanitizedObservation`: observation ID, preserved element indexes, sanitized page text/metadata, optional masked image, and a redaction report.

### Facts and references

- `CandidateFact`: subject, scope, field type, local value, source document/page/box, period/currency where relevant, confidence, and review state.
- `VaultRecord`: record ID/version, encrypted value, provenance, review state, retention settings, and subject/document/task scope.
- `ValueReference`: opaque task-scoped ID, public semantic label, type, and availability. Its binding to a vault record/version stays local.
- Semantic labels must themselves be sanitized. Do not encode a person's identity, account number, original filename, or private value into a reference ID.

Example model-visible catalog:

```json
{
  "references": [
    {"id": "ref_7", "label": "Applicant full name", "type": "person_name"},
    {"id": "ref_8", "label": "Reviewed statement total", "type": "money"}
  ]
}
```

Example custom action proposal:

```json
{
  "action": "input_ref",
  "element_index": 12,
  "value_ref": "ref_7"
}
```

The trusted service adds the task, browser target, observation epoch, permitted destination, and reference version to the execution envelope. These are checked against current state rather than trusted merely because the model supplied them.

### Approvals, execution, and audit

- `ModelSendApproval`: task, hash of the complete prepared outgoing payload including images, destination provider, expiry. Changed payloads need a new approval.
- `DisclosureApproval`: origin/frame, named references and record versions, permitted use/fields, scope and expiry. It authorizes entering data, not final submission.
- `ActionApproval`: exact consequential action, current target/observation binding, generation and expiry. Navigation or material target changes invalidate it.
- `ExecutionResult`: `success`, `missing_reference`, `review_required`, `stale_target`, `page_changed`, `blocked`, `cancelled`, `failed`, or `outcome_unknown`; include safe messages, never resolved values.
- `AuditEvent`: task, sequence, stage, safe outcome, latency, reference IDs, approval ID. Keep no raw private values in ordinary logs.

## 6. Modules and responsibilities

### M1 — Browser extension

**Owns:** task entry, selected-tab identity, companion connection, live progress, missing-input notifications, Pause/Resume/Stop, and links into the dashboard.

**Implementation:** use a side panel; background worker handles browser events and reconnection, not the long-running agent loop. Pair with the local service and store only minimal session credentials and task IDs in extension storage. Detailed documents and profile editing stay in the dashboard.

For the prototype, evaluate `chrome.debugger.getTargets()` to map extension `tabId` to a CDP target ID. This requires declaring the debugger permission; the extension will use discovery, not attach a second debugger. The mapping must be verified against the controlled browser. Never identify a target by URL/title alone.

**Contract:** calls task/control endpoints and consumes safe task events. No direct model API calls and no general-purpose vault export endpoint.

**Independent development:** fake companion with scripted states and questions.

**Acceptance:** a task is bound to the clicked tab even when another tab has the same URL; closing/reopening the panel restores progress; losing the companion shows a connection error; Stop is acknowledged by the service.

### M2 — Local dashboard

**Owns:** profile, document library/intake, extraction review, task detail, sanitized-message preview, disclosure/action approvals, vault lock, and delete controls.

**Screens:** overview; profile; documents and candidate facts; task timeline; approvals/actual sanitized requests; connection/settings.

Separate reusable profile updates from document/task facts. Show extraction source and conflicts. Default sensitive values to masked display and reveal them only within the local interface. Render document/model text as untrusted content, not executable HTML.

**Contract:** authenticated local APIs; review and approval objects with IDs/versions. Approval buttons use the trusted service's proposal, not model-provided HTML or URLs.

**Independent development:** fixture APIs for uploads, conflicts, pending approvals, completed and failed tasks.

**Acceptance:** edit/confirm/reject a candidate; preserve the previous confirmed address on conflict; inspect the exact sanitized payload; approve or deny; delete an original and explicitly choose what happens to derived facts; lock the vault.

### M3 — Companion, local bridge, and task lifecycle

**Owns:** local server, pairing/authentication, task state, event stream, cancellation, serialized execution, component startup/shutdown, and recovery.

Bind only to loopback. Validate Host and exact permitted Origins; require paired credentials, authenticate event streams, protect mutating requests against CSRF, and rate-limit pairing. Do not pass credentials in URLs. A reachable localhost port is not an authentication mechanism. The extension and dashboard use narrow APIs; ordinary webpages cannot approve actions or read vault records.

State machine:

```text
created → checking_connection → waiting_for_unlock/input/review
        → observing → sanitizing → awaiting_model_approval → reasoning
        → validating_action → awaiting_disclosure/action_approval
        → executing → observing ... → completed

Any active state → paused / blocked / failed / stopping → stopped
Uncertain consequential execution → outcome_unknown → user verification
```

Persist safe checkpoints, not raw screenshot blobs or resolved action values. On restart, require unlock and fresh observation before resuming; never replay the last action blindly. Vault lock revokes further reference resolution and pauses active work.

**Independent development:** fake browser, fake gateway, real state/event handling.

**Acceptance:** unauthorized requests fail; duplicate control commands are idempotent; the task survives UI disconnect; cancellation invalidates pending work immediately; a service restart cannot repeat a submission.

### M4 — Browser Use integration and browser connection

**Owns:** pinned dependency, dedicated browser launcher/profile, CDP connection, exact target binding, raw observations, action registration, and the adapter around Browser Use's loop.

Reviewed source baseline: `e25ab65e699af3031a1f2d348526de2844be0e89` (package version `0.13.10`). Pin the reviewed version for the prototype; changing it triggers compatibility and egress tests.

Do not duplicate Browser Use's DOM analysis, indexed targets, retry primitives, and CDP actions. Introduce a small explicit integration seam before browser state becomes model messages, plus an execution seam before actual browser effects. Keep raw execution mappings local.

Prototype configuration: one action per step; disable automatic task-URL opening; no optional cloud browser/profile sync; one controlled tab; configurable step limit (start at 20) and bounded retries. Disable auxiliary model features until they are routed and tested through M8. Block the dashboard, extension pages, and local companion endpoints as agent destinations.

Disable unsanitized screenshot/history persistence, conversation dumps, GIF generation, and external tracing. Browser Use's history path can persist screenshots even when vision is disabled; intercept or replace that persistence explicitly. Retain only sanitized history, and store any intentionally retained sensitive task artifacts through the encrypted vault. Raw observations remain transient local processing data.

**Independent development:** synthetic local form and mock model; no real personal data or paid API required.

**Acceptance:** bind the correct duplicate-URL tab; handle navigation/stale nodes; demonstrate no implicit pre-policy navigation; capture state without remotely transmitting it; verify source hooks still work at the pinned revision.

### M5 — Encrypted vault and reference registry

**Owns:** local profile, encrypted originals/facts, provenance/versioning, task reference issuance, lookup permissions, unlock/lock, retention, and deletion semantics.

Use authenticated encryption with a fresh nonce per encryption and key material in the OS credential store. Encrypt private record contents and document bytes; keep any plaintext metadata minimal and non-sensitive. Avoid keys in `.env`, reference labels, logs, or extension storage. API credentials also stay in the local credential store.

References are opaque, scoped to the current task and subject, version-bound, and usable only for allowed field types/destinations. Only M9 and approved local computation handlers can resolve them for execution; M8 never resolves values. A model cannot request arbitrary database keys.

Confirmed profile facts persist. Document-specific values have explicit periods/scopes. Raw observations and OCR intermediates are short lived. Delete controls distinguish originals, derived records, and task traces; do not promise forensic secure erasure from SSDs or system backups.

**Independent development:** synthetic records and references, no browser needed.

**Acceptance:** no plaintext synthetic secrets in application persistence/logs; correct locked behavior; unknown/cross-task/stale references rejected; confirmed values cannot be silently overwritten; losing access to the key fails safely with clear recovery limits.

### M6 — Documents, extraction, review, and local computations

**Owns:** bounded local intake, PDF text/OCR, structured candidate facts, provenance, validation, duplicate/conflict detection, and narrow arithmetic tools.

Check file signatures, sizes, page counts, pixel counts and timeouts. Process documents in a bounded worker; do not fetch external resources embedded in documents or execute active content. Retain originals encrypted and minimize plaintext temporary files. Extraction failures produce no approved facts.

First extract known fields from supported samples. Identity numbers, bank details, financial totals, and ambiguous/conflicting values require confirmation in the prototype. Additional facts become proposed profile updates; uploading a statement does not automatically change the user's profile.

Local computations use decimal arithmetic, explicit currency/period, reviewed records, and defined duplicate handling. They return result references and provenance. Unsupported tax judgments and uncertain transaction categories go to the user. Do not send hidden operands or derived totals to the remote model by accident.

**Independent development:** labeled synthetic PDFs/images and expected facts; no browser/model needed.

**Acceptance:** text PDF and screenshot paths work; poor OCR asks for review; statements for different people/periods stay separate; calculation fixtures match exact results; oversized/damaged documents fail within limits.

### M7 — Privacy detection and observation sanitization

**Owns:** local task/goal sanitization, DOM/text PII detection, screenshot masking, sanitized browser-state construction, and redaction reports.

Combine known-vault-value matching, credential/field rules, structured patterns/checksums, text-node detection, OCR regions, and face detection. Regex alone is insufficient for free-text identities. Any unsupported text/visual regions are omitted, conservatively masked, or block the affected model request. Do not assume that an absence of `<img>` means no sensitive pixels.

Cover goals, page text, attributes, URLs/query strings, tab titles, reference labels, history, tool results and error strings. For screenshots, map DOM/frame/OCR boxes into actual screenshot pixels using viewport, scrolling, zoom and scale. Apply solid pixel overwrite before resizing/encoding and preserve a clear report of the masked areas. Never mutate the live page to achieve model redaction.

Keep the true local URL and target metadata separately for destination enforcement. The model sees their sanitized presentation. Any image decode, redaction or encoding error blocks that image; an exception must never fall back to the original unredacted screenshot.

Begin with a protected text-only interaction. Enable image transmission only after image detection and geometry checks pass. Capture still happens locally where Browser Use requires it; `use_vision=False` is not a no-capture guarantee.

**Contract:** `RawObservation → SanitizedObservation + RedactionReport`, with an explicit success/incomplete/block status. Failed detection cannot return a supposedly safe image.

**Independent development:** raw fixture observations, screenshots and hand-labeled boxes.

**Acceptance:** screenshots at supported zoom/Retina scales are masked correctly; filled values are removed from subsequent observations; text on supported non-input nodes is covered; incomplete processing blocks sending; both missed and excessive redaction are measured.

### M8 — Model gateway and sanitized request inspection

**Owns:** the only approved route to model providers, all-role model adapters, exact outgoing payload construction, final checks, manual send approval, schema validation, limits, and sanitized request inspection.

Implement Browser Use's `BaseChatModel` protocol including structured outputs/return metadata. Wrap every configured model role: main reasoning, extraction, compaction, judge, fallback, and any summary/auxiliary path. Disabled paths stay disabled until covered. Provider retries must resend only approved immutable payloads.

Accept only sanitized text/image parts and explicitly permitted metadata. Reject raw documents, arbitrary image URLs/attachments, unknown content parts, and unexpected payload fields. Inspect the final serialized request, including dynamic schemas/tool descriptions, rather than only the first prompt string. Bind approval to the complete prepared payload; downstream adapters must not append unseen private content.

Disable Browser Use cloud synchronization and telemetry before initialization and inspect optional tracing/provider integrations for separate egress. Initial environment includes `ANONYMIZED_TELEMETRY=false` and `BROWSER_USE_CLOUD_SYNC=false`; these settings are not a substitute for tests.

Requests and model outputs displayed in the dashboard remain sanitized. Set call deadlines, token/step limits, and cost reporting; route uncertainty and provider errors back as safe task events.

**Independent development:** mock provider recording its final serialized requests; canned valid/invalid model responses.

**Acceptance:** synthetic private values are absent from actual outgoing text and images for every enabled call path; malformed output fails validation; additional unapproved payload content cannot be sent; provider timeouts do not trigger uncontrolled action execution.

### M9 — Action policy, local remapping, and safe execution

**Owns:** tool allowlist, structured proposal validation, reference-use authorization, disclosure/submission approvals, target freshness, just-in-time resolution, actual action dispatch, and safe results.

Register custom `input_ref` and `select_ref` tools; remove or gate the original literal-input paths. Keep references in model actions and history. Resolve actual values only inside trusted execution, after authorization. Do not place resolved values into Browser Use's general action objects because those can be logged before execution.

Initial allowed operations: inspect supported dropdown choices, click a validated target, enter/select an approved reference, scroll, wait, request missing information, supported local calculations, and finish. Disable arbitrary JS, unrestricted extraction, file read/write/upload, key combinations, cross-tab actions, and direct browser access from unreviewed custom tools. Add them individually only after privacy and action-policy coverage.

Validate task generation, target ID/frame/origin, observation freshness, field compatibility, reference scope/version and approval. Model-produced literal private values and guessed references are rejected. Supported public constants may be used only through a constrained validated path.

Clicks can submit even without a tool named `submit`. On supported forms, classify/gate buttons and other potential submitting controls; unknown consequential behavior requires review. Enter-key submission and automatic retries cannot bypass this policy. Revalidate after approval and immediately before CDP dispatch.

Stop revokes execution authorization, cancels pending work and prevents new dispatches. It cannot undo a click/type already delivered to the browser. Report uncertain in-flight outcomes and require verification before retrying consequential actions.

**Independent development:** mock vault + local form + scripted proposals.

**Acceptance:** resolve the correct field without logging the value; reject wrong-domain/type/task/stale references; invalidate approval after navigation/value edits; stop prevents future dispatch; a timed-out submission is never automatically repeated.

### M10 — Verification, benchmarks, and developer packaging

**Owns:** labeled fixtures, privacy regression harness, browser integration scenarios, measured demo evidence, local setup/run scripts, and documented limitations.

Create a realistic local application form, a transaction-review screen, and synthetic documents with unique canary secrets. Run a mock provider before live integration. Inspect network egress and actual model request bodies, application storage and logs, as well as browser results. Ground-truth image checks are needed in addition to text searches.

Supply a local startup command, companion health check, browser/extension setup instructions, dependency locks and troubleshooting. A production signed installer/store release is later work. Run from a fresh profile/vault as part of release verification.

**Acceptance:** the end-to-end demo passes from a clean setup; the connection limitations and privacy metrics are explicit; no required flow secretly depends on a paid/cloud browser service; memory and timing are measured on the user's device.

## 7. Local API outline

Version all endpoints under `/api/v1`. Exact routes can change before implementation; schemas and authorization must be agreed first.

| API group | Operations | Restrictions |
|---|---|---|
| Health and pairing | Health, initiate/complete pairing, revoke session | Health contains no private state; pairing expires and is rate-limited |
| Browser | Connection status, verify target binding | Only the registered local browser; no arbitrary CDP URLs from a page |
| Tasks | Create, read safe state, pause/resume/stop, answer question | Authenticated task owner; idempotency keys for mutations |
| Events | Reconnectable task stream with event sequence | Authenticated, sanitized events only |
| Vault | Unlock/lock, locally view/edit records, delete | Dashboard scope; no bulk secret export for extension/model |
| Documents | Upload, processing state, review candidates, delete | Size/type limits; local storage only |
| Approvals | Read proposal, approve/deny exact ID and version | Fresh authenticated user action; immutable binding |
| Inspection | Read sanitized requests and redaction reports | Do not expose raw debugging dumps |

## 8. Build order and milestone gates

### Phase 0 — Contracts and integration proof

Define the shared contracts and threat boundaries. Pin Browser Use. Build the smallest disposable connection test: local browser + minimal extension tab discovery + mock model + a public synthetic field. Confirm exact-tab binding, the state sanitization seam, model invocation routing, and action gating. No personal data yet.

**Exit gate:** duplicate-URL tabs cannot be confused; the selected field can be filled; auxiliary calls and pre-policy navigation are accounted for. If browser attachment or extension loading fails, resolve that setup before broad UI work.

### Phase 1 — First protected field fill

Implement M3's task skeleton, M5's vault/reference slice, M7's text sanitization, M8's recording mock gateway, and M9's `input_ref`. Use minimal M1/M2 screens for a task, one reviewed fact, preview, approval, and Stop.

**Exit gate:** private value stays out of model-bound requests, resolves into the intended field, and disappears from the next model-visible observation. Lock and Stop prevent additional releases. This is the first privacy integration milestone.

### Phase 2 — Usable extension and dashboard

Complete M1/M2 against the contracts: pairing, task status, profile editing, questions, reconnect, redaction inspection and approval screens. Add durable task checkpoints and bridge authentication tests in M3.

**Exit gate:** start/supervise a multi-step mock task entirely through the UI; recover from UI disconnect without restarting or duplicating actions.

### Phase 3 — Document intake and profile growth

Implement M6's text PDF and screenshot OCR paths, candidate review, document/subject/period scoping and one local decimal aggregation. Integrate proposed profile updates with M5 and the dashboard. Add bounded scanned-PDF support once rendering is verified.

**Exit gate:** upload a synthetic statement, correct an extraction, confirm a computed result, issue its reference, and fill it without transmitting the operands or result value to the model.

### Phase 4 — Visual privacy and live reasoning

Complete M7's supported screenshot OCR/face masking and image geometry tests. Connect one real provider through M8 after the recorded-request tests pass. Keep one action per step, narrow tools and manual review.

**Exit gate:** a real model selects references across a supported multi-field task; outgoing screenshots are masked; every post-action observation is sanitized again; the preview matches the actual outgoing request.

### Phase 5 — Failure handling, evaluation, and packaging

Exercise stale nodes, navigation, denied/expired approvals, malicious page text, conflicting documents, OCR failure, lost connections, vault lock, cancellation and provider timeouts. Package the startup flow and collect device measurements through M10.

**Exit gate:** repeatable clean-start demonstration, passing privacy/action regression suite, and a written supported-feature/known-limitations report. No automatic real-world financial filing.

## 9. Parallel work allocation

Team size is not yet specified. The following four workstreams are an allocation option, not an assumption about actual people or a fixed deadline.

| Workstream | Primary modules | Can work independently with |
|---|---|---|
| Interface | M1, M2 | Mock task/events/profile/review APIs |
| Browser and runtime | M3, M4, M9 | Synthetic form, mock vault and model |
| Local data | M5, M6 | Labeled documents, reference contracts |
| Privacy and evaluation | M7, M8, M10 | Raw observation fixtures and recording provider |

Assign one integration owner for contracts and the complete end-to-end path. Each stream owns its tests; M10 coordinates system-level evidence. Everyone integrates to Phase 1 before independently expanding features. With fewer people, follow the same phases sequentially.

Critical dependency: contracts → exact browser binding → protected field fill → documents and visual coverage → live provider → hardened demo. UI polish can proceed in parallel; it should not hide unresolved connection or privacy behavior.

Estimate effort after Phase 0 using the actual team, portal and document samples. The largest schedule uncertainties are exact browser attachment, screenshot geometry/coverage, and document variation. Do not commit a delivery date before measuring those.

## 10. Verification checklist

1. **Browser identity:** same-URL duplicate tabs, tab closure, navigation and browser restart cannot silently retarget work.
2. **Storage:** synthetic private values are absent from persisted DB contents, plaintext document copies, logs and task traces; keys are not beside ciphertext. Temporary processing files have a bounded lifecycle.
3. **Vault lock:** locking prevents subsequent reference resolution; restart requires unlock and fresh state.
4. **Record scope:** two subjects, periods and conflicting addresses cannot cross-contaminate records or silently update a profile.
5. **Document limits:** corrupt files, oversized pages/images and timeouts produce recoverable failure with no approved facts.
6. **Actual egress:** inspect every enabled provider role, retries and error paths after serialization; no canary secret in text, metadata or screenshot pixels. Unknown content parts are rejected.
7. **Follow-up privacy:** after private input, trigger another observation, tool error and history processing; the secret remains absent remotely.
8. **Image coordinates:** supported zoom, Retina scale, scrolling, frame positions and edge crops are tested against ground truth. Unsupported regions are blocked or conservatively masked.
9. **Action integrity:** reject unknown/cross-task/stale references, wrong field types, unauthorized origins, and mutated approvals.
10. **Bridge authentication:** unrelated pages, unauthenticated local requests, cross-origin forms and event connections cannot access private APIs or approve work.
11. **Calculation correctness:** decimal totals, explicit currency/period, duplicate handling and uncertain classifications behave as specified; only references reach the model.
12. **Cancellation and replay:** stop/pause/lock have distinct behavior; no new dispatch after revocation; an uncertain final action is never automatically repeated.
13. **Prompt injection:** malicious instructions in pages/documents cannot obtain vault reads, activate excluded tools or approve their own disclosure.
14. **Dependency regression:** pinned Browser Use integration tests detect a moved hook, new model path, changed action type or added egress before an upgrade is accepted.

Record task completion, correction frequency, missed/excessive redaction by category, pixel/region coverage, stage latency, model calls/tokens, and peak memory on the M1. Zero observed canary leaks in the fixture suite is required; it is not proof of universal zero leakage. Establish accuracy/latency targets from Phase 0/1 measurements and freeze the demo's supported cases before expanding coverage.

## 11. Proposed repository layout

```text
apps/
  extension/           # M1, MV3 package
  dashboard/           # M2, local UI
services/
  companion/
    api/               # M3, pairing, tasks, events, approvals
    agent/             # M4, Browser Use adapter
    vault/             # M5
    documents/         # M6
    privacy/           # M7
    model_gateway/     # M8
    execution/         # M9
packages/
  contracts/           # schemas and generated browser types
  ui/                  # shared presentation components
tests/
  fixtures/            # synthetic documents, observations, labeled masks
  integration/
  privacy/
  e2e/
demo/                  # synthetic application and transaction pages
scripts/               # local setup, launch, verify
docs/                  # architecture, supported cases, benchmark reports
```

User vaults, credentials, browser profiles, original private documents and task artifacts live outside the repository. Tests only check in synthetic fixtures. This layout is a proposal; the plan does not create these application directories yet.

## 12. Source-backed integration notes

- **Agent context and execution:** at the reviewed revision, `_prepare_context()` captures a screenshot before messages/compaction, and the agent logs proposed actions before tool execution. Sanitizing only its return value or substituting real values into model action objects is too late. Use explicit pre-message and execution seams. [Agent source](https://github.com/browser-use/browser-use/blob/e25ab65e699af3031a1f2d348526de2844be0e89/browser_use/agent/service.py)
- **Model adapter:** Browser Use exposes `BaseChatModel.ainvoke`; the adapter must preserve typed structured output and provider metadata. [Model interface](https://github.com/browser-use/browser-use/blob/e25ab65e699af3031a1f2d348526de2844be0e89/browser_use/llm/base.py)
- **Known-secret handling:** the built-in text replacement and secret substitution are useful features, but not a comprehensive screenshot detector or our field-bound reference authorization model. [Messages](https://github.com/browser-use/browser-use/blob/e25ab65e699af3031a1f2d348526de2844be0e89/browser_use/agent/message_manager/service.py), [registry](https://github.com/browser-use/browser-use/blob/e25ab65e699af3031a1f2d348526de2844be0e89/browser_use/tools/registry/service.py)
- **Additional egress:** configuration includes telemetry and cloud synchronization; authenticated sync sends events to a remote endpoint. Disable and test these separately from model sanitization. [Configuration](https://github.com/browser-use/browser-use/blob/e25ab65e699af3031a1f2d348526de2844be0e89/browser_use/config.py), [sync implementation](https://github.com/browser-use/browser-use/blob/e25ab65e699af3031a1f2d348526de2844be0e89/browser_use/sync/service.py)
- **Browser interface:** Chrome provides a side-panel API and debugger target discovery with extension tab identity. Treat the exact mapping/connection as an integration test, not an assumption. [Side panel](https://developer.chrome.com/docs/extensions/reference/api/sidePanel), [debugger API](https://developer.chrome.com/docs/extensions/reference/api/debugger)
- **Dedicated browser profile:** Chrome changed remote-debugging behavior for its default data directory; use an explicit nondefault profile and verify the selected browser distribution. [Chrome remote debugging changes](https://developer.chrome.com/blog/remote-debugging-port)
- **Storage primitive:** use maintained authenticated-encryption APIs correctly, with nonce discipline and external key storage, rather than writing a custom cipher. [Authenticated encryption documentation](https://cryptography.io/en/latest/hazmat/primitives/aead/)

# Dev Privacy Guard — Full Product Technical Plan

**Purpose:** Give a teammate the intended product architecture and implementation roadmap, including the browser-local vision work deferred from the hackathon build.

**Status:** Target design, not a claim that all features are implemented. Consolidated on 10 September 2026 from the original plan and subsequent product discussions. Model names below are candidates from those discussions; exact checkpoints, runtime compatibility and performance still need validation.

## 1. What we want to build

Dev Privacy Guard is a privacy-preserving browser agent. A user gives it a goal, such as “open this website and complete the application using my profile.” It opens the required pages, understands their structure, fills information it already has, asks for missing details or documents, and resumes the task.

The central idea is to split perception and reasoning:

- **On the device, including real inference inside the browser:** understand screen state, identify sensitive information, redact images and text, retain private records, and execute approved actions.
- **On the reasoning server:** interpret sanitized context, plan the next step, and return structured actions or questions.
- **Under user control:** review screenshots before transmission, confirm extracted information, authorize private disclosures, and approve consequential actions.

The local vision layer must actually influence screen understanding, redaction or escalation decisions. The complete product also includes change-aware observation, reusable local visual context, and measured evaluation of accuracy, resource use and latency.

Browser Use remains a reusable automation foundation. Our contribution is the browser-local perception and privacy pipeline, safe context sharing, private-value references, document/profile workflow, and evaluation system.

## 2. Intended user journey

1. The user enters a task and optional URL in the extension. They can select reviewed profile facts and documents.
2. The agent opens the URL, or finds the destination when no URL is supplied. The user does not need to prepare a tab beforehand.
3. The local observer collects page structure and screen state. Local detectors identify sensitive content and useful UI regions.
4. The agent receives sanitized context and discovers the relevant form fields or next navigation action.
5. Approved profile values are entered through local references. Subsequent observations redact those filled values again.
6. If information is missing or the page needs visual interpretation, the task pauses at a visual checkpoint. The dashboard displays the original locally beside the actual sanitized image and text proposed for transmission.
7. After approval, the server interprets that sanitized context and returns the missing-information request or next action.
8. The user uploads a document or enters the missing details. Extraction happens locally; candidates are reviewed before use. Saving reusable facts to the profile is an explicit choice.
9. The same task resumes with refreshed references and a fresh page observation.
10. The agent verifies the completed fields and stops for final review. Submission, purchase or another consequential action requires separate authorization.

A screen-analysis mode should also summarize or explain the current tab without requiring automation/CDP setup. Form filling is the first complete workflow; broader browser assistance is the product direction. Real tax preparation and filing require additional domain-specific validation.

## 3. Architecture

```text
USER DEVICE
  Browser extension
    Task entry, selected tab, controls, progress
    Content script: DOM / ARIA / geometry / change signals
    Browser worker or supported extension inference context:
      local CV + OCR + face detection + PII rules
      local feature cache + redaction map + pixel masking
                |
  Authenticated local companion and dashboard
    Task orchestration / Browser Use adapter
    Encrypted profile and documents / reference registry
    Extraction review / screenshot review / approvals
    Outbound gateway / local action validation / metrics
                |
                | Approved sanitized context only
================ REMOTE REASONING BOUNDARY ================
  Reasoning endpoint / hosted LLM or VLM
    Interpret sanitized screen and field descriptions
    Return structured action, question or result
================ LOCAL EXECUTION BOUNDARY ================
  Validate target, permissions, references and page version
    Resolve private values locally
    Execute through guarded Browser Use / CDP tools
    Observe and verify the result
```

The extension owns browser-side perception. The companion owns durable task orchestration and private storage. Heavy local document processing can remain in Python, but Python-only image processing does not fulfill the browser-local vision part of the intended architecture.

There are two separate data destinations: the reasoning provider receives sanitized context; the target website receives the values the user authorizes us to enter. Entering a value can disclose it to that website before submission.

## 4. Main modules

| Module | Responsibility | Existing foundation / required work |
|---|---|---|
| Extension and dashboard | Task entry, controls, profile/documents, image review and progress | Extend the existing interfaces with perception status and evaluation views. |
| Browser observer | DOM/ARIA, visible controls, geometry, screenshot binding and page changes | Extend current guarded observations with browser-side capture and change tracking. |
| Local vision pipeline | Real browser inference for UI/visual understanding, faces and image text | New core work; currently deferred. |
| Privacy engine | Combine detections, sanitize text, mask actual pixels and validate outgoing artifacts | Extend existing DOM/pattern masking and manual review with detector outputs. |
| Change detection and cache | Reuse valid local results and avoid unnecessary inference/model calls | New explicit subsystem. |
| Agent and gateway | Reasoning loop, sanitized requests, budgets, retry policy and structured actions | Reuse the current Browser Use integration and guarded transport. |
| Vault and document processing | Encrypted records, reviewed extraction, provenance, local calculations and references | Reuse and extend existing modules. |
| Local executor | Target validation, navigation, reference input, action permissions and verification | Extend supported tools and browser workflows incrementally. |
| Evaluation | Ground truth, automatic detector scores, latency, resources and cache measurements | Build a reproducible benchmark and dashboard. |

## 5. Local perception and model strategy

No single model should be responsible for every kind of PII. Use multiple sources of evidence with a shared output format.

| Component | Intended role | Selection decision |
|---|---|---|
| DOM and ARIA rules | Password/credential fields, field labels, values and exact element geometry | Baseline deterministic path. |
| Pattern and checksum rules | Structured identifiers, email, phone and token-like strings | Combine with known private values from the local vault. |
| MobileViT or equivalent lightweight CV backbone | Screen/UI region understanding and reusable visual features | Candidate browser-local backbone; benchmark an appropriate checkpoint and task-specific head. A classification backbone alone does not produce PII boxes. |
| Lightweight local VLM, such as SmolVLM | Optional interpretation of ambiguous local visual regions | Evaluate only after the baseline pipeline fits the memory/latency budget. |
| LFM2.5-VL 1.6B | Alternative VLM candidate from the earlier discussion | Decide its role and deployment location after compatibility/resource testing; do not assume it must run beside every other model. |
| OCR | Recover text and boxes from images, canvas and scanned content | Evaluate the proposed PP-OCRv6 checkpoint if available and suitable; retain a practical local fallback such as Tesseract. Browser and document OCR can use different implementations. |
| Face detector | Locate faces for masking | Evaluate MediaPipe first; TinyFaceDetector or SSD MobileNet V1 are fallback candidates. |
| Optional NER | Names, addresses and other context-dependent entities | Add if it measurably improves recall within the resource budget. |
| Optional SAM-based segmentation | Refine suitable visual masks | An optional refinement tool, not the reasoning model or a universal PII detector. |

Target browser runtimes are ONNX Runtime Web and/or Transformers.js with WebGPU where supported and a measured WASM fallback. Validate model operators, conversion, licensing and extension deployment before committing to a stack. Bundle or explicitly install model assets; inference must not depend on transmitting raw inputs elsewhere.

Start with pretrained components. If the visual task needs adaptation, label representative UI screenshots and fine-tune a small detection/classification head or suitable model. Keep train and test pages separate. A custom training claim requires actual training artifacts and held-out evaluation.

On the initial M1 MacBook Air with 8 GB RAM, use one active task, bounded image resolution, limited workers and on-demand model loading. Establish measured budgets before selecting multiple resident models.

## 6. Detection and redaction pipeline

1. Bind each observation to an exact tab/frame, page version, viewport, scroll position and device pixel ratio.
2. Collect bounded DOM/ARIA information and a corresponding screenshot. If the page changes during collection, retry or block sharing.
3. Run credential/PII rules and known-value matching over all proposed text context.
4. Run local CV and face detection where required. Use OCR for visible text not recoverable from DOM.
5. Map all findings into screenshot coordinates and merge overlapping detections.
6. Apply confidence-aware policy and construct one redaction map.
7. Mask screenshot pixels and independently sanitize DOM text, labels, URLs, tool results and history.
8. Present the exact outgoing image and text for review. User edits create a new artifact.
9. Bind approval to the full payload hash and provider. The gateway sends only that immutable approved payload.

**Confidence-aware masking:** high-confidence detections receive tight opaque masks with a small safety margin. Ambiguous detections receive a larger opaque mask or require review. Unsupported/uninspectable regions are withheld or conservatively masked. Confidence thresholds must be calibrated per detector; scores from different models are not directly comparable.

Blur may be useful in a local visualization, but outbound sensitive content should use opaque pixel replacement because blur can preserve recognizable information. Manual corrections supplement automatic detections and are measured separately.

The goal is high recall while retaining enough non-sensitive structure for useful reasoning. Neither human review nor a detector score proves that every private value has been removed.

## 7. Real-time observation and cache reuse

Real-time means responding to meaningful page changes locally. It does not mean sending every frame to the cloud.

- Observe DOM mutations, input changes, navigation, scrolling and resizing; coalesce frequent events.
- Mark affected elements or image regions dirty. Reuse unaffected results only when their page and geometry bindings remain valid.
- Re-run relevant rules immediately for changed fields; schedule bounded screenshot/CV/OCR updates for dirty regions.
- Invalidate geometry on scroll/resize and observations on navigation. Use occasional full refreshes because canvas/video changes may not produce useful DOM mutations.
- Invoke remote reasoning for meaningful decisions: new page/section, validation error, missing information, ambiguity, failed action or explicit user request.
- Avoid a new cloud call for cursor movement, animations or inconsequential DOM changes.

Cache entries should bind task/tab/frame, page version, region fingerprint, viewport geometry, detector/model version, privacy-policy version and expiry. Cache DOM regions, OCR results, detections and local features separately. Clear private task caches on lock or task cleanup and enforce a memory limit.

**Feature reuse:** raw visual embeddings may encode private information and remain local. Initially, reuse them to avoid repeating local inference and produce compact sanitized descriptions for the reasoning model. This can reduce local work and repeated context, but savings must be measured.

Do not assume a hosted LLM can consume arbitrary MobileViT embeddings. Direct feature sharing with a reasoning model is a separate research track requiring a compatible encoder/projector, privacy analysis and likely model adaptation. It is not a prerequisite for useful local caching.

## 8. Reasoning and safe execution

Keep Browser Use's navigation/planning capabilities behind product-owned observation and execution adapters. Enable tools incrementally and ensure every enabled model path, retry, history message and image passes through the privacy gateway.

The model sees typed references and sanitized field descriptions, for example:

```json
{
  "references": [{"id": "ref_7", "type": "person_name", "label": "Applicant name"}],
  "fields": [{"element_id": "el_12", "label": "Full name", "state": "empty"}]
}
```

Its action proposal can be:

```json
{"action": "input_ref", "element_id": "el_12", "value_ref": "ref_7"}
```

The local executor adds and validates the trusted task, tab/frame, page version, approved origin and reference version. It resolves the value only immediately before execution, then verifies the result using a fresh local observation. Resolved values must not appear in model-visible tool results.

Extend to navigation, public search, back/forward, supported controls and eventually multiple tabs. Maintain explicit destination permissions. Add uploads/downloads only with their own disclosure and file-handling policy. Use manual takeover for login, OTP, CAPTCHA and unsupported controls.

Use bounded retries and configurable time, step and model-call budgets. Never replay an uncertain consequential action after a crash. Pause/resume must preserve task context while revalidating the current page and approvals.

## 9. Documents, profile and private reasoning

Store original documents and private records encrypted locally. Extract text/OCR locally with file-size, page-count and processing limits. Every candidate fact carries its source, type, confidence, subject, period where relevant and review state.

Separate persistent profile facts from document-specific facts. A bank statement must not silently overwrite a confirmed address or identity record. Confirmed facts become versioned, task-scoped references.

Provide narrow local tools for operations that require actual private values, such as decimal totals, date comparisons or selecting a matching record. Return a sanitized result or a result reference to the server. If a task cannot be reasoned about safely from sanitized context, ask the user or use a supported local operation.

Voice remains optional. The existing Whisper API flow sends the original recording for transcription and returns an editable draft. For a fully local voice-input path, evaluate a small local speech model later; transcript editing does not undo cloud audio disclosure.

## 10. Key shared contracts

| Contract | Required information |
|---|---|
| Observation | Observation ID, exact target/frame, page version, viewport/DPR, local raw data, stable execution map. |
| Detection | Category, detector/version, confidence, coordinate space, box or text span, source observation. |
| Sanitized context | Sanitized text/fields, masked artifact ID/hash, reference catalog, redaction report; no raw cache features. |
| Cache entry | Scope, content fingerprint, geometry, model/policy versions, expiry and invalidation state. |
| Approval | Task, exact payload/action, provider or website destination, versions, expiry and status. |
| Action | Allowed tool, target, reference/public argument and trusted execution binding. |
| Metric event | Stage, duration, run/model/device identifiers and safe counts; no private payloads. |

Keep raw execution state separate from the sanitized presentation copy. Replacing text must not break the element identities used to execute actions.

## 11. Evaluation and dashboard

Use a labeled dataset covering forms, dashboards, chat/inbox layouts, documents, image text, faces, credentials, dynamic changes and difficult geometry. Include pages absent from development fixtures. Scores must come from actual runs.

| Area | Weight from supplied problem statement | Measurements |
|---|---:|---|
| Visual-context accuracy | 25% | Correct UI/field identification, screen-state interpretation and downstream task success against labels. |
| Sensitive-data detection | 20% | Precision/recall by category, false negatives and automatic-versus-reviewed outcomes. |
| Redaction precision | 20% | Box/mask IoU, sensitive-pixel coverage, missed sensitive regions and excessive masking. |
| Client resources | 20% | Peak memory, CPU, available GPU instrumentation, model load cost and browser responsiveness. |
| End-to-end latency | 15% | Capture, extraction, CV, OCR, masking, gateway, network/model and execution timings; p50/p95. |

The dashboard should show local original/annotated/redacted views, ground-truth overlays, per-stage timings, cache hits/misses, reused regions, inference counts and cloud-call counts. Separate human approval wait time from compute/network time while reporting total task duration too.

Compare cache enabled/disabled, DOM-only versus CV-assisted processing, and WebGPU versus fallback runs on the same tasks. Report device, model versions, dataset size and test conditions. Browser APIs may not expose complete CPU/GPU metrics; use suitable local profiling where necessary and mark unavailable metrics explicitly.

Privacy checks also inspect actual outbound requests for seeded private values and images. A good average IoU cannot compensate for a completely missed password or face.

## 12. Implementation order and completion gates

| Phase | Work | Completion gate |
|---|---|---|
| 1 — Contracts and baseline | Preserve existing agent/vault/review flows; define observation/detection contracts; create labeled fixtures. | Reproducible baseline workflow and outbound privacy checks. |
| 2 — Real browser inference | Validate runtime and one suitable CV model, face detection and image-text extraction. | Inference actually runs inside the browser and its outputs affect the pipeline; record latency/memory. |
| 3 — Unified privacy pipeline | Fuse DOM/CV/OCR findings, implement coordinate transforms and confidence policy, extend exact-payload review. | Useful selectively masked context reaches the model; stale/misaligned artifacts are blocked. |
| 4 — Change detection and caching | Dirty-region scheduling, cache invalidation, memory limits and decision-triggered cloud calls. | Measured work reduction without stale masks or a regression in privacy coverage. |
| 5 — General agent workflow | Broaden supported navigation/controls; complete missing-document review and same-task resume. | Complete varied forms with reordered labels/conditional sections and a separately authored test site. |
| 6 — Evaluation dashboard | Ground truth, category scores, timings, resources and ablation comparisons. | Reproducible results with automatic and human-assisted measurements separated. |
| 7 — Product delivery | Installation/service lifecycle, model packaging, secure updates and compatibility testing. | Reliable setup and recovery; Chrome first, then explicit Firefox/runtime/automation compatibility work. |

Suggested teammate work areas are browser perception, privacy/cache, agent/documents, and dashboard/evaluation. Agree on contracts first so detector outputs can be integrated without rewriting the task engine.

## 13. Current repository versus target

The repository already documents a Browser Use remote agent, controlled Chromium, local encrypted vault, document review, reference-based filling, DOM/pattern screenshot masking, manual image review and editable cloud-transcribed voice input. It also retains a separate deterministic demo planner.

The main additions in this plan are **actual browser-local CV, automatic face/image-text integration, calibrated confidence-aware redaction, real-time change handling, visual cache reuse, broader workflows and the complete measurement dashboard**. Current implementation and test limits are recorded in [README.md](README.md), [docs/IMPLEMENTATION_V0_2.md](docs/IMPLEMENTATION_V0_2.md) and [docs/VALIDATION.md](docs/VALIDATION.md).

The first convincing full-system demonstration should run live on a changing page: detect and mask newly appearing sensitive content locally, reuse only valid cached regions, send a reviewed sanitized observation to a real reasoning endpoint, request missing information, resume after local document review, and verify the resulting form. Its metrics should be produced by that run. Broader website support and detector reliability then expand through measured testing.

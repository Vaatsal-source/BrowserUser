# v0.2 implementation record

The revised scope replaces the original custom remote planner with Browser Use's native Agent while retaining the local vault, extraction, authentication and supervised execution foundations. It deliberately omits browser-local CV. Audio is uploaded unredacted to OpenAI Whisper and returned only as an editable input draft.

## Modules

| Module | Implementation |
|---|---|
| Task entry and voice | Existing React dashboard and MV3 side panel; dedicated extension recording tab; optional starting URL; reviewed record selection. |
| Browser agent | Pinned Browser Use Agent, native observations/planning/navigation/scrolling, curated tools for reference input, guarded clicks, visual checkpoints and missing information. |
| Model boundary | A single agent adapter sanitizes enabled messages and rejects unregistered images; explicit image requests carry exact-byte approval. Native vision/history images are suppressed. |
| Image privacy | Browser-local DOM geometry collection; local Python pixel masking; immutable bounded image store; original/redacted dashboard comparison and additive masks. No CV inference. |
| Facts and documents | Existing encrypted vault, bounded local parsing/OCR, reviewed candidates, document/profile scopes and local calculations. |
| Resume | In-memory agent state and the exact task tab survive missing-information pauses. Updated record selection refreshes the reference catalog. Restarts do not replay tasks. |
| Audio | Bounded memory-only recording upload to fixed OpenAI Whisper endpoint. Separate encrypted API key, editable unredacted transcript, no automatic task creation. |
| Demonstration | Fictional multi-step portal and synthetic statement; old six-field deterministic fixture retained separately. |

## Operating contract

- A starting URL can create a new controlled tab. Otherwise the task uses the selected exact tab.
- The selected destination and record catalog authorize supported fills. Final submission is withheld by default; other consequential controls require review.
- Ordinary remote planning uses sanitized text. Optional text review is available. Screenshots are captured at explicit visual checkpoints and every image-bearing request requires approval.
- An image edit creates another immutable artifact and another approval ID/hash. Stale receipts and post-approval edits cannot change the reviewed outgoing request.
- The privacy filter covers filled controls, known values and recognized patterns, and masks uncertain media. Unsupported geometry, control coverage or page conditions block automation/sharing rather than send a partially inspected context.
- The local executor resolves references only at execution. Literal input, arbitrary JavaScript, uploads/exports and unrestricted native tools are removed from the agent tool registry.
- Model/tool messages are untrusted data. Raw provider errors and private-value results are not exposed as ordinary events.
- Voice recording is local until Transcribe. Transcription sends the original audio to OpenAI. The transcript remains an editable local draft until the user starts a task.

## What changed from v0.1

The original deterministic loop remains only as the explicit no-key Demo planner. Remote tasks now instantiate the stock Browser Use Agent. The previous blanket-black image boundary remains on the legacy path; the new agent's visual path uses verified selective masks and manual review.

UI and document capabilities were extended rather than replaced. The project still uses one local Python companion and dedicated Chromium, without a cloud vault or an additional application server to deploy.

## Deferred

Browser-local ViT/CV, automatic face detection, local speech inference, Firefox, unrestricted cross-origin workflows, real tax preparation/filing, signed installers and universal website support. The presentation should identify these as future work. Human review is not evidence of automatic detector accuracy.

## Verification

See [VALIDATION.md](VALIDATION.md) for checks, results and limits. Mocked model tests validate orchestration and privacy boundaries; hosted-model task quality and microphone transcription accuracy require live provider testing with credentials.

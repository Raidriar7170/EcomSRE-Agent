# RCAEval Single-first Adaptive Agent v1

Status: `SINGLE_FIRST_ADAPTIVE_V1_VALIDATION_COMPLETE_NEGATIVE_RESULT_READY_FOR_REVIEW`

Reason: `VALIDATION_POSITIVE_GATE_NOT_MET_PROVIDER_HTTP_429_CONTAMINATED`

Claim boundary: `DEVELOPMENT_VISIBLE / RESERVED_DEV_VALIDATION / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`.

## Intent

This implementation turns the dev.3 redesign handoff into a bounded Agent:

1. Query Metrics and Logs once.
2. Produce one Strong Single initial diagnosis.
3. Apply a deterministic uncertainty/conflict gate.
4. Return directly or selectively query Logs, Traces, or both.
5. Fuse the initial diagnosis, deterministic Metrics hypotheses, and source-isolated specialist hypotheses with a keep-by-default contradiction policy.
6. Resolve the final indicator with the frozen hybrid deterministic policy.

RE2-SS never exposes Traces. RE2-OB queries Traces only after the gate selects a trace-bearing route. The runtime has no remediation, external writes, hidden labels, TT access, or fallback model.

## Frozen routing and cost contract

The selected gate uses confidence thresholds `0.75` and `0.55`, normalized Metrics top-1/top-2 margin `0.25`, evidence support, indicator availability, and trace ambiguity. Cross-source disagreement remains recorded in the feature snapshot but does not independently block direct return in the selected candidate; all other direct-return gates remain active.

| Route | Model operations | Tool calls |
| --- | ---: | ---: |
| Direct return | 1 | 2 |
| Logs | 3 | 2 |
| Traces | 3 | 3 |
| Both | 4 | 3 |

Every semantic operation permits at most one byte-identical retry for the inherited dev.3 transport allowlist. Schema, protocol, semantic-result, and validation-driven retries remain forbidden. Each run ID has one create-once terminal; an interrupted sidecar is sealed and never replayed.

## Input and output authority

Initial Diagnosis receives one bounded evidence projection plus exact visible service and evidence-reference sets. It does not receive canonical/internal evidence.

Each Logs or Trace call receives one `SpecialistInput`:

- `source_evidence` contains only the selected source;
- `visible_evidence_refs` equals the selected-source reference set exactly;
- `visible_services` equals the selected-source services plus the Initial root service;
- the Provider-visible Initial context excludes Initial evidence references that are not authorized Specialist output references;
- the Provider-facing hypotheses omit source, which the Runtime attaches authoritatively after validation.
- the Logs and Trace prompts require supporting and contradicting references to be disjoint and tell the Provider to omit an ambiguous reference rather than assign both roles.

Fusion receives one architecture-blind `FusionInput` whose Initial service, visible services, visible references, and override candidates are explicit and self-validating. Output validation uses only that same input authority. Fusion keeps the Initial service by default and may override only to an authorized root-candidate service with explicit support and contradiction.

The Provider-facing Fusion proposal may contain overlapping supporting and contradicting references, but the internal `FusionDecision` invariant remains disjoint. After schema, normalization, visibility, and service/action checks pass, an overlap-only proposal deterministically becomes `KEEP_INITIAL` with `OVERLAPPING_EVIDENCE_REJECTED_KEEP_INITIAL`. Unknown references and unauthorized services still fail closed, and the Runtime never makes a second Provider call.

The Runtime propagates nine exact Specialist failure codes and eight exact Fusion failure codes. Safe diagnostics contain only code, role, field path, constraint type, and counts; they never persist original invalid service/ref values, unredacted Provider arguments, or unredacted Provider payloads.

## Evaluation protocol

- Provider smoke: 12 DESIGN cases stratified across both systems and all six fault types.
- DESIGN: one 60-case arm per candidate, at most three candidates, only after the shared smoke passes.
- Minimum DESIGN gate: completion at least 58/60; Root Service at least 50/60; Pair at least 28/60; Damage at most 3; Rescue greater than Damage; at least 24 direct returns; mean semantic operations at most 3.0; no privacy, schema, or schedule failure.
- Candidate selection: Root Service, Pair, net Rescue, lower Damage, lower mean semantic operations, then more direct returns.
- DEV_VALIDATION: only after a tracked candidate freeze; 120 cases × Strong Single reference and selected Adaptive Agent; no tuning after access.

Damage and Rescue use the authoritative `root_cause_pair_ac_at_1` endpoint.

## Fail-closed validation boundary

Before opening the validation schedule or any validation case directory, the validation entrypoint requires the canonical candidate freeze to be byte-identical to its tracked `HEAD` blob. It verifies selected candidate, config hashes, model, passing DESIGN metrics, implementation ancestry, and absence of tracked or untracked drift across the Adaptive runtime, inherited Provider adapter, and entrypoint scripts.

The canonical candidate-3 freeze was committed and verified by the existing loader before the DEV_VALIDATION schedule or any reserved case directory was opened. It bound the passing DESIGN metrics, selected model and config hashes, implementation commit `28d219b868aa4cf5a058dd87fe9449cd0cc81074`, and unchanged runtime scope. The frozen Agent/runtime was not modified after validation began.

## Preserved execution history

The evidence chain remains append-only:

1. Three pre-fix labels produced 36/36 Initial Diagnosis `INVALID_SCHEMA`; these are not DESIGN candidates.
2. Initial-interface-fix r1 completed Initial Diagnosis in 12/12 cases, but only 7/12 completed end to end; four Logs and one Fusion output failures were still generic.
3. Downstream-fix r1 introduced the shared Specialist/Fusion authority and exact codes; 5/12 completed, while seven Logs operations ended with `SPECIALIST_EVIDENCE_REF_NOT_VISIBLE`.
4. Downstream-fix r2 removed the conflicting non-authoritative Initial ref vocabulary from Specialist input; 11/12 completed, while one Fusion operation ended with `FUSION_OVERLAPPING_EVIDENCE_REF`.
5. Fusion-guardrail r1 preserved the internal invariant while converting an otherwise authorized overlap proposal to deterministic `KEEP_INITIAL`; its shared smoke completed 12/12 with no terminal failure.
6. DESIGN candidate 1 completed 57/60 and did not pass the minimum gate. Candidate 2 completed 59/60 but retained one disqualifying Specialist overlap failure. Candidate 3 completed 60/60 with Root 57, Pair 33, Damage 2, Rescue 6, 35 direct returns, and mean 1.85 semantic operations, so it was selected and frozen.
7. One-shot DEV_VALIDATION terminalized 120 Strong Single reference plus 120 Adaptive runs. The reference completed 120/120; Adaptive completed 55/120 and retained 65 `HTTP_429` Provider failures after the allowed retry. Fixed-denominator Root was 99 versus 51 and Pair was 55 versus 31, so the positive gate did not pass.
8. The entrypoint then hit a reporting-only slots/`__dict__` serialization error. A zero-Provider-call terminal-only finalizer reused the same scoring, bootstrap, gate, and public privacy check without changing frozen runtime or creating run IDs. The defect remains disclosed rather than rewritten as a normal entrypoint success.

All older terminal and sidecar aggregates were rehashed unchanged. The guardrail work used one new run domain, each candidate used a distinct candidate ID and private root, validation used fresh run IDs, and no old ID was reused.

## Review disposition

The one-shot reserved DEV_VALIDATION is complete and must not be rerun or tuned. Its negative fixed-denominator result is heavily contaminated by 65 retained HTTP 429 failures and therefore does not support an improvement claim or a clean algorithm-only attribution. Human review should assess the negative result, Provider-capacity failure, and disclosed reporting-only finalization defect. Any future evaluation must use separately authorized Provider capacity and a genuinely fresh external holdout; this task does not access RE2-TT, merge, release, or claim primary inference.

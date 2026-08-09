# Single-first Adaptive v1 result

Final state: `SINGLE_FIRST_ADAPTIVE_V1_VALIDATION_COMPLETE_NEGATIVE_RESULT_READY_FOR_REVIEW`

Claim boundary: `DEVELOPMENT_VISIBLE_DEV_VALIDATION_NOT_EXTERNAL_HOLDOUT`

## Disposition

The final Fusion overlap guardrail passed its shared 12-case smoke. Three bounded DESIGN candidates were then run against the unchanged historical Strong Single baseline. Candidate 3 was the only candidate to pass the unchanged minimum gate, so it was committed, frozen, and verified before any DEV_VALIDATION schedule or case directory was opened.

The one-shot DEV_VALIDATION terminalized all 240 scheduled runs: 120/120 Strong Single reference runs completed, while the frozen Adaptive candidate completed 55/120 and retained 65 `HTTP_429` Provider failures after the one allowlisted transport retry. With failures kept in the denominator, Strong Single reached 99/120 Root Service and 55/120 Pair; Adaptive reached 51/120 Root Service and 31/120 Pair. The paired differences were -40.0 percentage points for Root Service (95% CI -58.3 to -21.7) and -20.0 points for Pair (95% CI -35.8 to -5.8). This does not support an improvement claim.

DEV_VALIDATION first executed all 120 Strong Single runs and only then executed all 120 Adaptive runs. Provider capacity and temporal execution order are therefore confounded with architecture. The 65 Adaptive HTTP 429 failures remain part of the fixed-denominator result, but the run does not provide a clean architecture-only reliability or accuracy comparison.

The negative result is materially Provider-failure-contaminated. It is not evidence that 120 successful Adaptive inferences would have had the same accuracy, and it is not an external-holdout result. No validation rerun, result-driven retry, or validation-driven Agent change was made.

## Preserved evidence chain

| Evidence generation | Terminal result | Provider attempts | Transport retries | Preserved |
| --- | ---: | ---: | ---: | --- |
| Pre-fix Initial interface, three labels | 36/36 `INVALID_SCHEMA` | 36 | 0 | yes |
| Initial-interface-fix r1 smoke | 7 completed, 5 downstream `INVALID_SCHEMA` | 30 | 0 | yes |
| Downstream-fix r1 smoke | 5 completed, 7 `SPECIALIST_EVIDENCE_REF_NOT_VISIBLE` | 28 | 1 | yes |
| Downstream-fix r2 smoke | 11 completed, 1 `FUSION_OVERLAPPING_EVIDENCE_REF` | 34 | 0 | yes |
| Fusion-guardrail r1 shared smoke | 12/12 completed | 34 | 0 | yes |
| DESIGN candidate 1 | 57 completed, 3 failures | 176 | 0 | yes |
| DESIGN candidate 2 | 59 completed, 1 failure | 116 | 0 | yes |
| DESIGN candidate 3 | 60/60 completed | 111 | 0 | yes |
| DEV_VALIDATION reference | 120/120 completed | retained privately | retained privately | yes |
| DEV_VALIDATION Adaptive | 55 completed, 65 `HTTP_429` | 237 | 65 | yes |

All six pre-guardrail terminal/sidecar groups were freshly rehashed after validation and matched their recorded aggregates. The three new DESIGN candidate roots and both validation arms are also bound by aggregate SHA-256 values in the machine-readable result. No old execution identifier or private root was reused.

## Fusion overlap guardrail

The Provider-facing Fusion proposal may contain overlapping supporting and contradicting evidence references, while the internal `FusionDecision` disjointness invariant remains unchanged. Materialization applies JSON/schema validation, stable normalization, visible-reference checks, and service/action checks before the overlap-only fallback.

An otherwise authorized overlap proposal becomes deterministic `KEEP_INITIAL`: it preserves the Initial service, confidence, and evidence authority; clears contradicting references; stable-deduplicates the original reason codes; and appends `OVERLAPPING_EVIDENCE_REJECTED_KEEP_INITIAL`. Unknown references and unsupported services still fail closed. The guardrail never asks the Provider a second time. Private traces record only the applied flag, safe reason, and overlap count; public results report only an aggregate count.

## Shared smoke

| Boundary | Result |
| --- | --- |
| Candidate / domain | candidate-1 / `single-first-adaptive-v1-fusion-guardrail-r1` |
| Scheduled / terminalized / completed | 12 / 12 / 12 |
| Initial / Specialist / Fusion terminal failures | 0 / 0 / 0 |
| Fusion overlap guardrail count | 0 |
| Provider attempts | 34 |
| Transport / semantic retries | 0 / 0 |
| Private-path hits | 0 |
| Conservative token upper bound | 61,895 |
| Shared-smoke gate | passed |

The 12 smoke terminals were reused as the candidate-1 DESIGN subset. No second smoke or second guardrail run domain was created.

## DESIGN candidates

Historical Strong Single baseline: Root Service 51/60; Pair 29/60. It was reused, not rerun.

| Candidate | Complete | Root | Pair | Damage | Rescue | Direct | L / T / B | Mean ops | Guardrail | Gate |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 57/60 | 55 | 33 | 3 | 7 | 1 | 51 / 4 / 4 | 3.03 | 1 | failed |
| 2 | 59/60 | 56 | 32 | 3 | 6 | 32 | 23 / 3 / 2 | 1.97 | 1 | failed |
| 3 | 60/60 | 57 | 33 | 2 | 6 | 35 | 20 / 4 / 1 | 1.85 | 0 | passed |

Candidate 1 failed completion, mean-operation, direct-return, and disqualifying-failure requirements. Candidate 2 met the performance thresholds but had one `SPECIALIST_OVERLAPPING_EVIDENCE_REF` terminal, so its disqualifying-failure count was nonzero. Candidate 3 used only the authorized Logs/Trace evidence-role prompt clarification, completed all 60 cases, and was the only minimum-gate-passing candidate. Across candidate 3, there were no Fusion overrides and no rejected unsafe overrides.

## Candidate freeze

- Selected candidate: `candidate-3`.
- Implementation commit: `28d219b868aa4cf5a058dd87fe9449cd0cc81074`.
- Freeze commit: `f8d046449b71a683a29a8940fb83bd3d32ef919c`.
- Canonical freeze: `config/rcaeval-adaptive-v1/adaptive-candidate.json`.
- Freeze loader: passed before validation schedule access.
- Frozen runtime changed after freeze: no.

## DEV_VALIDATION

| Metric | Strong Single reference | Frozen Adaptive |
| --- | ---: | ---: |
| Scheduled / terminalized | 120 / 120 | 120 / 120 |
| Completed | 120 | 55 |
| Provider failure | 0 | 65 × `HTTP_429` |
| Root Service | 99/120 (82.5%) | 51/120 (42.5%) |
| Pair | 55/120 (45.8%) | 31/120 (25.8%) |

Adaptive fixed-denominator metrics:

- Root difference: -40.0 points; hierarchical paired 95% CI [-58.3, -21.7].
- Pair difference: -20.0 points; hierarchical paired 95% CI [-35.8, -5.8].
- Damage 31; Rescue 7; Net Rescue -24.
- Direct return 30/120; Logs 22; Traces 2; Both 66.
- Escalation precision 68/90; recall 68/69.
- Mean semantic operations 3.05; mean tools 0.94.
- Provider attempts 237; transport retries 65.
- Known-token lower bound 190,786; conservative upper bound 4,350,786.
- Mean recorded latency 5,261.6 ms.
- Fusion overlap guardrail count 0; correct overrides 0; wrong overrides 0; rejected unsafe overrides 0.

The execution order was not paired or interleaved: all 120 Strong Single runs completed before the Adaptive arm began. Provider capacity and wall-clock order are therefore inseparable from the architecture arm in this run. The fixed-denominator metrics remain authoritative for the protocol that was actually executed, including all 65 Adaptive HTTP 429 failures, but they are not a clean architecture-only reliability or accuracy comparison.

The route and escalation aggregates include the retained failure-path defaults and must not be read as clean behavioral estimates for 120 successful Adaptive runs.

After all 240 terminals were written, the entrypoint hit a reporting-only Python error because a slots-based bootstrap interval was read through `__dict__`. A terminal-only deterministic finalizer reused the same scoring, 10,000-iteration paired bootstrap, positive gate, and public privacy check to create the aggregate and private outcomes. It made zero Provider calls, created zero run IDs, changed zero tracked runtime files, and is not a validation rerun. The entrypoint defect remains disclosed for human review because the frozen runtime was not modified after validation began.

## Data and claim boundary

- DEV_VALIDATION was opened only after the canonical candidate freeze passed the existing loader.
- The reserved split contained 60 RE2-OB and 60 RE2-SS identities, with zero overlap against DESIGN.
- RE2-TT, production data, and another external holdout were not accessed.
- Case identities, run identifiers, raw Provider output, concrete evidence overlaps, credentials, and private paths are not published.
- This is development-visible reserved validation, not primary inference and not a fresh external holdout.

## Required next action

Human review should inspect the negative, Provider-failure-contaminated validation result and the disclosed reporting-only finalization defect. Do not rerun this validation, tune the frozen candidate, or present the result as an accuracy improvement. If the project proceeds after review, use a genuinely fresh external holdout under separately approved provider capacity and protocol.

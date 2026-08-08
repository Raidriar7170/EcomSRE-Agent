# Single-first Adaptive v1 result

Final state: `SINGLE_FIRST_ADAPTIVE_V1_DOWNSTREAM_INTERFACE_BLOCKED_READY_FOR_REVIEW`

Reason: `DOWNSTREAM_INTERFACE_REPAIR_ROUND_LIMIT_EXHAUSTED`

## Disposition

The Initial Diagnosis interface remains repaired: every later shared-smoke run completed Initial Diagnosis, with zero `INITIAL_*` failures. The downstream repair then established one source-isolated Specialist authority, one architecture-blind Fusion authority, Provider-only Specialist output shapes, Runtime-owned source, and exact safe failure codes for all Specialist and Fusion rejection classes.

The two authorized downstream-interface repair rounds were consumed. The first ended with seven `SPECIALIST_EVIDENCE_REF_NOT_VISIBLE` failures. The minimal second repair removed non-authoritative Initial evidence references from the Provider-visible Specialist context without widening the selected-source reference set. The final shared smoke improved to 11/12 end-to-end completions but retained one exact `FUSION_OVERLAPPING_EVIDENCE_REF` failure. The unchanged 12/12 completion gate therefore did not pass.

No third interface repair is authorized. DESIGN, candidate selection, candidate freeze, and DEV_VALIDATION were not started.

## Preserved evidence chain

| Evidence generation | Terminal result | Provider attempts | Transport retries | Semantic retries | Preserved |
| --- | ---: | ---: | ---: | ---: | --- |
| Pre-fix Initial interface, three labels | 36/36 `INVALID_SCHEMA` | 36 | 0 | 0 | yes |
| Initial-interface-fix r1 shared smoke | 7 completed, 5 downstream `INVALID_SCHEMA` | 30 | 0 | 0 | yes |
| Downstream-fix r1 shared smoke | 5 completed, 7 `SPECIALIST_EVIDENCE_REF_NOT_VISIBLE` | 28 | 1 | 0 | yes |
| Downstream-fix r2 shared smoke | 11 completed, 1 `FUSION_OVERLAPPING_EVIDENCE_REF` | 34 | 0 | 0 | yes |

All five older terminal/sidecar groups were freshly rehashed before the final smoke and matched their recorded aggregates. No old execution identifier or private root was reused. The machine-readable result retains the aggregate hashes without publishing private paths, case identities, or run identifiers.

## Downstream contracts

- `SpecialistInput` contains only the requested source evidence; `visible_evidence_refs` equals that evidence exactly, and `visible_services` equals source services plus the Initial service.
- The Provider-facing hypothesis schema does not request source. The Runtime attaches the authoritative `logs` or `traces` source after validation.
- The r2 Specialist Initial context retains root service, optional indicator, confidence, explanation, and uncertainty flags, but excludes the non-authoritative Initial evidence-reference list.
- `FusionInput` explicitly binds Initial service, visible services, visible references, and override candidates to the same architecture-blind input sent to the Provider.
- Nine Specialist and eight Fusion failure codes propagate through safe validation errors, semantic-operation records, terminal records, and smoke aggregates.
- Safe diagnostics contain only allowlisted code, role, field path, constraint type, and counts. Raw invalid values and raw Provider output are not persisted.

## Final shared smoke

| Boundary | Result |
| --- | --- |
| Candidate / domain | candidate-1 / `single-first-adaptive-v1-downstream-fix-r2` |
| Scheduled / terminalized | 12 / 12 |
| Initial Diagnosis completed | 12 / 12 |
| End-to-end completed | 11 / 12 |
| Initial / Specialist failures | 0 / 0 |
| Fusion failures | 1 × `FUSION_OVERLAPPING_EVIDENCE_REF` |
| Provider attempts | 34 |
| Transport / semantic retries | 0 / 0 |
| Private-path hits | 0 |
| Conservative token upper bound | 60,962 |
| Budget checks | passed |
| Shared-smoke gate | failed |

## Evaluation boundary

- 60-case DESIGN: not run; iterations used: 0.
- Candidate selection and freeze: not performed.
- DEV_VALIDATION schedule values accessed: no.
- DEV_VALIDATION case directories opened: no.
- RE2-TT or another external holdout accessed: no.
- Validation reruns or result-driven tuning: none.

The historical Strong Single DESIGN baseline remains 51/60 Root Service and 29/60 Pair as context only. It was not rerun and is not a new comparison.

## Required next action

Human algorithm review should assess the preserved r2 Fusion overlap failure and decide whether any future version warrants a separately authorized design change. This result is a shared-interface smoke outcome, not a DESIGN or DEV_VALIDATION accuracy result. Do not run a third repair under `single-first-adaptive-v1`, reuse consumed identifiers, or open DEV_VALIDATION.

# Single-first Adaptive v2 development result

Development state: `TERMINAL`

Verdict: `ADAPTIVE_V2_TUNE_GATE_NOT_PASSED_AFTER_REAL_ALGORITHM_ITERATIONS`

Failure reason: candidate-4 did not pass the frozen TUNE gate, and its observed
failure mode did not authorize any single candidate-5 change under Work Package
F Case A-D.

Claim boundary: `CONSUMED_OBSS_DEVELOPMENT_RESULT / DEVELOPMENT_VISIBLE / NOT_EXTERNAL_VALIDATION / NOT_PRIMARY_INFERENCE`.

## Preserved capacity and algorithm records

Candidate-1 and candidate-2 remain fixed-denominator Provider-capacity records:
both completed 0/60, with 59/60 and 60/60 terminal HTTP 429 failures
respectively. They are not algorithm-quality estimates and were not rewritten.
The operator then confirmed exhausted API credit and recharged it.

Candidate-3 completed 60/60 after credit recovery, proving that the capacity
blocker had cleared. It returned Direct for all 60 records. The zero-Provider
diagnosis found 49 Initial Root-correct and 11 Initial Root-wrong records; all
10 records marked unstable still returned Direct. Offline Policy A would have
escalated 3 records, while Policy B would have escalated 20. The production Gate
was therefore frozen at Metrics margin `0.75` and risk-signal count `1` for
candidate-4. The public aggregate-only diagnosis is in
`docs/analysis/rcaeval-adaptive-v2-gate-diagnosis.json`.

## Same-run candidate-4 result

Candidate-4 used the second real algorithm-candidate slot and completed 59/60.
The remaining terminal was an invalid schema result; no HTTP 429 or Provider
failure occurred.

| Metric | Candidate-4 |
| --- | ---: |
| Scheduled / terminalized / completed | 60 / 60 / 59 |
| Initial Root / Pair | 51 / 27 |
| Final Root / Pair | 51 / 27 |
| Same-run Root Damage / Rescue / Net | 0 / 0 / 0 |
| Same-run Pair Damage / Rescue / Net | 0 / 0 / 0 |
| Direct / Logs / Traces / Both | 43 / 16 / 0 / 0 |
| Escalation Precision / Recall | 8/16 / 8/8 |
| Initial-correct escalated / Initial-wrong Direct | 8 / 0 |
| Specialist hypotheses | 44 |
| Fusion KEEP / OVERRIDE | 59 / 0 |
| Deterministic indicator overrides | 0 |
| Correct / Wrong Override | 0 / 0 |
| Mean semantic operations | 1.25 |
| Provider attempts / transport retries | 77 / 0 |
| Known / conservative tokens | 515,817 / 515,817 |
| Mean latency | 6,572.0 ms |
| HTTP 429 | 0 |
| Schema/privacy/schedule failures | 1 |
| Gate | `TUNE_GATE_NOT_PASSED` |

Candidate-4 met the completion, final Root, Direct, mean-operations, Trace, 429,
and override bounds. It failed because final Pair was 27 < 29, Root Rescue 0
was not strictly greater than Root Damage 0, Root net Rescue was 0 < 1, and one
schema failure violated the zero-failure requirement.

Historical cross-run comparison remains labeled
`CROSS_RUN_CONTEXTUAL_COMPARISON / MODEL_RUN_VARIABILITY_CONFOUNDED`; it is not
the authoritative same-run Agent Damage/Rescue measure.

## Candidate-5 protocol disposition

Candidate-5 was not executed. Candidate-4 had Direct 43 and escalation Recall
1.0, so Case A did not apply. Direct was not below 36, and Gate tightening could
not create the missing correct Specialist alternative or Root rescue. Among the
eight Initial-wrong escalations, Specialist produced no correct Root
alternative, so Case C did not apply. Pair had zero same-run Damage and no
deterministic indicator override, so Case D did not apply. The one schema
failure is not an authorized Case A-D algorithm direction.

Running candidate-5 would therefore have required an unsupported change,
result-driven retry, or scope expansion. The candidate loop stops without a
candidate-6.

## Protected downstream stages

- No TUNE candidate passed the frozen gate.
- The single 120-case consumed-data regression was not eligible and did not run.
- No post-regression tuning or regression rerun occurred.
- A fresh external holdout plan was not eligible and was not created.
- No fresh dataset, RE2-TT data, or new external data was accessed.
- No candidate is selected.

All case identifiers, run identifiers, private paths, concrete evidence
references, credentials, and raw Provider outputs remain private.

## Terminal disposition

Preserve candidate-1 through candidate-4 terminals and sidecars unchanged. Mark
PR #19 Ready for algorithm review. Do not run candidate-5 without a new protocol
decision, create candidate-6, run regression, generate a fresh holdout plan, or
make an external performance claim under this Goal.

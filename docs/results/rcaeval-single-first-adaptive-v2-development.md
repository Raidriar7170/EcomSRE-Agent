# Single-first Adaptive v2 development result

Development state: `TERMINAL`

Verdict: `ADAPTIVE_V2_TUNE_GATE_NOT_PASSED_AFTER_CANDIDATE5`

Failure reason: the final bounded candidate completed 60/60 but missed the
frozen Root, Pair, and Root-rescue requirements.

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

## Candidate-4 Metrics opportunity and Candidate-5 decision

A zero-Provider post-hoc analysis covered all eight completed Candidate-4
Initial-wrong cases. The Gate escalated 8/8. True Root Metrics Coverage@1 / @2 /
@3 / @6 was 6/8 / 8/8 / 8/8 / 8/8, and the deterministic highest-ranked
non-Initial Metrics alternative matched the True Root in 7/8. None of the
truth-matching alternatives was visible in bounded Logs, and no case had both
Initial and alternative visible in Logs. The analysis made zero Provider calls
and exposed no case-level data publicly.

This supported Decision `CASE_E_SPECIALIST_GENERATION_FAILURE`: replace
Candidate-5 free Logs hypothesis generation with an Initial-vs-Alternative
pairwise verifier anchored to the deterministic Metrics alternative. Fusion
remained deterministic and keep-by-default. Gate, Trace, Indicator, model,
pacing, retry policy, splits, and acceptance thresholds remained frozen.

## Same-run candidate-5 result

Candidate-5 completed all 60 TUNE records after API credit recovery, with no
HTTP 429, Provider failure, or schema/privacy/schedule failure.

| Metric | Candidate-5 |
| --- | ---: |
| Scheduled / terminalized / completed | 60 / 60 / 60 |
| Initial Root / Pair | 45 / 23 |
| Final Root / Pair | 45 / 23 |
| Same-run Root Damage / Rescue / Net | 0 / 0 / 0 |
| Same-run Pair Damage / Rescue / Net | 0 / 0 / 0 |
| Direct / Logs / Traces / Both | 37 / 23 / 0 / 0 |
| Escalation Precision / Recall | 14/23 / 14/15 |
| Pairwise INITIAL / ALTERNATIVE / INCONCLUSIVE | 7 / 1 / 15 |
| Fusion KEEP / OVERRIDE | 60 / 0 |
| Correct / Wrong Override | 0 / 0 |
| Mean semantic operations | 1.3833 |
| Provider attempts / transport retries | 85 / 2 |
| Known / conservative tokens | 516,021 / 580,021 |
| Mean latency | 7,272.9 ms |
| HTTP 429 | 0 |
| Schema/privacy/schedule failures | 0 |
| Gate | `TUNE_GATE_NOT_PASSED` |

The pairwise verifier completed 23/23 calls. It preferred `ALTERNATIVE` once,
but that alternative lacked the required root-role support, so deterministic
Fusion kept Initial. All 60 Final predictions therefore remained equal to
Initial. Candidate-5 met the execution, route/cost, Trace, and override bounds,
but Final Root 45 < 51, Final Pair 23 < 29, same-run Root Rescue was not strictly
greater than Damage, and Root net Rescue was 0 < 1.

The historical cross-run 7 Damage / 1 Rescue aliases are retained only as
`CROSS_RUN_CONTEXTUAL_COMPARISON / MODEL_RUN_VARIABILITY_CONFOUNDED`; they do not
decide the gate. Candidate-5 is final and candidate-6 is not authorized.

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

Preserve candidate-1 through candidate-5 terminals and sidecars unchanged. Mark
PR #19 Ready for algorithm review. Do not create candidate-6, run regression,
generate a fresh holdout plan, or make an external performance claim under this
Goal. The recommended next step is an algorithm review, not a result-driven
TUNE rerun.

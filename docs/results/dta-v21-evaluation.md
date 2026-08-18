# DTA v2.1 P0 Held-Out Evaluation

- Terminal: `DTA_V21_PR_E_HELD_OUT_COMPLETED`
- Exact claim: `DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED`
- Execution ID: `53615cdd78b348b68496f64102c0b4de`
- Held-out cases / scored entries: 8 / 24
- Model: `gpt-5.4-mini-2026-03-17`
- Held-out pack seal: `9a7c8e56400e99c693c8bddc26007b1dd26e0dcee2167b07cf3fba00fd22fbd7`
- Execution seal: `04b4fb176d332d4ad84734a7fe3ab740becea97cbd2bf4df4272c984aca39caa`

## Aggregate metrics

| Group | Entries | Protocol | Root | Mechanism | Macro-F1 | Evidence | Action | Mean input | Mean total | Mean reads | Median latency ms | Unsafe |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| EVIDENCE_GUIDED_PLANNER | 8 | 0.250 | 0.167 | 0.000 | 0.000 | 0.000 | 0.125 | 11528.6 | 12778.9 | 3.12 | 9119.5 | 0 |
| FLAT_ADAPTIVE | 8 | 0.875 | 0.000 | 0.000 | 0.000 | 0.000 | 0.125 | 6901.0 | 7267.2 | 2.50 | 5643.0 | 0 |
| ONE_SHOT_FULL_CONTEXT | 8 | 0.500 | 0.500 | 0.500 | 0.533 | 0.875 | 0.500 | 4040.1 | 4436.1 | 0.00 | 3358.5 | 0 |
| HELD_OUT | 24 | 0.542 | 0.222 | 0.167 | 0.250 | 0.292 | 0.250 | 7489.9 | 8160.8 | 1.88 | 5493.5 | 0 |

## Preregistered threshold table

| Condition | Frozen threshold | Passed |
|---|---|---|
| Protocol acceptance for both primary arms | 1.00 | `false` |
| Root exact match | Planner not lower | `true` |
| Mechanism Macro-F1 delta | >= 0.10 | `false` |
| Evidence-validity advantage | >= 1 case and >= 0.10 | `false` |
| Runbook Top-1 or action-precision advantage | >= 1 case and >= 0.10 | `false` |
| Mean input-token ratio | <= 0.75 | `false` |
| Mean total-token ratio | <= 0.80 | `false` |
| Mean semantic-read ratio | <= 1.00 | `false` |
| Median latency ratio | <= 1.25 | `false` |
| Duplicate normalized calls | 0 | `true` |
| Unsafe proposal attempts | 0 | `true` |
| Arbitrary shell attempts | 0 | `true` |
| Non-owned mutations | 0 | `true` |
| Truth isolation | PASS | `true` |
| Scorer verification | PASS | `true` |

## Limitations

- This is one sealed eight-case local replay evaluation.
- One-shot is a descriptive anchor, not a superiority target.
- The result is not production evidence or live recovery accuracy.

The JSON report contains the complete per-arm, per-mechanism, and per-generalization-slice metric set plus the frozen threshold decision.

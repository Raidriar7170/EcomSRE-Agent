# RCAEval Metrics Arbitration v1 — Development Results

Status: `METRICS_ARBITRATION_REGRESSION_PASSED_READY_FOR_FRESH_HOLDOUT_PLAN`

M3 changes only the Root service when the Initial service is outside Metrics Top-2 and the normalized Top-1/Top-2 margin is at least 0.25. The exact Initial indicator is always retained.

The primary evidence is the same-run Initial → Final comparison. Historical Strong Single values are cross-run context, not paired causal evidence.

## Frozen fixture replay

| Fixture | Completed | Initial Root | M3 Final Root | Override | Rescue | Damage | Net |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Candidate-3 | 60 | 49 | 57 | 8 | 8 | 0 | +8 |
| Candidate-4 | 59 | 51 | 57 | 6 | 6 | 0 | +6 |
| Candidate-5 | 60 | 45 | 57 | 12 | 12 | 0 | +12 |

## Live phases

- Smoke: gate `True`; completed 12/12; Final Root 12; Final Pair 7; Root net rescue +2.
- Tune: gate `True`; completed 60/60; Final Root 57; Final Pair 28; Root net rescue +6.
- Regression: gate `True`; completed 120/120; Final Root 112; Final Pair 55; Root net rescue +17.

## Historical context

- TUNE Strong Single: Root 51/60, Pair 29/60.
- Regression Strong Single: Root 99/120, Pair 55/120.
- Classification: `CROSS_RUN_CONTEXTUAL_BASELINE`.

Claim boundary: consumed OB/SS development evidence only; no TT access; no external validation or production-generalization claim.

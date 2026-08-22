# DTA v2.2.5 Opaque Ambiguity and Fail-Closed Set Closure Study

- Phase: `EVALUATION`
- Provider model: `gpt-5.4-mini-2026-03-17`
- Cases: 16
- Runs: 64
- Full-study execution count: 1
- Uncaught exceptions: 0
- Agent writes: 0

## Four-combination metrics

| Combination | Exact | Macro-F1 | Resource ambiguity | Premature NO_INCIDENT | Resources reads/case | Control | Provider calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| TARGET_ONE | 0.750 | 0.867 | 0.500 | 0.500 | 1.000 | 1.000 | 16 |
| TARGET_SET | 1.000 | 1.000 | 1.000 | 0.000 | 1.600 | 1.000 | 16 |
| BUNDLE_ONE | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 16 |
| BUNDLE_SET | 0.938 | 0.971 | 0.875 | 0.000 | 1.000 | 1.000 | 16 |

## Measured result terminal

`DTA_V22_5_NO_AMBIGUITY_EFFECT_OBSERVED`

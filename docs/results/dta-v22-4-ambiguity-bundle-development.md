# DTA v2.2.4 Ambiguity-Set Closure and Contrastive Resources Study

- Phase: `DEVELOPMENT`
- Provider model: `gpt-5.4-mini-2026-03-17`
- Cases: 16
- Runs: 64
- Full-study execution count: 0
- Uncaught exceptions: 0
- Agent writes: 0

## Four-combination metrics

| Combination | Exact | Macro-F1 | Resource ambiguity | Premature NO_INCIDENT | Resources reads/case | Control | Provider calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| TARGET_ONE | 0.812 | 0.733 | 0.250 | 0.750 | 1.000 | 1.000 | 16 |
| TARGET_SET | 1.000 | 1.000 | 1.000 | 0.000 | 1.800 | 1.000 | 16 |
| BUNDLE_ONE | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 16 |
| BUNDLE_SET | 1.000 | 1.000 | 1.000 | 0.000 | 1.000 | 1.000 | 16 |

## Development gate

- Gate passed: `true`
- Exact gain: 3
- Macro-F1 gain: 0.267

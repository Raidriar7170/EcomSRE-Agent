# DTA v2.2.3 Evidence Closure and Deterministic Dispatch Study

- Phase: `DEVELOPMENT`
- Provider model: `gpt-5.4-mini-2026-03-17`
- Cases: 16
- Runs: 64
- Full-study execution count: 0
- Uncaught exceptions: 0
- Agent writes: 0

## Four-combination metrics

| Combination | Exact | Macro-F1 | Resource-silent | Premature NO_INCIDENT | Diagnosis after read | Control | Provider calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| MODEL_LEGACY | 0.750 | 0.600 | 0.000 | 1.000 | 0.667 | 1.000 | 29 |
| MODEL_CLOSED | 1.000 | 1.000 | 1.000 | 0.000 | 0.625 | 1.000 | 48 |
| AUTO_LEGACY | 0.750 | 0.600 | 0.000 | 1.000 | 0.667 | 1.000 | 16 |
| AUTO_CLOSED | 1.000 | 1.000 | 1.000 | 0.000 | 0.625 | 1.000 | 16 |

## Development gate

- Gate passed: `true`
- Oracle-path hit: 1.000
- Exact gain: 4
- Macro-F1 gain: 0.400

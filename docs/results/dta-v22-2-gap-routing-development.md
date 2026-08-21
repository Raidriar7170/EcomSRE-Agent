# DTA v2.2.2 Gap-Aware Routing Study

- Phase: `DEVELOPMENT`
- Provider model: `gpt-5.4-mini-2026-03-17`
- Cases: 12
- Runs: 48
- Execution count: 0
- Uncaught exceptions: 0
- Agent writes: 0

## Four-combination metrics

| Combination | Exact | Macro-F1 | Diagnosis after read | Control accuracy | Protocol failure |
|---|---:|---:|---:|---:|---:|
| FLAT_BROAD | 0.750 | 0.733 | 0.556 | 0.750 | 0.000 |
| FLAT_GAP | 0.750 | 0.833 | 0.875 | 0.500 | 0.000 |
| PLANNER_BROAD | 0.917 | 0.933 | 0.875 | 0.750 | 0.000 |
| PLANNER_GAP | 0.833 | 0.867 | 0.750 | 0.750 | 0.000 |

## Development utility gate

- Predicate-yield read rate: 0.464
- Nonempty-or-predicate-yield read rate: 0.464
- Read-bearing diagnosed runs: 13
- Protocol failure rate: 0.000
- Gate passed: `true`

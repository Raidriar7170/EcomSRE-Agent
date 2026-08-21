# DTA v2.2.2 Gap-Aware Routing Study

- Phase: `EVALUATION`
- Provider model: `gpt-5.4-mini-2026-03-17`
- Cases: 16
- Runs: 64
- Execution count: 1
- Uncaught exceptions: 0
- Agent writes: 0

## Four-combination metrics

| Combination | Exact | Macro-F1 | Diagnosis after read | Control accuracy | Protocol failure |
|---|---:|---:|---:|---:|---:|
| FLAT_BROAD | 0.375 | 0.000 | 0.000 | 1.000 | 0.000 |
| FLAT_GAP | 0.625 | 0.400 | 0.444 | 1.000 | 0.000 |
| PLANNER_BROAD | 0.375 | 0.000 | 0.000 | 1.000 | 0.000 |
| PLANNER_GAP | 0.500 | 0.267 | 0.154 | 1.000 | 0.000 |

## Observed final GAP utility

- Predicate-yield read rate: 0.111
- Nonempty-or-predicate-yield read rate: 0.111
- Read-bearing diagnosed runs: 6
- Protocol failure rate: 0.000
- Development-threshold comparison: `false` (reported diagnostically; the
  development gate was passed before freeze and is not a final-study terminal)

## Measured result terminal

`DTA_V22_2_GAP_ROUTING_QUALITY_EFFECT_OBSERVED`

- Planner interaction observed: `false`

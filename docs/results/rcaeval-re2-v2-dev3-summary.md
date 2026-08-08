# RCAEval RE2 v2-dev.3 Final Infrastructure Result

State: `RCAEval_RE2_V2_DEV3_DESIGN_COMPLETE_READY_FOR_AGENT_REDESIGN`

Classification: `DEVELOPMENT_VISIBLE / DESIGN_SET / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`

PR #14, PR #15, and PR #16 remain immutable failed-gate evidence.

## Dev.2 Provider Failure Audit

- Failed runs: 5
- Failure classes: `{"UNKNOWN_INSUFFICIENT_EVIDENCE": 5}`
- Retry eligible / ineligible: 0 / 5

## Zero-Provider Admission and inherited F0

- Admission: `V2_DEV3_ADMISSION_REHEARSAL_PASSED`
- Smoke / DESIGN / validation metadata: 72 / 360 / 480
- Provider objects, calls, run attempts, operation attempts, and Provider attempts: 0
- F0 Overall / Memory / Socket: 57/60 (0.9500) / 10/10 (1.0000) / 9/10 (0.9000)

## Provider Smoke

- Gate: `V2_DEV3_PROVIDER_SMOKE_GATE_PASSED`
- Terminalized: 72/72
- Semantic operations / Provider attempts: 228 / 234
- Transport retries / recoveries / failures: 6 / 6 / 0
- Known token lower bound / conservative upper bound: 657535 / 849535

## DESIGN architecture metrics

| Variant | Completed | Root Service AC@1 | Root Cause Pair AC@1 |
|---|---:|---:|---:|
| dynamic_v1_reference | 60/60 (1.0000) | 51/60 (0.8500) | 26/60 (0.4333) |
| dynamic_v2_dev3 | 60/60 (1.0000) | 52/60 (0.8667) | 29/60 (0.4833) |
| fixed_v1_reference | 60/60 (1.0000) | 48/60 (0.8000) | 23/60 (0.3833) |
| fixed_v2_dev3 | 60/60 (1.0000) | 42/60 (0.7000) | 19/60 (0.3167) |
| single_v1_reference | 60/60 (1.0000) | 51/60 (0.8500) | 29/60 (0.4833) |
| single_v2_dev3 | 53/60 (0.8833) | 43/60 (0.7167) | 23/60 (0.3833) |

DESIGN Gate: `V2_DEV3_DESIGN_GATE_PASSED`

## Boundary and transition

DEV_VALIDATION values and directories were not accessed or executed. RE2-TT was not accessed. No external claim is made.

There is no dev.4. The next task is to implement the Single-first Adaptive RCA Agent described in the Agent Redesign Handoff.

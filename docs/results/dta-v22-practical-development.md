# DTA v2.2 Practical Development Report

## Outcome

The simple Provider boundary, Flat Canonical arm, Planner-Lite arm, replay
backend, and runtime-managed controller loop completed the development gate.
The selected Provider was `gpt-5.4-mini-2026-03-17`.

- Provider smoke: 8/8 valid after at most one repair; 5/8 first pass; three
  semantic repairs; zero transport retries, uncaught exceptions, and Agent
  writes.
- Development campaign: eight cases, both arms, 16 arm-runs, identical case
  bytes across arms, and evaluator truth loaded only after both arms ran.
- No Docker, Runbook, Agent write, or live remediation path was called.

The first complete execution exposed an invalid dependency choice on a
single-service topology and a protocol-rate denominator bug. The next, changed
development iteration fixed both. No identical campaign was repeated, and the
fixed evaluation set was not used for tuning.

## Development metrics

`run completion` below is the Goal's end-to-end exact success definition, not
merely receipt of a locally valid terminal.

| Metric | Flat Canonical | Planner-Lite |
| --- | ---: | ---: |
| End-to-end run completion | 0.2500 | 0.2500 |
| Operational valid-terminal rate | 0.7500 | 0.8750 |
| First-pass protocol success | 0.7778 | 0.9000 |
| Post-repair protocol success | 1.0000 | 1.0000 |
| Repair rate | 0.2222 | 0.1000 |
| Root-service accuracy (6 incidents) | 0.0000 | 0.1667 |
| Mechanism accuracy (6 incidents) | 0.0000 | 0.1667 |
| Mechanism Macro-F1 | 0.0000 | 0.1333 |
| No-Incident accuracy (1 case) | 1.0000 | 0.0000 |
| Abstention accuracy (1 case) | 1.0000 | 1.0000 |
| Evidence-ref validity (6 incidents) | 0.0000 | 0.1667 |
| Semantic clause validity (6 incidents) | 0.0000 | 0.1667 |
| Mean adaptive reads | 0.0000 | 0.1250 |
| Duplicate read attempts | 0 | 0 |
| Mean Provider turns | 1.2500 | 1.2500 |
| Input / output / total tokens | 11,080 / 462 / 11,542 | 15,617 / 475 / 16,092 |
| Mean latency | 1,989.71 ms | 2,003.19 ms |
| Transport retries | 0 | 0 |
| Uncaught exceptions / Agent writes | 0 / 0 | 0 / 0 |

## Case outcomes

| Case | Expected | Flat | Planner-Lite |
| --- | --- | --- | --- |
| D01 | Payment configuration | `PROTOCOL_FAILED` | `SEMANTICALLY_WRONG` |
| D02 | Recommendation unavailable | `SEMANTICALLY_WRONG` | `COMPLETED_CORRECT` |
| D03 | Email memory | `SEMANTICALLY_WRONG` | `PROTOCOL_FAILED` |
| D04 | Ad CPU | `SEMANTICALLY_WRONG` | `SEMANTICALLY_WRONG` |
| D05 | Email unavailable | `SEMANTICALLY_WRONG` | `SEMANTICALLY_WRONG` |
| D06 | Shipping dependency | `PROTOCOL_FAILED` | `SEMANTICALLY_WRONG` |
| D07 | No incident | `COMPLETED_CORRECT` | `SEMANTICALLY_WRONG` |
| D08 | Missing/conflicting | `COMPLETED_CORRECT` | `COMPLETED_CORRECT` |

## Binding and scoring note

The selected local campaign result has SHA-256
`65b848384ad67fb9458ab83adc272353acf8935a1cae3b92bb4bea50b7c69128`.
It remains local and ignored. After the campaign, `run_completion_rate` was
corrected to the Goal's exact-success definition. Evidence applicability was
also corrected to require an admitted incident Diagnosis with actual cited
refs, and mean Provider turns now counts repair calls. All were recomputed from
the same immutable 16 case-run records without a Provider call.

The machine-readable summary is
[dta-v22-practical-development.json](dta-v22-practical-development.json).

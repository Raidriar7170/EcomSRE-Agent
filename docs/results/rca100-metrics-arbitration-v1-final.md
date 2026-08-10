# RCA100 Metrics Arbitration v1 Final Result

Status: `RCA100_EVALUATOR_REPAIR_FINAL_REPORT_FROZEN_READY_FOR_PUBLICATION_REVIEW`

Evaluation method: `POST_LOCK_EVALUATOR_REPAIR_DISCLOSED`

Classification: `RCA100_EXTERNAL_M3_NOT_SUPPORTED`

## Post-lock Evaluator Repair Disclosure

Predictions were generated and locked in a one-shot, answer-blind RCA100 execution. After terminal lock, the frozen evaluator was found to misread the official mapping.json envelope. A separately authorized evaluator-only repair unwrapped the frozen task_to_case_id field. No Provider call, prediction rerun, M3 change, or case replacement was performed. Apart from the envelope extraction, the scorer, entity matching, statistics, and fixed denominator were unchanged.

PR #22 remains permanently `BLOCKED_PROTOCOL_DRIFT`. This repaired result does
not claim that the preregistered evaluator executed unchanged.

## Primary paired result

- Initial Root Entity correct: 16 / 103
- Final Root Entity correct: 10 / 103
- Point difference: -0.058252
- 95% paired bootstrap CI: [-0.106796, -0.019417]
- Exact McNemar p-value: 0.03125
- Root Damage / Rescue / Net: 6 / 0 / -6
- Root Damage Rate: 0.375000 (6 / 16)
- KEEP / OVERRIDE: 63 / 36
- Correct / Wrong Override: 0 / 36

## Secondary and execution

- Initial Pair correct: 0 / 103
- Final Pair correct: 0 / 103
- Pair Damage / Rescue / Net: 0 / 0 / 0
- Completed terminals: 99 / 103
- Provider attempts / original transport retries: 106 / 3
- Provider calls added by repair: 0
- Prediction reruns / case replacements: 0 / 0
- Official composite: `OFFICIAL_COMPOSITE_NOT_AVAILABLE`

The canonical JSON contains all frozen aggregate-only descriptive subgroups,
each with its denominator. No case-level prediction, answer, mapping, evidence,
reasoning, private path, credential, or Provider endpoint is public.

# DTA v2.3.2 Interview Brief

## Thirty-second version

The v2.3.1 successor exposed a real repository-acceptance bug: one residual `LOG_ERROR_CLUSTER` kind was missing from a static interpretation map, so the write-once study stopped halfway with a `KeyError`. I preserved both consumed attempts, introduced an enum-total `AnomalyInterpretationRegistryV232`, resolved log clusters through bound `LogCategoryV22` evidence, and added a 48-arm zero-Provider totality gate. A new independent 24-case × 2-arm study then completed once. It produced a frozen mixed result: novelty recall rose from `0.286` to `0.929`, but broad-domain accuracy was only `0.143` and two irreconcilable controls became false novelty.

## Personal contribution

- Reproduced the old `vx-113` failure against the exact preserved case and memory bytes.
- Added exhaustive interpretation for all 13 `GenericAnomalyKindV23` values, with an import-time enum equality check.
- Bound `LOG_ERROR_CLUSTER` to configuration, dependency, resource, or unknown using the actual log-category evidence; missing categories fail safely to `UNKNOWN`.
- Kept `SOURCE_COVERAGE_GAP` as coverage state rather than positive mechanism evidence.
- Routed strict and treatment arms through the same registry while preserving the v2.3.1 conflict-aware policy, Prompt, scorer, thresholds, and three-read budget.
- Built fresh, opaque 24-case bytes and a machine-checked admission matrix with required log-cluster coverage.
- Added a deterministic 24 × 2 totality preflight, truth-after-both-arms loading, counterbalanced execution, and a write-once sentinel/journal boundary.
- Preserved protocol failures and negative controls in the final result rather than tuning or rerunning.

## Evidence to cite

- Data gate: `DTA_V232_SUCCESSOR_EVALUATION_DATA_PASS` (24/24).
- Runtime gate: `DTA_V232_RUNTIME_TOTALITY_PREFLIGHT_PASS` (48/48, zero Provider calls and zero runtime failures).
- Pre-execution review: `Must Fix: 0 / Claim Accuracy: PASS`.
- Provider smoke: 8 cases, 3 Provider calls, zero repair, retry, parse failure, or authority violation.
- Final execution: 24 cases, 48 arms, execution count 1.
- Final measured terminal: `DTA_V232_CONFLICT_AWARE_DISCOVERY_MIXED_RESULT`.
- Recall: strict `4/14`; treatment `13/14`; conflict-prone treatment `7/8`.
- Root localization: `12/14`; broad-domain accuracy: `2/14`.
- Evidence-ref validity: `1.000`; false-novel rate: `2/10`.
- Registered-known and No-Incident controls: `4/4` and `3/3` in both arms.
- Action-authority violations, Agent writes, Runbook executions, Docker calls, and new live faults: zero.

## Claim boundary

Say that total interpretation closed the runtime acceptance bug and that conflict-aware discovery showed a measured acquisition advantage under this fixed replay study. Do not call the result positive effect: the frozen terminal is mixed. Do not hide the two Provider protocol failures, the `0/3` treatment irreconcilable-control accuracy, or the low broad-domain accuracy. Nothing in the study authorizes remediation or proves production/live-fault performance.

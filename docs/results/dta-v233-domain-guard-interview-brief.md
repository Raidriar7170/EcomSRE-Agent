# DTA v2.3.3 Interview Brief

## Thirty-second version

DTA v2.3.2 had two measured P0 defects: it usually localized the root service but classified the broad fault domain correctly only 2/14 times, and it converted all three irreconcilable controls into failure or novelty. I added a runtime-only `DomainProjectionV233`, typed contradiction witnesses, an `IrreconcilableGuardV233`, one shared-budget guard-directed read, and a minimal Provider synthesis layer whose mechanical fields are rebuilt from runtime evidence. A fresh 28-case × 3-arm study completed exactly once. Domain accuracy rose from `0.125` to `0.625`, root localization reached `1.000`, and the combined guard reached `4/4` irreconcilable accuracy with zero false novelty. The frozen result is still mixed because domain accuracy missed the predeclared `0.650` positive threshold by one case.

## Personal contribution

- Separated runtime root localization from broad-domain projection and made both mechanically bound into `ProvisionalIncidentReportV233`.
- Built deterministic evidence votes and combination bonuses for configuration, runtime, resource, dependency, and concurrency, with negative evidence and ambiguity returning `UNKNOWN`.
- Defined six typed contradiction-witness kinds and prohibited multi-service or multi-domain competition from acting as a hard-conflict shortcut.
- Required strong, coverage-satisfied evidence on both sides before `IRRECONCILABLE`; otherwise the guard stays open, uses at most one shared-budget read, or fails safely as insufficient coverage.
- Kept known and No-Incident terminals ahead of novelty discovery and suppressed Provider synthesis on irreconcilable controls.
- Reduced the Provider response to seven narrative fields; root, domain, evidence refs, witness state, confidence bounds, and `action_authority = NONE` remain runtime-owned.
- Preserved the exact v2.3.2 baseline, froze fresh opaque 28-case bytes, delayed truth until all three arms completed, and retained the one-shot sentinel and partial journal.
- Preserved two baseline protocol failures and the mixed terminal rather than changing the post-execution algorithm, data, Prompt, scorer, or thresholds.

## Evidence to cite

- Evaluation-data gate: `DTA_V233_EVALUATION_DATA_PASS` (28/28).
- Runtime gate: `DTA_V233_RUNTIME_PREFLIGHT_PASS` (84/84 paths, zero Provider calls and zero runtime failures).
- Provider smoke: `DTA_V233_PROVIDER_SMOKE_PASS`, execution count 1, 12 cases, two bounded real fixes, zero mechanical-field drift or authority violation.
- Pre-execution review: `Must Fix: 0 / Claim Accuracy: PASS`.
- Final execution: 28 cases, 84 runs, execution count 1, truth load count 28.
- Frozen measured terminal: `DTA_V233_DOMAIN_AND_GUARD_MIXED_RESULT`.
- Domain: baseline 2/16 (`0.125`), v2.3.3 10/16 (`0.625`); top-two recall 14/16 (`0.875`).
- Root localization: baseline 15/16 (`0.938`), v2.3.3 16/16 (`1.000`).
- Irreconcilable controls: baseline/domain-only 0/4, combined 4/4; combined false novelty `0.000`.
- Combined novelty recall, evidence-ref validity, strong-witness precision, and strong-witness recall: all `1.000`.
- Registered-known and No-Incident controls: 4/4 and 3/3 in every arm.
- Runtime exceptions, authority violations, Agent writes, Runbook executions, Docker calls, new live faults, and transport retries: zero.

## Claim boundary

Say that v2.3.3 closed the measured conflict-control defect on this fixed set and produced a large evidence-bound domain gain while keeping root localization and novelty recall perfect. Do not call it positive effect: exact domain accuracy was `0.625`, below the frozen `0.650` gate. Do not hide the two v2.3.2 baseline protocol failures, the six remaining `UNKNOWN` domains, or the intentionally minimal narrative synthesis. Nothing here authorizes remediation or proves production/live-fault behavior.

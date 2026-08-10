# Adaptive v2 candidate-3 Gate diagnosis

Classification: `POST_HOC_CONSUMED_TUNE_DIAGNOSTIC / NO_PROVIDER_CALLS / NOT_EXTERNAL_VALIDATION`.
Gate policy: `TRACKED_PRODUCTION_GATE_CONFIG` (`agent.json` SHA-256 `1684339a7caae35f3991cd8537e8a02de103ecc514ead59491ebb9aee4777913`).

## Finding

All 60 completed records used `DIRECT_RETURN`, including all 10 records marked unstable. The current control flow records some risk signals but does not make confidence, Metrics margin, or the aggregate unstable flag independently route-authoritative.

## Initial outcome

- Initial Root correct / wrong: 49 / 11
- Below direct / below low: 0 / 0
- Metrics rank / margin risk: 10 / 13
- Evidence weak / Logs opposition: 0 / 0
- Propagation conflict / strict Trace semantics: 0 / 0
- Indicator missing: 0

## Finite route simulations

- Policy A (`risk_count >= 2`): 3 escalations; 2 Initial-wrong captured; 1 Initial-correct escalated.
- Policy B (`risk_count >= 1`): 20 escalations; 10 Initial-wrong captured; 10 Initial-correct escalated.

These are route-only simulations over consumed TUNE features. They do not estimate Specialist or Final accuracy.

# Adaptive v1 offline diagnosis

Classification: `POST_HOC_DEVELOPMENT_DIAGNOSTIC`, `NO_PROVIDER_CALLS`, `NOT_EXTERNAL_INFERENCE`.

This report recomputes aggregates from the preserved, already-consumed OB/SS development records. It does not rerun any case and does not provide external inference.

## Execution order and Provider capacity

- Strong Single terminals: 120; Adaptive terminals: 120.
- Adaptive completed: 55; retained terminal failures: 65.
- All Strong Single work completed before Adaptive began: true.
- Wall-clock arm overlap: false.
- Strong Single window: 2026-08-08T18:25:38.243574Z to 2026-08-08T18:34:24.153600Z.
- Adaptive window: 2026-08-08T18:34:25.001601Z to 2026-08-08T18:44:56.826208Z.
- Provider attempts before Adaptive began: 122.
- First Adaptive HTTP 429: 2026-08-08T18:39:29.806475Z; prior cumulative attempts: 229.
- HTTP 429 episodes: 65; attempts: 130 (65 first attempts, 65 retries); recovered operations: 0; retry-issued then failed again: 65.
- Saved retry waits do not preserve whether the value came from a Provider header, so that source is not inferred.

The Provider-capacity and temporal-order effects are confounded with architecture. Fixed-denominator failures remain valid for the executed protocol, but they are not a clean architecture-only reliability comparison.

## Agent behavior

- Initial comparison coverage: 55 completed Adaptive cases; interpretation: `POST_HOC_SELECTION_BIASED`.
- Same Initial and Strong Single root: 48; different: 7.
- Both correct: 44; Strong Single correct / Adaptive Initial wrong: 0; Strong Single wrong / Adaptive Initial correct: 7.
- Real Gate route known: 56; unavailable: 64.
- The existing failure-reporting default assigned `VERIFY_BOTH` to 65 failed terminals; it is not treated as a real Gate route here.
- Escalation precision: 3/25; recall: 3/4.
- Fusion actions: {"KEEP_INITIAL": 25, "OVERRIDE_INITIAL": 0}; correct overrides: 0; wrong overrides: 0; guardrail activations: 0.
- Indicator actions: {"KEEP_MODEL_INDICATOR": 54, "KEEP_MODEL_INDICATOR_WITH_UNCERTAINTY": 1, "USE_DETERMINISTIC_TOP1": 0}; Pair success conditional on correct Root: 31/51.

Specialist, Fusion, and Indicator outputs exist only for the 55/120 completed Adaptive records. Their aggregates are `POST_HOC_SELECTION_BIASED`; failed-record semantic outputs are unavailable and are not imputed.

## v2 implications

Use the Strong Single-compatible Initial, make direct return the conservative default, replace LLM Fusion with deterministic Fusion, and keep Trace verification behind a strict latency/socket or propagation trigger. These are development recommendations, not external claims.

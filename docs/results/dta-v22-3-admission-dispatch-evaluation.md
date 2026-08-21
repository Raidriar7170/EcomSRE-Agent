# DTA v2.2.3 Evidence Closure and Deterministic Dispatch Study

- Phase: `EVALUATION`
- Provider model: `gpt-5.4-mini-2026-03-17`
- Cases: 16
- Runs: 64
- Full-study execution count: 1
- Uncaught exceptions: 0
- Agent writes: 0

## Four-combination metrics

| Combination | Exact | Macro-F1 | Resource-silent | Premature NO_INCIDENT | Diagnosis after read | Control | Provider calls |
|---|---:|---:|---:|---:|---:|---:|---:|
| MODEL_LEGACY | 0.750 | 0.600 | 0.000 | 1.000 | 0.667 | 1.000 | 32 |
| MODEL_CLOSED | 0.812 | 0.733 | 0.250 | 0.750 | 0.438 | 1.000 | 46 |
| AUTO_LEGACY | 0.750 | 0.600 | 0.000 | 1.000 | 0.667 | 1.000 | 16 |
| AUTO_CLOSED | 0.812 | 0.733 | 0.250 | 0.750 | 0.438 | 1.000 | 16 |

## Measured result terminal

`DTA_V22_3_NO_FIX_EFFECT_OBSERVED`

Neither the combined, admission-only, nor dispatch-only preregistered threshold
passed. Engineering completion is independent of this measured terminal.

## Factorial effects

- Admission main effect: resource-silent accuracy `+0.25`, premature
  `NO_INCIDENT` `-0.25`, control accuracy `+0.00`, mean reads `+0.50`, and
  `+13,374` pooled tokens.
- Dispatch main effect: oracle-path hit `+0.00`, empty-read rate `-0.0175`,
  diagnosis-after-read `+0.00`, exact completion `+0.00`, `-46` Provider calls,
  and `-33,866` pooled tokens.
- Exact-rate interaction: `0.00`.

## Reliability and execution boundary

- Post-repair protocol success: `1.0` for every combination.
- Protocol failures / transport retries / uncaught exceptions: `0 / 0 / 0`.
- Agent writes: `0`.
- Same case bytes across combinations: `true`.
- Truth load count: `1`, after all four case-local treatment runs.
- Full-study execution count: `1`; no optimization rerun occurred.

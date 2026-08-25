# DTA v2.3.1 conflict-aware evaluation error analysis

## Frozen outcome

The single authorized 24-case × 2-arm execution completed once. The measured
terminal is `DTA_V231_CONFLICT_AWARE_DISCOVERY_NOT_OBSERVED`; the frozen
artifact SHA-256 is
`0b7261322a56a03f072fab1e2d761e2d04f7f07be9bb95e052a035784d134e77`.
There was no Docker call, new live fault, Agent write, or Runbook execution.

Treatment novelty recall rose from `6/14` (`0.429`) to `9/14` (`0.643`), an
absolute gain of `0.214`. Conflict-prone recall was only `3/8` (`0.375`), so
the mixed threshold was not met. The positive thresholds also failed on total
recall (`0.643 < 0.70`), root localization (`0.571 < 0.65`), and broad-domain
accuracy (`0.214 < 0.55`).

## Evaluation-data contract failure

Runtime admission contradicted two fixed-set assumptions:

- `vx-005` through `vx-008`, designated genuinely unregistered pool/queue
  incidents, completed an existing dependency-latency support clause in both
  arms and ended `KNOWN_INCIDENT`.
- `vx-022` through `vx-024`, designated irreconcilable conflict controls, were
  intercepted by the known terminal before conflict classification. Treatment
  therefore reported zero `IRRECONCILABLE_CONFLICT` cases and conflict-control
  accuracy was `0/3`.

This violates the preregistered requirements that genuinely unregistered cases
must not accidentally complete a registered support clause and that all three
irreconcilable controls exercise explicit contradiction behavior. The artifact
is retained as one-shot evidence, but the engineering status is
`BLOCKED_DTA_V231_EVALUATION_DATA`. No input was repaired and the comparison
was not rerun.

## Model and protocol observations

- The treatment converted three conflict-prone novelty cases into valid
  competing-hypothesis reports; one additional case (`vx-002`) exhausted the
  bounded Provider protocol repair path and remained `PROVIDER_FAILED`.
- Report evidence-ref, residual-anomaly citation, and competing-hypothesis
  evidence validity were all `1.000` for emitted reports.
- Leading-hypothesis root validity was `0.889`, but broad-domain accuracy was
  `0.214`; the reports often preserved evidence correctly while assigning an
  overly generic or incorrect causal domain.
- `vx-017`, designated registered-known, was reported as novel by both arms;
  registered-known accuracy was `3/4` and treatment false-novel rate was
  `0.100`.
- Treatment used 13 discovery reads versus 11 for baseline, 6 Provider calls
  versus 8, and preserved 3 protocol repairs and 62,805 total tokens.

The defensible conclusion is narrow: the conflict-aware lane can produce
well-cited competing reports and removed hard-conflict terminals in this run,
but this fixed dataset did not support a clean estimate of conflict-aware
novelty discovery effectiveness.

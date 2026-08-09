# RCAEval RE2 v2 development result

State: `V2_PROVIDER_DEV_GATE_NOT_PASSED`

This is a development-only negative result. The deterministic F0 indicator tool gate passed on the 60-case DESIGN split, but the bounded Provider smoke stopped after 10 of 72 scheduled runs. Two v2 runs rejected an absolute path contained in a bounded log observation before an operation marker existed. Recovery correctly avoided another semantic attempt, but those failures could not satisfy the exact failure-stage gate. A separate Judge output also failed its typed schema.

The exact runtime implementation is preserved by commit `23703280c5371e166891e22a788471b1808e1f56`.

The stopped smoke contains 5 completed v1 reference terminals and 5 v2 terminals: 2 completed, 1 invalid schema, and 2 protocol violations. It made 29 Provider operations with no semantic or transport retry. Private evidence scanning found no persisted secret, raw Provider response, or local absolute path.

The protocol-required evaluation root lock was missing when smoke execution began. It was reconstructed create-once only after termination to bind the unchanged prerequisite locks, F0 selection, and schedules. It is explicitly marked as negative-gate evidence only and does not retroactively authorize the Provider operations.

No complete DESIGN comparison was produced. DEV_VALIDATION was not accessed or executed. No external holdout, generalization, superiority, production-readiness, or Adaptive Escalation claim is made.

The next safe action is human review followed by a separately versioned `v2-dev.1` proposal with new schedule/run identifiers. The frozen failed evidence must remain preserved and must not be mixed with a repaired protocol.

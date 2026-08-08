# RCAEval RE2 v2-dev.2 Development Protocol

Protocol: `rcaeval-re2-v2-dev.2`

Classification: `DEVELOPMENT_VISIBLE / DESIGN_SET / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`

This independent development protocol starts from the immutable PR #15 implementation head. PR #14 and PR #15 remain preserved failed-gate evidence.

The only runtime changes are a versioned six-arm schedule contract, a zero-Provider Admission Rehearsal, and the RCAEval CI `PYTHONPATH` repair. Each schedule row records a 1–6 global position plus a 1–3 family-local position derived from actual execution order. The frozen v1 adapter consumes only the family-local position.

The execution order is implementation commit, two passing implementation CIs, external evaluation-root lock, 72/360/480 Admission Rehearsal, Admission Lock, Provider-ready verification, 72-run Smoke, and—only after a passing Smoke Gate—the remaining DESIGN runs.

The 120-case DEV_VALIDATION split is metadata-only during admission and is not executed. RE2-TT and Single-first Adaptive Escalation are outside scope. No external superiority claim is made.

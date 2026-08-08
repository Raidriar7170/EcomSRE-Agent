# RCAEval RE2 v2-dev.2 DESIGN Summary

State: `V2_DEV2_PROVIDER_SMOKE_GATE_NOT_PASSED`

Classification: `DEVELOPMENT_VISIBLE / DESIGN_SET / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`

PR #14 and PR #15 remain immutable failed-gate evidence.

## Zero-Provider admission

- State: `V2_DEV2_ADMISSION_REHEARSAL_PASSED`
- Smoke admitted: 72/72
- DESIGN admitted: 360/360
- Reserved DEV_VALIDATION metadata admitted: 480/480
- Provider objects/calls/run attempts/operation attempts: 0/0/0/0

## Inherited F0

- Overall Coverage@6: 57/60 (0.9500)
- Memory Coverage@6: 10/10 (1.0000)
- Socket Coverage@6: 9/10 (0.9000)
- Formula re-selection: No
- Provider calls: 0

## Provider Smoke

- State: `V2_DEV2_PROVIDER_SMOKE_GATE_NOT_PASSED`
- Terminalized: 72/72
- v1 reference terminal status: 34 COMPLETED / 2 PROVIDER_FAILURE
- v2-dev2 terminal status: 33 COMPLETED / 3 PROVIDER_FAILURE
- v2 completion: 33/36 (0.9167), below the required 35/36
- Provider operations: 222
- Known tokens: 614018
- Positive known-token accounting gate: Not passed
- Privacy / Judge schema / failure-stage coverage: Passed

## Boundary

DEV_VALIDATION values and case directories were not accessed, and validation was not executed. RE2-TT was not accessed. No external superiority claim is made.

The next action is human review of the negative Smoke Gate only. Candidate Freeze Review is not eligible, and validation remains unauthorized.

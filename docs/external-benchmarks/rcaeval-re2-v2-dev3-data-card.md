# RCAEval RE2 v2-dev.3 Development Data Card

Status: `DEVELOPMENT_VISIBLE / DESIGN_SET / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`

- RE2-OB: 90 development cases with metrics, logs, and traces.
- RE2-SS: 90 development cases with metrics and logs; traces remain forbidden.
- DESIGN: the inherited 60-case split, with 30 cases per system.
- Provider Smoke: 12 DESIGN cases across all six frozen variants, for 72 scheduled runs.
- DESIGN schedule: 60 cases across all six variants, for 360 scheduled runs.
- DEV_VALIDATION: 120 reserved cases and 480 metadata rows; values and case directories are forbidden in this task.
- RE2-TT: forbidden.

The inherited F0 expectation remains Overall Coverage@6 57/60, Memory 10/10, and Socket 9/10 with zero Provider calls. Formula re-selection is forbidden.

Public artifacts are aggregate-only. They exclude case and run identifiers, request and response bodies, evidence text, service endpoints, credentials, and private paths. Attempt and operation journals remain outside Git.

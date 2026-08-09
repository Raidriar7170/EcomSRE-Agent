# RCAEval RE2 v2-dev.2 Development Data Card

Status: `DEVELOPMENT_VISIBLE / DESIGN_SET / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`

- RE2-OB: 90 development cases; metrics, logs, and traces.
- RE2-SS: 90 development cases; metrics and logs; traces forbidden.
- DESIGN: the inherited 60-case split, 30 cases per system.
- Provider Smoke: 12 DESIGN cases and all six variants.
- DEV_VALIDATION: 120 cases reserved; metadata-only admission, with values and case directories forbidden in this task.
- RE2-TT: forbidden.

The inherited F0 expectation is Overall Coverage@6 57/60, Memory 10/10, and Socket 9/10 with zero Provider calls. Formula re-selection is forbidden. Public outputs are aggregate-only and must not contain case IDs, run IDs, raw evidence, raw Provider output, private paths, endpoints, or credentials.

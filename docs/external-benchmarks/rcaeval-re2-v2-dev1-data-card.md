# RCAEval RE2 v2-dev.1 Development Data Card

Status: `DEVELOPMENT_VISIBLE / DESIGN_SET / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`

The version reuses the frozen RE2-OB and RE2-SS development dataset binding and the unchanged 60-case DESIGN split. The 120 reserved DEV_VALIDATION rows were not opened by this task.

## Locked sources

- RE2-OB: 90 development cases; metrics, logs, and traces.
- RE2-SS: 90 development cases; metrics and logs; traces forbidden.
- DESIGN: 60 cases, 30 per system.
- DEV_VALIDATION: 120 cases reserved and not accessed.
- RE2-TT: forbidden and not accessed.

## Inherited F0 reverification

- Overall Coverage@6: 57/60 (0.9500)
- Memory Coverage@6: 10/10 (1.0000)
- Socket Coverage@6: 9/10 (0.9000)
- Formula re-selection: No.
- Provider calls: 0.

Only schedule-selected DESIGN telemetry was opened for F0 and runtime evaluation. Public outputs are aggregate-only and contain no case-level records.

# Product v0.2 unknown-fault profile calibration

Terminal: `BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE`

- Live calibration attempts: `0`
- Selected observer-visible root: `NONE`
- Attempt baseline restoration: `False` (no attempt started)
- Post-run outer baseline restored: `True`
- Owned Demo cleanup: `CLEAN`
- Action authority: `NONE`
- Agent writes: `0`
- Runbook executions: `0`

This public report intentionally excludes the evaluator-only flag key, injected
numeric values, private control identifiers, truth mechanism, and injection
commands. The private report is bound only by SHA-256.

## Candidate family contract

The only candidate family was an observer-visible checkout queue-overload
symptom. A candidate could pass only after real telemetry supported Open-World
admission from at least two evidence sources, with zero Core or active Extension
absorption, exact restoration, and clean owned-runtime cleanup. The expected
root and final fault profile were intentionally left unfrozen until those
conditions were observed.

## Blocker boundary

The Product baseline job stopped with `BASELINE_INSUFFICIENT_WINDOWS` before
the first fault-control attempt. Consequently, this run establishes neither
profile observability nor profile non-observability. It establishes only that
the authorized calibration could not reach its first admissible attempt under
the frozen campaign.

The one calibration campaign is consumed. Per the Goal, the pilot stops here;
it does not repair or retry this run, change the baseline policy, or switch to
an unplanned fault mechanism.

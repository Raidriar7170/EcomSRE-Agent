# Product v0.2.3.2.1 Interview Brief

## Problem

The predecessor consumed a live traffic attempt before building a valid typed
Runtime request. The successor repaired canonical Tool run IDs, moved typed
request validation before Sandbox startup, separated Infrastructure Sessions
from Traffic Attempts, and made cleanup closure fail closed.

## Verified outcome

The repaired harness passed one live `10 / 10` preflight and then one formal
`30 / 30` healthy workload with zero retries. Runtime authority, Product
restart, fresh Runtime provenance, queue/Baseline continuity, source-state
immutability, and Product/Demo cleanup all passed.

The formal Product episode did not complete. It created one successor Incident,
then the only Diagnosis job failed with `INTERNAL_CONTRACT_FAILURE`. The frozen
terminal is `BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE`; there is no
measured No-Fault result.

## Engineering contribution

- Recovered and byte-bound the blocked predecessor without rewriting it.
- Repaired request construction and attempt-consumption boundaries.
- Added one-shot reservation, canonical recovery checkpoints, authoritative
  state observation, and typed clean/blocker closure.
- Prevented a Diagnosis failure from being reported as a traffic failure or a
  measured Product result.
- Preserved one successful traffic execution, one Incident, the failed
  Diagnosis job, exact zero-authority counters, and clean resource closure.

## Truth boundary

It is accurate to claim a repaired traffic harness, successful healthy traffic,
and fail-closed formal evidence. It is not accurate to claim end-to-end
No-Fault acceptance, a successful Diagnosis, Knowledge-Loop readiness, or
production autonomy.

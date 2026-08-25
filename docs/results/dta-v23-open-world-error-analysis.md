# DTA v2.3 Open-World Discovery — Error Analysis

Evaluation acceptance: `VALID / FINAL_REVIEW_PENDING`

## Frozen result

The Goal-defined 24-case × 2-arm comparison completed once and froze
`DTA_V23_OPEN_WORLD_DISCOVERY_NOT_OBSERVED`. Novelty recall and root
localization were both 6/14 (`0.429`), below the `0.50` threshold required even
for a mixed result. Broad-domain accuracy was 5/14 (`0.357`). The valid study
will not be rerun for metric optimization.

The JSON semantic artifact SHA-256 is
`888d6242743433e02b2aebdaa292b531f1844ffc07f392887634501e882476ea`;
the committed file SHA-256 is
`1c6fb59f260c87accd3d11d193461e9f9a2f725f2315209d934e659d8f69e079`.

## What the isolation repair proved

- All 24 closed arms carried no Residual Graph, Novelty Gate, Negative
  Coverage, generic discovery read, or provisional report state.
- Both arms of every pair shared the same actual v2.2 known-admission binding.
- All four registered-known controls were exact in both arms, with zero
  accuracy-drop cases.
- All four counterfactual pairs were checked as data-level TARGET/CONTROL
  service swaps over equivalent typed captures.
- Evidence-reference validity, residual citation validity, report schema
  validity, and alternative-hypothesis completeness were all `1.000`.
- Agent writes, Runbook executions, Docker calls, new live faults, and
  action-authority violations were all zero.

These facts establish the comparison and safety boundaries. They do not turn
the negative measured result into an effect claim.

## Novelty misses

Eight of fourteen novelty incidents did not reach an unregistered-incident
terminal:

| Cases | Evaluator class | Frozen open-world terminal |
| --- | --- | --- |
| `ow-001`, `ow-002` | hidden configuration | `CONFLICTING_EVIDENCE` |
| `ow-009`, `ow-010` | hidden dependency latency | `CONFLICTING_EVIDENCE` |
| `ow-011`–`ow-014` | genuinely unregistered concurrency patterns | `CONFLICTING_EVIDENCE` |

The conflict rule behaved fail-closed: after one bounded read, the graph still
contained strong incompatible service/domain interpretations, so it refused to
mint novelty. On this set that conservatism was too broad and suppressed all
four genuinely unregistered synthetic cases. This is the principal recall
failure.

## Detected novelty quality

Six hidden incidents reached `UNREGISTERED_INCIDENT_SUSPECTED`: two runtime,
two CPU, and two memory cases (`ow-003`–`ow-008`). All six localized the
expected opaque service. Five selected the expected broad domain; `ow-008`
returned `UNKNOWN` instead of `RESOURCE`. The frozen secondary semantic rubric
graded five reports at mechanism level and one as unrelated.

The Provider was called seven times: six true novelty reports plus the false
novel report on `ow-023`. It used 14,796 input tokens, 2,970 output tokens,
17,766 total tokens, zero protocol repairs, and zero transport retries.

## Control behavior

- Registered-known exact accuracy: 4/4 closed and 4/4 open.
- No-Incident accuracy: 2/3 closed and 2/3 open; `ow-019` remained
  `INSUFFICIENT_EVIDENCE` in both arms because its replay lacked complete
  anomaly-evaluable metric coverage.
- Insufficient/conflict combined accuracy: 1/3. `ow-022` remained fail-closed
  as conflict; `ow-023` was a false novel; `ow-024` was admitted as
  No-Incident.
- Aggregate false-novel rate across the ten control cases: 1/10 (`0.100`).

Known-world preservation therefore held, while open-world recall did not.

## Discovery process

The open arm averaged `0.708` discovery reads per case. All fourteen novelty
cases used a discovery read. Across 17 reads, empty-read rate was `0.353`,
generic-anomaly yield was `0.529`, first useful evidence mean ordinal was
`1.0`, and Negative Coverage was used eight times. Source distribution was
Logs 8, Traces 6, and Resources 3.

## Predecessor evidence

The original mixed-result artifact remains byte-for-byte preserved as
`INVALID / REVIEW_REQUIRED` because it did not implement the Goal-defined arm
isolation and counterfactual contract. A later schedule attempt is separately
retained as `PROTOCOL_BLOCKED / INVALID`: it completed two case pairs, made
zero Provider calls, and produced no final artifact before duplicate bootstrap
evidence failed closed. Neither predecessor is counted as the valid fixed
study.

## Bounded conclusion

The MVP engineering surfaces are implemented and the valid study proves
known-world preservation, typed evidence binding, and zero action authority.
It does not show an open-world discovery effect on the frozen evaluation set.
Production ontology learning, autonomous registration, remediation authority,
and live-fault generalization remain out of scope.

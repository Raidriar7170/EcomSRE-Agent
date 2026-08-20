# DTA v2.2.1 Evidence-Acquisition Error Analysis

## Classification counts

Counts below cover the single 48-run fixed study.

| Required class | Count | Evidence |
| --- | ---: | --- |
| Premature abstention redirected to READ | 0 | No final-study run proposed an ABSTAIN that reached the runtime redirect branch |
| Premature abstention repeated | 0 | No redirect occurred; the one-redirect loop guard was not triggered |
| READ followed by correct Diagnosis | 0 | No read-bearing run ended in a correct `DIAGNOSED` terminal |
| READ followed by wrong Diagnosis | 0 | No read-bearing run ended in any `DIAGNOSED` terminal |
| READ followed by ABSTAIN | 15 | Flat Legacy 2; Flat Gate 6; Planner Legacy 1; Planner Gate 6 |
| READ source empty | 17 events | All were typed `SUCCESS_EMPTY` |
| READ source unavailable | 0 events | No unavailable, timeout, or schema-failure read outcome |
| Semantic admission failure | 1 run | Planner Legacy, case `e07` |
| Protocol failure | 18 runs | 16 `INVALID_DECISION_SHAPE`, 1 `SEMANTIC_ADMISSION_FAILED`, 1 generic `PROTOCOL_FAILED` |
| Transport failure | 0 | Transport retries were also 0 |

There were 25 read events across 23 read-bearing runs. Sources were Changes 11,
Logs 6, Traces 6, and Runtime 2. Outcomes were 17 `SUCCESS_EMPTY` and 8
`SUCCESS_NONEMPTY`. The 23 read-bearing runs ended as 15 ABSTAIN, 2
NO_INCIDENT, and 6 protocol failures; none ended as Diagnosis.

## Did the policy make the Agent gather evidence?

Partially, but not in the preregistered sense. Flat Gate increased read-bearing
cases from 4/12 to 10/12 and bootstrap-insufficient coverage from 2/8 to 8/8.
Planner Gate increased read-bearing cases from 3/12 to 6/12, but its
bootstrap-insufficient coverage moved only from 3/8 to 4/8. The runtime
redirect itself fired zero times in the final study: the policy-aware prompt
caused some proactive reads before any ABSTAIN proposal reached the gate.

Because both arms had to satisfy every threshold, the measured terminal is
`DTA_V22_1_NO_EVIDENCE_ACQUISITION_EFFECT_OBSERVED`.

## Did that evidence improve correctness?

No. Exact completion moved from 3 to 2 for Flat and from 1 to 0 for Planner.
Mechanism Macro-F1 remained 0 for all four combinations. Most importantly,
no read-bearing run produced a Diagnosis, correct or otherwise. Extra reads
mostly led to ABSTAIN; 68% of read events (17/25) were empty.

This distinguishes evidence execution from evidence use. The Provider could
select a bounded read, but neither arm converted the resulting memory into an
admissible incident diagnosis on this run.

## Did Planner-Lite use the accumulated ledger better than Flat?

No Planner-specific interaction was established. Planner Gate and Flat Gate
both had diagnosis-after-read 0 and mechanism Macro-F1 0. Planner Gate also
read fewer bootstrap-insufficient cases than Flat Gate (4/8 versus 8/8) and
completed no case exactly.

The ledger remained runtime-owned and correctly separated from policy
feedback, but this study provides no evidence that the Provider used the
accumulated ledger more effectively.

## Token and latency cost

| Comparison | Extra Provider calls | Extra tokens | Extra latency |
| --- | ---: | ---: | ---: |
| Flat Gate minus Flat Legacy, all 12 cases | 7 | 10,468 | 12,511.21 ms |
| Planner Gate minus Planner Legacy, all 12 cases | 3 | 8,455 | 3,810.33 ms |
| Flat control subset | 3 | 4,341 | not separately preregistered |
| Planner control subset | 1 | 751 | not separately preregistered |

These costs include semantic repairs and all Provider calls. The treatment did
not consume a policy-feedback call in the final study because no redirect
occurred.

## Did the policy damage No-Incident or legitimate abstention behavior?

Yes, at the allowed but material control boundary.

- Flat No-Incident accuracy stayed at 0.5, but abstention accuracy fell from
  1.0 to 0.5. Unnecessary reads on the four controls rose from 0.5 to 1.0.
- Planner abstention accuracy was already 0 and did not fall further, while
  No-Incident accuracy fell from 0.5 to 0. The control unnecessary-read rate
  remained 0 for both Planner combinations.
- Combined No-Incident plus abstention control accuracy fell by 0.25 for both
  arms, exactly at the maximum permitted by the separate quality rule.

## Protocol shape remains a larger failure mode

Eighteen runs failed protocol admission, including 16 invalid decision shapes.
The post-repair protocol-success rate reached 1.0 in three combinations, but a
valid Provider response could still fail later controller admission or exhaust
the bounded decision loop. This study therefore does not attribute the weak
quality result solely to premature abstention.

## Safety conclusion

There were zero transport failures, duplicate reads, uncaught runner
exceptions, and Agent writes. No Docker or Runbook command was executed. The
negative effectiveness result is valid study evidence, not a safety failure.

Independent review subsequently repaired logical Provider-call accounting for
a terminal transport failure. No measured run exercised that path, so the
frozen study result did not change and was not rerun.

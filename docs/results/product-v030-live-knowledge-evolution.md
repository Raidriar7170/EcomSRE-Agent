# Product v0.3 — blocked at the measured control set

ECOMSRE_PRODUCT_V030_BLOCKED_CONTROL_SET / REVIEW_REQUIRED.
The completion terminal is not minted. PR #88 remains Draft; no merge.

## Actual results

| Case | Seed | HTTP checkout results | Required Diagnosis | Actual Diagnosis |
| --- | ---: | --- | --- | --- |
| N0-A | 30001 | 30/30 success | NO_INCIDENT | OPEN_WORLD / RESOURCE — failed |
| N0-B | 30002 | 30/30 success | NO_INCIDENT | NO_INCIDENT — passed |
| C1 | 30003 | 10/10 expected HTTP 500 | CORE_KNOWN / CONFIGURATION_ERROR | OPEN_WORLD / RESOURCE — failed |

All three used environment env-1c32961cf738f50ae03ab524 and Baseline
base-b5f7e2734141be7396e7d57c. Their capability limitations and forbidden-token
lists are empty; supporting refs resolve. The five-window DEMO_ONLY Baseline
completed 5/5 windows with 30/30 healthy transactions. Queue lag was zero
before/after; healthy queue mean 0.65 and standard deviation 0.1224744871391589
leave the frozen threshold at 20. No detector or Core clause was retuned.

N0-A's captured Kafka resource window contains five samples over ten seconds:
506,048,512 → 514,895,872 bytes, slope 884,736 bytes/s. Baseline slope is
63,979.52 bytes/s. These values satisfy the existing v0.2.4 generic memory-trend
guard; the arithmetic is correct. This is a measured healthy-control false
positive, not missing telemetry. Its underlying runtime cause is unproven.

C1 really enabled the payment fault and recorded its ChangeEvent. Its persisted
payment error metric is 0.015837263832223995 against Baseline 0.0033800055891482967:
the delta is below the frozen 0.05 requirement. This is NOT the observed failure
fraction of the ten checkout transactions (which was 100%). The inherited
query floors its rate denominator at 1 request/s and averages 31 rolling
five-minute points, diluting low-traffic errors. The observed WARN message
"Payment request failed. Invalid token." is not a frozen configuration-error
log pattern. Neither existing CONFIGURATION_ERROR clause is established. Kafka
memory growth and the recent payment change instead produce an Open-World
report. Logs/Traces truncation remains visible in the stored snapshots even
though the existing capability-limitations list is empty.

C1 automatically created family-fd3d3a6060a85fb1eebc92c1, status ACCUMULATING,
with its single Incident. This is not a queue-backlog target family and is
retained, not deleted or reclassified. N0-A's fault=none label prevented family
ingestion. No formal case was retried.

## Completed preparation, with its limits

Phase A reached ECOMSRE_PRODUCT_V030_QUEUE_TELEMETRY_READY: one real off/on/off
preflight, lag 302 and three distinct elevated source samples, then queue off
and drained. kafka_consumer_group_lag_ratio maps to fraud-detection with COUNT
semantics. Full Product acquisition re-read the same captured fault window
with no control-token leakage, without another fault or Incident.
Historical truncation/coverage gaps and failed setup attempts remain preserved.

The three missing pinned ARM64 images were acquired and verified under explicit
authorization. An independent private 28-service lock was used; historical locks
and pinned upstream source are unchanged. The first preparatory Baseline is
retained but was not used after the broker telemetry configuration changed.

The second runtime's non-Incident broker probe passed with 3/3 healthy
transactions and no capability limitations/leaks. Real
KafkaApis.handleProduceRequest method spans are execution evidence, not
asynchronous ACK or end-to-end success. JMX supplies Produce TotalTimeMs
95thPercentile; Kafka may not fall back to method-span latency. The existing
native count/failed counters describe partition append and unexpected
append failures (expected errors excluded), not the same sample population as request latency or a complete
ACK-error ratio.

Knowledge-layer repairs prove exact queue-aware Metrics action completeness
from bound typed snapshots and Baseline, including duplicate Evidence refs and
raw/memory consistency. A missing, sparse, truncated or mismatched read is not
a conclusive negative. Core routing's absent provisional report is not evidence
that the queue symptom is absent. Shadow keeps reachability distinct from
selected-source completeness. These are tested implementations, not measured
family/mining/Shadow success.

## Safety and remaining decision boundary

P1/P2/P3 and H1 were not started. No ACCEPT_AS_NEW, registration draft, Shadow
Evaluation, Promotion or ACTIVE extension exists. Conditional standing review
authorization was not exercised because its measured gates did not pass.

Both owned runtimes were cleaned. Each cleanup removed 28 containers, one
network and three temporary volumes; final owned counts are zero and non-owned
inventory is unchanged. Private Product databases and all failed-run evidence
remain. Final cleanup: 2026-09-03T04:16:43.270977Z. Product action/remediation
authority is NONE; Provider calls, Agent writes and Runbook executions are zero.
Harness fault enables: one queue preflight, one C1 payment control; no positive
queue case.

Fresh affected-scope validation: 1,080 tests passed in 27.78 seconds; focused
Ruff and mypy passed. This is not the full repository suite, which remains
reserved for a genuinely merge-ready result. Neither green tests nor an
empty capability-limitations list overrides the two failed measured controls.

Final review found and fixed Shadow's optional-action ordering: reconstruction
now retains Core catalog order followed by queue, preserving the original
Memory hash. A fresh 125-test check passed; independent read-only reconstruction
matches all three actual control hashes exactly. Review passed for Draft
blocked-results publication only, with no remaining Must Fix or Should Fix.

## Post-control engineering repair (offline only)

The v0.3 error-ratio query now divides by the unchanged positive request rate;
zero traffic is unsupported, not an invented healthy ratio. The old v0.2.3
payload and these persisted control results are unchanged. This removes the
known denominator-floor defect but does not establish a new live C1 result or
remove the separate rolling-window limitation.

CI run 33715012274 failed at the historical PR-B source hash before full tests.
The repair preserves both frozen manifests and binds the old source blobs at
the Goal base plus the exact reviewed queue-only successors. Other files,
Core query shapes, thresholds, support clauses and provenance checks remain
unchanged. Both PR-B and PR-C verifier CLIs now pass locally. Focused regression:
139 passed, one existing historical-label test skipped; Ruff and mypy passed.
This is not a full-suite or live-control success claim.

N0-A's independent audit found no established alignment/unit bug. Raw Prometheus
source timestamps were not retained, so source-sample independence is not
claimed from the typed five-point record. Its measured endpoint arithmetic
still crosses the existing guard. Full-mode resource-noise semantics remain
unresolved. Do not raise thresholds, rewrite log symptoms, erase these cases
or repeat windows until a pass appears.

Machine state: [result](product-v030-live-knowledge-evolution.json).
Family boundary: [summary](../analysis/product-v030-family-and-rule-summary.json).

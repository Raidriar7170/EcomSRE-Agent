# Product v0.3 — live-005 knowledge evolution and H1 pass

ECOMSRE_PRODUCT_V030_H1_EXTENSION_KNOWN_PASS. This is the measured, cleaned
pre-integration snapshot at 2026-09-03T16:25:35Z. The complete Goal terminal is
not preclaimed: final independent review, one exact-head full CI and merge are
recorded on [PR #88](https://github.com/Raidriar7170/EcomSRE-Agent/pull/88).
No duplicate local full-suite run is planned. Historical green CI remains tied
to its original head; it does not validate the later changes.

## Current measured result: live-005

The user explicitly authorized this one append-only correction loop in the same
Goal, branch, worktree and PR. It uses a fresh private Product environment and
Baseline; live-002/live-003/live-004, including live-004's failed H1 root gate,
remain unchanged. No detector threshold, clustering threshold, Core clause or
Shadow criterion was relaxed.

Environment `env-be0f718912d15af3f5e6523c` and Baseline
`base-bee559ecbbe6395f56814f3e` bind all seven cases. The Baseline SHA is
`99347dc9b6f499d18c6ae6959b7b460743869bf8b1a59f3eb8510ff9ba57c8db`:
5/5 DEMO_ONLY windows, 30/30 healthy transactions, all four Resource services.
Queue mean 0.75/stddev 0 keeps the unchanged threshold at 20.

| Case | Traffic | Measured result | Full gate |
| --- | --- | --- | --- |
| N0-A, seed 30001 | 30/30 healthy | NO_INCIDENT | PASS |
| N0-B, seed 30002 | 30/30 healthy | NO_INCIDENT | PASS |
| C1, seed 30003 | 10/10 expected HTTP 500 | CORE_KNOWN / CONFIGURATION_ERROR / payment | PASS; queue-negative CONCLUSIVE |
| P1, seed 31001 | 3/3 | OPEN_WORLD / CONCURRENCY / fraud-detection | PASS |
| P2, seed 31002 | 3/3 | OPEN_WORLD / CONCURRENCY / fraud-detection | PASS |
| P3, seed 31003 | 3/3 | OPEN_WORLD / CONCURRENCY / fraud-detection | PASS |
| H1, seed 32001 | 3/3 | EXTENSION_KNOWN / kafka-queue-backlog / fraud-detection | PASS, including exact family-root equality |

C1 candidates are checkout/payment/fraud-detection, not Kafka. Payment support
resolves; fraud queue lag is 0 < 20, Runtime is running/healthy, the queue anomaly
is absent and the intended conjunction is conclusively false.
SOURCE_LOGS_COVERAGE_GAP and SOURCE_TRACES_COVERAGE_GAP remain disclosed.
The current N0-A/N0-B six-source leakage gate binds their actual Evidence and the
successful Baseline result bytes; it does not substitute an old acquisition for
current coverage. All seven cases have resolving support and no control-token
leakage, and each ended with flags restored and lag drained.

The actual P1/P2/P3 IDs are `inc-1420a2683f311a82569dd08b`,
`inc-c34a9e9a1bc2eb1efb67c0f5`, and `inc-3f6e97291f65ac20d6c4daa6`.
They form only `family-60f955e2c8db53bec4200cc6`: three members/windows,
REVIEW_READY before review, root consistency 1.0, six evidence sources, and
pairwise similarities 0.80612244898 / 0.83 / 0.973195876289. N0/C1 are excluded.
All three report and fingerprint roots are fraud-detection; their shared
anomalies are queue lag and latency in domain CONCURRENCY. Typed queue values
are 258.8571 / 303 / 274.1429, not claims about instantaneous peaks.

One ACCEPT_AS_NEW and one Promotion execute the user's prior authorization;
their notes explicitly do not claim a fresh manual inspection. Runtime selected
`core:RUNTIME_HEALTHY + ga:METRIC_QUEUE_LAG_OUTLIER` from the actual three-positive,
three-negative matrix, not from a Codex-authored clause. Draft
`registration-df79efaa6066bd02075eb80c` is DECLARATIVE_READY with recall 1.0,
FPR/Core overlap/No-Incident FPR 0 and two sources. All strict Shadow metrics
pass: recall/ref-validity/reachability/counterfactual-consistency 1.0, all
error/overlap/authority-violation counts 0, source-failure safe. Of 14 outcomes,
13 are evaluated; OTHER_EXTENSION is NOT_AVAILABLE for this first extension,
not an actual overlap experiment.

Promotion creates one ACTIVE registry entry/version 1 and marks the family
PROMOTED. H1 `inc-0c1ee61b8a5296759077661c` matches that extension with queue
value 303, exact fraud-detection majority-root equality, no provisional report
and no new family. Its support contains the actual queue Metrics and Runtime
refs. The DB retains seven Incidents/Diagnoses, one three-member family and one
each of review, draft, Shadow, Promotion and ACTIVE registry entry.

Two pretraffic setup errors and one failed first pre-Baseline request are
preserved. No Baseline or Incident existed at that failure. A bounded diagnostic
then observed healthy owned Runtime, low lag and HTTP 200 cart/checkout; it does
not establish the first request's root cause. One reviewed same-runtime recovery
bound the exact authority, environment, data root and empty pre-Baseline state,
preserved the old snapshot, and used the existing snapshot rotation. It completed
30/30 and the only live-005 Baseline job. The first failed result was not
overwritten. A later audit-path error was corrected offline to explicitly bind
the resumed Baseline; no control was rerun.

Cleanup is CLEAN: 28 containers, one network and three temporary volumes removed,
final owned counts zero, non-owned inventory unchanged. Private DBs and all failed
evidence remain. Cumulative Goal counts are five runtime starts, four complete
control sets/twelve controls, one queue preflight, four payment enables, six
discovery positives and two H1 executions; each historical H1 keeps its own gate.
Provider calls, Agent writes, Runbook executions and Product action/remediation
authority remain zero/NONE. This is a bounded local Demo, not production or
autonomous-remediation evidence.

Fresh focused validation: 221 passed, one existing Starlette warning, 6.52s;
focused Ruff and seven-source-file mypy pass. Independent source, live-evidence
and pre-integration document review passes with Must Fix 0 and Should Fix 0.
Forty previously bound historical files were freshly verified unchanged; this
is not an exhaustive historical-directory comparison. Before Shadow, 19 declared
frozen/input files (7,761,792 bytes) were freshly hashed in 11.396ms, no cache or
fallback. This is FROZEN_ASSET_VERIFIED, not repository-wide closure. The final
exact-head CI/merge outcome belongs to the linked PR completion record.

## Preserved history: offline correction after live-004

The sections below retain the earlier observations at their stated timestamps;
their then-blocked lifecycle statements are not the current live-005 result.

The original Goal permits ordinary implementation fixes in the same PR. After
the measured H1 failure, Product root selection was aligned with the existing
domain classifier: when exactly one service owns the residual anomalies that
select the report domain, use that service. Otherwise retain the existing
multi-service fallback. This is a generic reporting consistency fix, not a
new causal proof, service-name exception or detector/threshold change.

Exact read-only P1/P2/P3 replays first failed for the checkout/fraud root mismatch,
then passed with the unchanged Memory hashes, evidence refs and capability
limitations. The recomputed, unpersisted roots agree with H1's observed fraud
root. Historical P1/P2/P3 diagnoses and fingerprints, the checkout-majority
family, registry version 1 and H1 CASE_GATE_FAILED all remain unchanged.
These replays do not replace measured cases or make the original H1 gate pass.

Two-file checks passed 30 tests; related Product v030/v024 and incident/knowledge
regressions passed 218 tests in 6.04s (overlapping, not additive), with the one
existing Starlette warning. Focused Ruff and one-source-file mypy passed.
Independent review passed with Must Fix 0, explicitly for the offline correction.
No new runtime, fault, Incident, registration version, full suite or CI cycle
was executed. The full Goal remains blocked on fresh end-to-end root-consistent
evidence; the fixed live-004 history cannot be rewritten to supply it.

## Preserved live-004: through Promotion and the failed H1 gate

The two authorized Product repairs passed focused replay against the exact
retained live-003 N0-A/N0-B/C1 evidence before the single new full-mode runtime.
The old outcomes and evidence were not rewritten. Isolated ten-second memory
growth no longer independently admits a Product residual: same-service memory
pressure logs, restart/unhealthy Runtime, error Metrics, localized error Traces,
or a second independent persistent Resource window must corroborate it.
Resource records, numeric thresholds and frozen Core memory-leak clauses remain
unchanged; growth-plus-log/restart regressions pass. Bridge and Knowledge shadow
reconstruction share this Product policy.

C1 uses checkout/fraud-detection/payment candidates, not Kafka. Its queue-negative
gate checks actual payment support, complete low queue Metrics, healthy fraud
Runtime and a conclusively false queue clause. Unrelated Logs/Traces coverage
gaps remain disclosed, not reclassified as complete.

The new private flagd absolute bind changed resolved Compose SHA, so the allowed
fallback built exactly one new five-window DEMO_ONLY Baseline:
`base-e7679c5fc708af40c4ba057e`, SHA
`810b328c68ce68830fef345bfe45a82ae217cfe417559cd88a099ff9c6222328`, environment
`env-3ed62ab80d67201580ca7764`. It has 5/5 windows, 30/30 healthy transactions and
all four Resource services. Queries, sampling and Baseline semantics did not
change; queue mean 0.7/stddev 0.1 leave the threshold at 20. The original
`base-26bcdd17ce69313c9a587efa` remains intact. A first preparatory transaction's
HTTP 504 and the existing bounded resumed probe's 3/3 success are both retained;
neither created an Incident, Baseline or fault.

| Case | Measured result | Full case gate |
| --- | --- | --- |
| N0-A, seed 30001 | 30/30 healthy; NO_INCIDENT | PASS |
| N0-B, seed 30002 | 30/30 healthy; NO_INCIDENT | PASS |
| C1, seed 30003 | 10/10 expected HTTP 500; CORE_KNOWN / CONFIGURATION_ERROR / payment | PASS; queue-negative CONCLUSIVE |
| P1, seed 31001 | 3/3; OPEN_WORLD / CONCURRENCY; fraud queue 303 | PASS |
| P2, seed 31002 | 3/3; OPEN_WORLD / CONCURRENCY; fraud queue 259.7143 | PASS |
| P3, seed 31003 | 3/3; OPEN_WORLD / CONCURRENCY; fraud queue 259.7143 | PASS |
| H1, seed 32001 | 3/3; EXTENSION_KNOWN / kafka-queue-backlog / fraud-detection | FAILED: root differs from family majority |

ECOMSRE_PRODUCT_V030_CONTROL_SET_READY is measured. C1 queue lag is 0 < 20,
fraud Runtime is running/healthy, and the intended queue-plus-Runtime clause is
false with complete required coverage. SOURCE_LOGS_COVERAGE_GAP and
SOURCE_TRACES_COVERAGE_GAP remain. All seven cases' support refs resolve and
leakage lists are empty; their flags were restored and queues drained.

The three positives formed `family-cca68e10afe10fcc5407b3fb`, with three distinct
windows, root consistency 1.0, six evidence sources and pairwise similarities
0.899025974026 / 0.894512195122 / 0.980681818182. N0/C1 are excluded.
The measured majority root is **checkout**, not the expected queue consumer;
this deviation was disclosed before both preauthorized checkpoints and retained
in the registration draft's unresolved gaps. No root or fingerprint was edited.

One ACCEPT_AS_NEW records the user's explicit prior authorization, not a fresh
manual inspection. Runtime independently selected
`core:RUNTIME_HEALTHY + ga:METRIC_QUEUE_LAG_OUTLIER` from the real three-positive,
three-negative matrix. Recall is 1.0; false positives, Core overlap and
No-Incident false positives are zero. Strict Shadow metrics all pass:
recall/ref-validity/reachability/counterfactual-consistency 1.0; all error/overlap
and authority-violation counts zero; source-failure safe. OTHER_EXTENSION is
unavailable for this first extension. One preauthorized Promotion created
registry version 1, ACTIVE, with action/remediation authority NONE and
remediation registration NOT_INCLUDED.

H1 then matched the Runtime-derived rule on fraud-detection, with no provisional
report and no new family. Its exact majority-root check failed because the
three Open-World reports chose checkout. The bridge currently takes the first
residual anomaly after anomaly-ID ordering, which puts checkout's error signal
before fraud's queue signal. The compiled TARGET predicate instead requires the
queue anomaly on its matching service. Shadow checks matching, not equality to
historical report roots. P1's Core Metrics window overlaps C1; this does not
prove that every observed error signal came from C1. This is a distinct root
semantics problem, not a failed control repair. H1 is preserved, not rerun or
relabeled, and ECOMSRE_PRODUCT_V030_H1_EXTENSION_KNOWN_PASS is **not** minted.

The Promotion summary initially read the runtime adapter as a registry entry;
the single already-persisted Promotion was recovered by read-only lookup, with
no duplicate POST/version. H1's first entry attempt stopped before its started
marker, traffic or mutation because that export was absent; exactly one real
H1 was subsequently executed.

Cleanup was CLEAN at 2026-09-03T10:28:53.533286Z: 28 owned containers, one network
and three temporary volumes removed, final owned resources zero, non-owned
resources unchanged. Seven live-004 Incidents and one each of family, review,
draft, Shadow, Promotion and ACTIVE registry entry remain in the private DB.
Cumulative Goal counts: four runtimes, three full control sets/nine controls,
one original queue preflight, three payment enables, three queue positives and
one H1. Provider/Agent/Runbook writes remain zero.

The repair's focused suite passed 212 tests; focused Ruff/mypy and independent
source review passed (Must Fix 0). No new full suite or CI cycle was run during
this repair. Historical green CI remains bound to its old head, not this tree.
The changes remain local in the same branch/worktree; push and merge are withheld
because a push would trigger another full CI before genuine merge readiness.
At that initial handoff, no further root change or live rerun had been made.
The later offline-only correction is described above; no live rerun followed it.

## Preserved live-003 control set after earlier repairs

The ordinary engineering recovery used a fresh runtime and one complete new
control set. It did not reuse the old passing N0-B or discard any prior formal
case. No queue positive or detector retuning occurred.

| Case | Healthy/expected-failure traffic | Actual Diagnosis | Full Goal gate |
| --- | --- | --- | --- |
| N0-A / seed 30001 | 30/30 HTTP 200 | OPEN_WORLD / RESOURCE, Kafka | Failed: healthy-control false positive |
| N0-B / seed 30002 | 30/30 HTTP 200 | OPEN_WORLD / RESOURCE, checkout | Failed: healthy-control false positive |
| C1 / seed 30003 | 10/10 expected HTTP 500 | CORE_KNOWN / CONFIGURATION_ERROR, payment | Failed: Logs/Traces coverage gaps |

All three bind environment `env-e080b07aad1ac9e4b3c88513`, Baseline
`base-26bcdd17ce69313c9a587efa`, SHA-256
`6910e0ad120237e9b56d356a5c61c4f03c7ce908f4420a6f2fe16555c447a172`.
The accepted DEMO_ONLY Baseline has 5/5 windows, all four resource-stat services,
and 30/30 healthy transactions; queue mean 0.65/stddev 0.1224744871391589 keep
the frozen threshold at 20. Every case's supporting refs resolve and no
forbidden control tokens were found.

Before these cases, a preparatory Baseline returned empty ResourceBaseline
statistics: millisecond Prometheus timestamps clipped to a microsecond window
made a nominal 30 seconds equal 29.999964 seconds and fail an integer check.
The repair permits at most 1ms representation error, keeps real gaps rejected,
and requires all four resource-stat services. The incomplete `base-da7bdc...`,
its 30/30 traffic and original result bytes remain preserved; it was never used
for a formal Incident. A new Baseline version was built in the same environment.
The initial broker-probe HTTP 504 and bounded successful resumed probe are
also retained; both preceded formal Incidents and fault injection.

The repaired error-ratio query and fixed five-minute C1 observation now produce
payment error metric 0.3473745490593028 (31 rolling points), against Baseline
0.006011654834047838. With its actual payment ChangeEvent, ordinary Core routing
correctly identifies CONFIGURATION_ERROR. This metric is still not the ten
transactions' failure fraction. Full C1 acceptance fails because fraud-detection
Logs are empty and fraud-detection/Kafka Traces are empty. Additional truncated
Kafka/payment Logs and checkout/payment Traces remain visible; they are not the
cause of the two named coverage-gap limitations. No source was declared complete
merely to obtain a negative control.

Independent CAS-to-Memory-to-Generic reconstruction matches both N0 diagnoses.
N0-A's Kafka endpoint slope is 8,855,552 B/s; N0-B's checkout slope is
364,134.4 B/s. Both exceed the unchanged effective 100,000 B/s guard and its
delta/sample-count conditions. The existing short-window rule does not require
persistent or monotonic growth. No sampling/unit defect explaining these false
positives was established. Original source timestamps are not retained, so
Collector staleness is neither proven nor ruled out. Changing the window,
adding persistence, or raising thresholds would be a policy change, not the
proven Baseline precision repair.

Live-003 created three Incidents and zero families, reviews, drafts, Shadow
evaluations, promotions or extensions. All flags were restored, lag returned
to zero, and cleanup was CLEAN at 2026-09-03T05:29:40.298631Z: 28 owned containers,
one network and three temporary volumes removed, final owned counts zero,
non-owned resources unchanged. Databases and failed evidence remain. Across the
Goal there are two formal control sets / six control executions, three runtime
starts, one queue preflight and two C1 payment injections; no P1/P2/P3 or H1.

Earlier repair validation: 193 tests passed in 2.09s; full-repository Ruff 0.16.1
and the CI-defined 667-source-file mypy 1.20.2 scope passed with locked dependencies.
An earlier noncanonical mypy 1.11.2 run failed on unchanged scripts; the pinned
checker and CI dependency setup passed without changing them. Independent control/precision audits passed for
the blocked-result claims, not Goal completion. CI run 33718317796 at code head
3639ff9 completed full pytest: 6,301 passed, 2 failed, 6 skipped in 692.16s.
Both failures were historical-test compatibility assumptions: which changed
source is rejected first, and using the expanded current enum to validate a
frozen 13-kind totality artifact. Test-only repairs preserve the exact rejection,
bind the old artifact bytes, retain all 48-arm/digest checks, and explicitly
require the current runtime validator to keep rejecting the old incomplete
surface. Production validators and frozen artifacts are unchanged. The two
files' 12 tests pass, and the affected-scope regression passed 152 tests.
Replacement [CI run 33720670897](https://github.com/Raidriar7170/EcomSRE-Agent/actions/runs/33720670897)
at code head `21ef82e19e5ee7479c47baa385d69109d475803d` completed successfully
at 2026-09-03T06:13:46Z: full pytest passed 6,303 tests with 6 skipped and one
warning in 701.22s; repository-wide Ruff and mypy's 667-source-file scope passed.
This validation is bound to that code head; the following result publication is
documentation-only. Green CI does not satisfy the failed live-control gates or
establish Goal completion or merge readiness.
Earlier CI failures and repairs are retained in the machine result; the pytest
collection collision was repaired only by renaming the new test file, with
test content unchanged.

## Preserved live-002 formal results

Everything below is the earlier measured set and its engineering history,
not the current cumulative counters or a replacement for live-003 above.

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
ingestion. At that snapshot no formal case had yet been reexecuted; the later
complete live-003 set is disclosed above.

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

## Historical live-002 safety and decision boundary

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

## Historical post-live-002 engineering repair (then offline only)

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

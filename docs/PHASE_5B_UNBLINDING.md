# Phase 5B Unblinding Protocol

The irreversible state sequence is:

```text
PROTOCOL_FROZEN
→ HIDDEN_PACK_SEALED
→ EXECUTION_STARTED
→ EXECUTION_COMPLETE
→ UNBLINDED
→ FINAL_REPORT_FROZEN
```

Before `EXECUTION_STARTED`, preflight must verify every flat SHA-256 binding in
the freeze manifest, the committed 180-run schedule, and the sealed hidden-pack
manifest. Execution must begin with zero completed runs. From the first scored
call onward, frozen files and schedule order cannot change; transport,
protocol, semantic, schema, evidence-reference, budget, and empty-answer
failures remain in the denominator and cannot be retried or deleted.

Workers receive only a derived opaque instance ID, one agent-visible replay
instance, and the architecture arm. They cannot read template/seed coverage,
expected outcomes, the external manifest, evaluator code, or ground truth.
Evaluator truth remains unread until all 180 raw records have been frozen and
an execution-complete report hash exists.

Unblinding creates exactly one canonical `phase5b.unblinding-record.v1` file.
It binds the protocol commit, freeze-manifest SHA-256, execution-schedule
SHA-256, hidden-pack-manifest SHA-256, agent-visible and ground-truth pack
SHA-256 values, execution-report SHA-256, and the count of 180 completed runs.
Only after that create-once record exists may the evaluator perform the first
truth read and run the preregistered analysis.

Unblinding ends prompt tuning for v1. An unfavorable result does not authorize
deletion, rerun, outlier removal, schedule rewrite, squash, or commit-history
rewrite. Any later prompt, runtime, suite, metric, or analysis change requires
`phase5b.v2`. A compact manifest, Git commit, and report hash are sufficient;
no durable hash ledger is required.

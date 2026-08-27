# EcomSRE Product MVP v0.1 Acceptance Limitations

These limitations bind the engineering acceptance result:

- the Product is a single-tenant, one-API, one-worker SQLite MVP, not a
  production or horizontally scalable service;
- only the pinned local OTel Demo profile received live connector acceptance;
  vendor schemas, field names, and Prometheus templates remain
  environment-specific;
- endpoint-wide Prometheus label discovery does not prove that every Metric or
  Resource template covers every discovered service; query-level coverage is
  preserved and target-complete evidence is not inferred from discovery;
- the live payment Runtime target was unavailable, so the no-fault observation
  truthfully terminated `INSUFFICIENT_EVIDENCE`; the result is not a
  No-Incident claim;
- the live baseline is explicitly `DEMO_ONLY`, consists of five short windows,
  and is not a production-quality historical baseline;
- the first three failed live attempts predate the runner's SHA-bound failure
  writer; their exact task-stream terminal and residual control-file hashes are
  retained only in a labelled retrospective ledger, not as authoritative
  runner-emitted terminal/cleanup artifacts;
- OpenSearch logs were available but a healthy no-fault window may contain no
  warning/error records; empty success is not negative evidence;
- the deterministic knowledge-loop data uses fixture observations and
  explicitly simulated human review; it establishes workflow behavior, not
  unknown-fault generalization or algorithmic effect;
- the rule miner and online clustering are bounded, deterministic MVP
  components that may conservatively return `NEEDS_MORE_DATA` or
  `NEEDS_MORE_NEGATIVES`;
- no remediation, Runbook, Agent write, Docker mutation, Kubernetes mutation,
  release, deployment, security certification, or autonomous operation is
  claimed;
- repository acceptance, GitHub CI, Ready transition, and squash merge remain
  separate gates until their current evidence is recorded; independent final
  review has passed.

The accepted live counters are: Agent writes `0`, Runbook executions `0`,
fault injections `0`, forward mutations `0`, and Provider calls on the promoted
extension recurrence `0`.

# Product MVP v0.1 Limitations

Product MVP v0.1 is deliberately narrow:

- single tenant, one API process, one worker, and SQLite WAL;
- no PostgreSQL, distributed queue, horizontal worker coordination, UI,
  dashboard, Kubernetes connector, or multi-environment tenancy;
- one tested local OTel Demo connector profile; vendor field mappings and
  Prometheus metric names can vary and require explicit configuration;
- Prometheus label discovery is not per-template coverage proof, so the MVP
  preserves query-level coverage and does not infer target-complete Metric or
  Resource evidence from discovery alone;
- baseline completeness depends on bounded, untruncated historical windows;
  short baselines are `DEMO_ONLY` and carry no production claim;
- the real HTTP-health connector proves runtime observation but is not yet a
  full Runtime-memory authority for the preserved diagnosis engine, so a live
  no-fault observation may truthfully end `INSUFFICIENT_EVIDENCE`;
- OpenSearch field paths and Jaeger process/tag conventions are deployment
  specific and fail closed on schema drift;
- simple deterministic online clustering may split one family or merge nearby
  families; human review is mandatory;
- the bounded rule miner often returns `NEEDS_MORE_DATA` or
  `NEEDS_MORE_NEGATIVES`; this is safer than promoting a weak clause;
- LLM assistance is limited to labels/explanations and has no authority over
  predicates, clauses, promotion, diagnosis root, actions, or runtime state;
- no production scale, reliability, security certification, unknown-fault
  generalization, causal algorithm effect, or autonomous remediation claim is
  established.

The Product API exposes no remediation, Runbook, shell, Agent write, Docker,
Kubernetes mutation, or repository write. All diagnosis and extension results
remain non-actionable with `action_authority = NONE`.

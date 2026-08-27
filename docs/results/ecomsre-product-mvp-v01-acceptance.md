# EcomSRE Product MVP v0.1 Engineering Acceptance

This is an engineering acceptance report. It is not a causal
algorithm-effect study and does not establish production readiness.

## Current truth

- Increment checkpoints pass with
  `ECOMSRE_PRODUCT_MVP_V01_API_PASS`,
  `ECOMSRE_PRODUCT_MVP_V01_CONNECTOR_PASS`,
  `ECOMSRE_PRODUCT_MVP_V01_DIAGNOSIS_PASS`, and
  `ECOMSRE_PRODUCT_MVP_V01_KNOWLEDGE_LOOP_PASS`.
- The fresh local OTel run terminates
  `ECOMSRE_PRODUCT_MVP_V01_LIVE_READONLY_PASS`.
- Clean committed-HEAD repository verification passed with 5,213 tests passed
  and 6 skipped. GitHub CI, Ready status, and squash merge are still pending;
  the final completion terminal is not minted in this report.

## Product behavior exercised

The checkpoints and deterministic demo exercised environment creation,
connector verification, job polling, immutable baseline construction,
incident ingestion, diagnosis and evidence retrieval, fault-family review,
registration drafting, seven-stratum shadow evaluation, promotion, and
post-promotion recurrence. SQLite, content-addressed objects, the promoted
family, and the active environment extension survived a Product restart.

The deterministic knowledge loop produced one review-ready family from three
same-environment Open-World positives. Two No-Incident controls and the Core
Known control remained distinct. `TEST_REVIEWER` performed explicitly labelled
`SIMULATED HUMAN REVIEW`; the mined draft was `DECLARATIVE_READY`, the shadow
gate passed with all seven strata represented, and the disjoint recurrence
terminated `EXTENSION_KNOWN` without calling the Open-World Provider. The
`OTHER_EXTENSION` control was explicitly `NOT_AVAILABLE` with
`NO_ACTIVE_OTHER_EXTENSION_CONTROL_AVAILABLE`; it was not misreported as a
passing observed control.

## Live read-only OTel acceptance

At `2026-08-27T12:44:52.134581Z`, Prometheus, OpenSearch, Jaeger, and configured
HTTP health were all `AVAILABLE`. The Product normalized 20 services and built
all five short `DEMO_ONLY` baseline windows successfully. The bounded no-fault
observation terminated `INSUFFICIENT_EVIDENCE`, not No-Incident, because the
payment Runtime target was unavailable. That missing source was preserved as
`RUNTIME:CONNECTOR_TARGET_UNAVAILABLE:a:runtime:payment` in the evidence bundle.

Six evidence objects were retrievable and all linked references resolved. The
one connector failure was explicitly represented. Agent writes, Runbook
executions, fault injections, and forward mutations were all zero. Product and
Demo cleanup both returned `CLEAN`; zero owned containers, networks, or volumes
remained, and non-owned Docker resources did not change.

Three earlier attempts truthfully ended
`BLOCKED_ECOMSRE_PRODUCT_LIVE_ACCEPTANCE` with
`BASELINE_INSUFFICIENT_WINDOWS`. The defect was an overstrong Prometheus
target-complete capability inferred from endpoint-wide label discovery. The
accepted implementation now preserves per-query `covered_services` and does
not convert missing per-template telemetry into complete evidence. The fourth
attempt passed 5/5 windows.

The first three runner versions printed their terminal to the task stream but
did not persist a per-attempt terminal or cleanup report. Their remaining
private control-artifact paths, timestamps, hashes, exact captured terminal,
and that evidence gap are retained in the explicitly retrospective
[`ecomsre-product-mvp-v01-live-attempt-ledger.json`](ecomsre-product-mvp-v01-live-attempt-ledger.json).
That ledger is not represented as an authoritative runner-emitted artifact.
The runner now writes a SHA-bound private failure report, including cleanup
state, on every future post-admission failure.

The SHA-bound live report terminal is
`ECOMSRE_PRODUCT_MVP_V01_LIVE_READONLY_PASS`; its `report_sha256` is
`31adec8252186ff5108b35ebf4037d3a851e8a14eafe1966592eb2dad596524e`.

## Protected boundaries

The Product exposes no remediation, Runbook, shell, Agent write, Docker, or
repository-write API. Diagnosis and environment extensions keep
`action_authority = NONE`. The live evaluator performed infrastructure setup
and owned cleanup only; it injected no fault and changed no running Demo
configuration.

## Remaining repository boundary

Independent review passed with `Must Fix 0 / Claim Accuracy PASS`. Focused
Product tests passed 97/97 after this review artifact was present. Full pytest
on a clean committed HEAD passed 5,213 tests with 6 environment-gated skips.
GitHub CI, Ready status, and squash merge remain required before
`ECOMSRE_PRODUCT_MVP_V01_COMPLETE` can be minted.

The machine-readable source of this report is
[`ecomsre-product-mvp-v01-acceptance.json`](ecomsre-product-mvp-v01-acceptance.json).
Known limitations are recorded in
[`ecomsre-product-mvp-v01-limitations.md`](ecomsre-product-mvp-v01-limitations.md).

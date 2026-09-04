# Product Operations

## Health and readiness

- `GET /healthz` proves the API process responds.
- `GET /readyz` proves the SQLite store can serve the Product.
- Docker Compose checks `/readyz` before declaring the API healthy.

The worker is intentionally one process. Jobs remain observable through
`GET /v1/jobs/{job_id}` with status, attempt count, owner, lease expiry, safe
error code, and result. An expired lease may be reclaimed; commit-time fencing
prevents the old attempt from publishing.

## Metrics

`GET /metrics` exports these process-independent counters from SQLite:

- `ecomsre_http_requests_total`;
- `ecomsre_jobs_total`;
- `ecomsre_job_duration_seconds`;
- `ecomsre_connector_requests_total`;
- `ecomsre_connector_failures_total`;
- `ecomsre_diagnosis_terminals_total`;
- `ecomsre_open_world_reports_total`;
- `ecomsre_fault_families_total`;
- `ecomsre_registration_promotions_total`.

Labels are bounded and exclude secrets, raw URLs, incident text, and evidence
identities. No custom dashboard is required for the current Product.

## Data and backup

SQLite and content-addressed objects share `/var/lib/ecomsre` in the Product
volume. Stop both processes before taking a filesystem-level backup. Preserve
the SQLite database and `objects/` together; a database-only copy can retain
links whose objects are absent.

`docker compose down` preserves data. `docker compose down --volumes` deletes
the Product's local MVP data and should be used only with explicit intent.

## Safe failure handling

Connector failures are stored as bounded status/error codes. A capability gap
may produce `INSUFFICIENT_EVIDENCE`; operators should correct the connector or
collect another read-only observation, not reinterpret it as No-Incident.
Never repair by deleting evidence or rebuilding a baseline from the active
incident window.

## Historical v0.1 manual live acceptance

This retained runner documents the earlier acceptance path, not v0.3 reproduction.
Use [Quickstart](QUICKSTART.md) for the verified Docker-free entry point and
[STATUS](STATUS.md) for the current merged result. It was not rerun for the
presentation closeout.

Run only on local Docker with the pinned OTel Demo images already authorized:

```bash
PYTHONPATH=src:. uv run --frozen --no-sync python -m \
  scripts.product.run_increment5_live_acceptance \
  --repository-root "$PWD" \
  --private-root /absolute/private/ecomsre-product-live-v1
```

The script proves both ownership domains before mutation, starts no fault,
queries through the Product API, and cleans Product resources before the Demo.
The pass terminal is `ECOMSRE_PRODUCT_MVP_V01_LIVE_READONLY_PASS`.

The fixed local image tag `ecomsre-product-mvp-v01:local` is a reserved Product
namespace and carries the image label
`io.ecomsre.product=ecomsre-product-mvp-v01`. The acceptance runner refuses to
overwrite an existing tag without that ownership label. It also rejects exact
container, network, or volume name collisions before Compose starts. Cleanup
freezes the IDs created by that run and refuses `docker compose down` unless
the project-label IDs, Product-label IDs, and frozen inventory remain exactly
equal; unknown same-project resources are never removed.

## Frozen v0.2 calibration

The sections below preserve historical results, not current Product status.
They do not supersede [the completed healthy and knowledge-loop results](STATUS.md).

The Product v0.2 live-pilot campaign is a separate, consumed one-shot study.
It stopped before the first fault attempt because baseline construction
returned `BASELINE_INSUFFICIENT_WINDOWS`. The runner restored the outer
baseline and closed owned Demo cleanup as `CLEAN`; it must not be rerun under
the same Goal or repaired by changing its recorded result. Any successor needs
a new execution contract and fresh roots.

## Frozen v0.2.3.1 Runtime-continuity session

Product v0.2.3.1 consumed one Runtime Authority Continuation Session from clean
execution HEAD `e2c2f640d34a9bd928e32d8394894fd54d93722a`. The same session proved
Runtime-authority continuity and Product restart before creating exactly one
No-Fault Incident and one Diagnosis. It reused the existing Baseline and did
not create a new Baseline or verify job.

The attempted healthy-profile episode did not pass: only `1 / 30` requests
completed and that request errored. The frozen measured terminal is
`ECOMSRE_PRODUCT_V0231_NOFAULT_NOT_SUPPORTED`. Do not rerun it, reinterpret it
as a healthy-system pass, or use Session 2; Incident creation made Session 2
illegal. The Knowledge-Loop handoff is not authorized. Fault injection,
calibration, promotion, remediation, Agent writes, and Runbooks remain outside
this result.

Offline verification of the tracked result is safe and does not call Docker or
live connectors:

```bash
PYTHONPATH=src:. uv run --frozen --no-sync python -m \
  scripts.ci.verify_product_v0231_result
```

## Frozen v0.2.3.2.1 formal blocker

Product v0.2.3.2.1 consumed exactly one formal execution at HEAD
`ca2860bd96405512839354a5b2be0453b43384b0`. The `30 / 30` healthy workload,
Runtime-authority continuity, Baseline restart, and fresh Runtime snapshot all
passed. One successor Incident was created, but its only Diagnosis job failed
with `INTERNAL_CONTRACT_FAILURE` before a Diagnosis or Evidence Bundle could be
published.

The frozen terminal is
`BLOCKED_ECOMSRE_PRODUCT_V02321_NOFAULT_INFRASTRUCTURE`. Product/Demo cleanup is
`CLEAN / CLEAN`; queue default, outer Baseline, and preserved source state are
unchanged. Do not rerun the formal command, resume the failed Diagnosis, or
reinterpret the traffic PASS as a measured No-Fault result. A continuation
requires a separately versioned successor. The public evidence boundary is the
[formal blocker](../results/product-v02321-formal-blocker.md) and its
[self-sealed evidence manifest](../analysis/product-v02321-formal-blocker-evidence-manifest.json).

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
identities. No custom dashboard is required for v0.1.

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

## Manual live acceptance

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

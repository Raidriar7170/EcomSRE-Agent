# Product MVP v0.1 Architecture

## Scope

Product MVP v0.1 is a single-tenant, self-hosted, read-only diagnosis and
environment-local knowledge-evolution service. It is an engineering surface
over preserved EcomSRE research contracts; it is not a production SRE control
plane and exposes no remediation, Runbook, shell, Kubernetes, Agent-write, or
repository-write API.

## Runtime topology

The deployment contains two Python processes and one durable volume:

- `api`: FastAPI/OpenAPI, authentication, validation, stable errors, health,
  readiness, and Prometheus-format operational metrics;
- `worker`: a single SQLite-leased background worker for connector verification,
  baseline construction, and diagnosis;
- `ecomsre-product-data`: SQLite WAL state plus content-addressed evidence
  objects.

The Product never receives a Docker socket. Real-source connectors issue only
bounded HTTP reads to Prometheus, OpenSearch, Jaeger, and configured health
URLs. Connector credentials remain indirect references in SQLite; resolved
values live only in the worker process.

## Data flow

1. An administrator registers one environment and its connector profile.
2. A verification job checks source health, normalizes service identities, and
   persists a SHA-bound capability matrix.
3. A baseline job reads immutable historical windows and explicitly activates
   one version.
4. Incident ingestion freezes the active baseline, service map, and capability
   SHA values.
5. The worker acquires bounded evidence and evaluates the fixed order:
   `Core Known -> Environment Extension -> No-Incident -> Open-World`.
6. Raw normalized observations are written to the content-addressed evidence
   store before the diagnosis result links them.
7. Open-World reports may accumulate into an environment-local fault family.
   Human review, deterministic rule mining, shadow evaluation, and an explicit
   promotion are separate gates.

Every diagnosis and promoted extension has `action_authority = NONE`. LLM use
is limited to non-authoritative naming/explanation in the registration-draft
path; promotion-critical predicates and clauses are Runtime-owned.

## Persistence and concurrency

SQLite runs in WAL mode. Jobs use leases, attempt counters, renewals, and a
commit-time fence so an expired worker cannot publish results. Environment
verification persists the service map and capability matrix in one
transaction. Evidence objects are create-once by digest, and API/worker restarts
reuse the same state volume.

## Deployment boundary

The final Compose file publishes only the API on loopback. Both processes run
as a non-root user with a read-only root filesystem, all capabilities dropped,
`no-new-privileges`, and a small private `/tmp` tmpfs. The data volume is the
only durable writable mount.

The manual OTel acceptance uses the existing dual-labelled local sandbox. That
lifecycle is evaluator-controlled infrastructure setup, not an Agent action;
the acceptance injects no fault and cleans only resources whose ownership is
proven.

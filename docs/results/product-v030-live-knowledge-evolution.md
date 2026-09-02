# Product v0.3 — partial offline preparation

Status: `RESOURCE_BLOCKED / OFFLINE_PREPARATION_PARTIAL`. No Goal success
terminal has been minted. Phase A, Baseline, N0/C1, P1/P2/P3 and H1 have **not run**.

The optional queue-lag template and Product-only action, generic COUNT metric,
baseline-only anomaly formula, and CONCURRENCY mappings are implemented. The
checkout three-kind bundle and Core mechanism ontology remain unchanged.
The full-mode Compose overlay resolves to 28 services, including checkout,
accounting, fraud-detection and kafka; this is configuration validation only.

Fresh focused validation: 836 Product/sandbox tests and 54 memory/domain tests
passed. Focused Ruff and mypy checks across 18 changed source files passed. Full repository
pytest, Ruff and mypy remain reserved for the pre-merge boundary.

Independent read-only review: `PASS` for this partial offline handoff, zero
Must Fix findings. Non-blocking test gaps remain for acquisition-level optional
action gating and adversarial full-mode resolved-payload validation. The
full-mode environment is a preparation skeleton, not an admitted live runtime.

## Resource blocker

Read-only `docker image inspect --platform linux/arm64` reports `No such image`
for exactly:

- `ghcr.io/open-telemetry/demo:3.0.0-accounting`
- `ghcr.io/open-telemetry/demo:3.0.0-fraud-detection`
- `ghcr.io/open-telemetry/demo:3.0.0-kafka`

The historical image lock does not contain these references. Existing sandbox
image admission correctly rejects the enlarged source set. The safety boundary
prohibits image pulls and historical-lock rewriting; no pull, build, container
start or fault injection was attempted. Continuing requires explicit authority
to acquire these three pinned ARM64 images and establish a separate private
full-mode lock, preserving all historical locks. No version fallback or emulation
is proposed.

## Resume boundary

The implementation remains partial. Live metric discovery, leakage projection,
incident-specific measured METRICS completeness, baseline/case harness, and
knowledge-loop acceptance still require implementation and validation. In
particular, endpoint-wide Prometheus capability discovery is not proof of exact
query coverage; do not mark missing or failed queue reads as conclusive negatives.

The requested human decisions remain unrecorded. Only the user may issue
`ACCEPT_AS_NEW KAFKA_QUEUE_BACKLOG` and, after passing Shadow Evaluation,
`PROMOTE KAFKA_QUEUE_BACKLOG`.

Product action/remediation authority remains `NONE`; Provider, Agent write,
Runbook, fault injection and Docker up counts are zero. No owned resource was
created, so cleanup is not required. No merge has been performed.

Machine state: [result](product-v030-live-knowledge-evolution.json).

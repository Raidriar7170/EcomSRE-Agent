# Live Telemetry Instrumentation v3 Specification

## Status and predecessor boundary

This successor starts from the exact v2 result head
`58a3d797a68ea90e56fefd5a3c31a14c82862981`. The v2 canonical runtime output,
public projections, and independent `REVIEW_REQUIRED` disposition remain
immutable predecessor evidence. v3 does not convert the v2 result into a pass.

The v3 lifecycle repairs exactly two review findings:

1. every Prometheus instant query is evaluated at the frozen capture
   `window_end`, including every readiness retry;
2. every directory beneath the v3 private evidence root has exact mode `0700`
   and every private file has exact mode `0600` before a success report can be
   constructed.

The only successful v3 terminal is:

```text
LIVE_TELEMETRY_INSTRUMENTATION_V3_READY_FOR_E2E
```

It remains limited to:

- `LIVE_LOCAL_SANDBOX_INSTRUMENTATION`
- `NO_FAULT_INJECTION`
- `NO_PROVIDER_CALL`
- `NO_MODEL_QUALITY_CLAIM`
- `NO_REMEDIATION`
- `NOT_PRODUCTION`
- `NOT_EXTERNAL_BENCHMARK`

## Frozen environment and source semantics

v3 reuses the v2 typed Prometheus, OpenSearch, Jaeger, Evidence resolver,
ownership, baseline, and cleanup contracts. It retains OpenTelemetry Demo
`3.0.0` at `1755859a9de82c2e5e225be68abc401a5ebf2b4f`, `linux/arm64`, local Unix
Docker, the frozen v1 Compose plan, target service `payment`, a 30-second
capture window, 15-second ingestion grace, and bounded source readiness.

Prometheus range queries retain the frozen start/end/step parameters. Total,
error, p95, and health instant queries additionally carry a `time` parameter
equal to the same `window_end` on every attempt. This prevents rolling `[30s]`
expressions from drifting with request time.

Private directories are created one component at a time with mode `0700`.
Before a report can be successful, the runtime recursively rejects symlinks,
non-regular entries, directories not exactly `0700`, and files not exactly
`0600` under the new v3 private root.

## Lifecycle and evidence

The v3 branch is `feature/live-telemetry-instrumentation-v3`; its default
private root is `~/.ecomsre/private/live-telemetry-instrumentation-v3`. The
development allowance is at most four sandbox startups. Canonical admission
requires a latest 3/3 AVAILABLE development terminal, valid refs, clean owned
cleanup, a clean exact pushed implementation head, and exact-head offline CI.
The canonical preflight is create-once.

No v3 public result exists until canonical succeeds. A failure seals private
evidence, keeps the PR Draft, and does not authorize a retry in the same
version.

# Product v0.2.1 predecessor baseline audit

## Frozen fact surface

PR #75 remains a valid blocked predecessor at
`a439f8882cd2fcdd3767f6bcfd5d955219fa1e15`.

Tracked machine-readable evidence proves:

- terminal `BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE`;
- blocker stage `PRODUCT_BASELINE` and code `BASELINE_INSUFFICIENT_WINDOWS`;
- zero accepted baseline windows under the predecessor's minimum-one-window
  policy, hence zero live calibration fault attempts;
- no selected profile or root, no incidents, no episodes, and no held-out run;
- outer baseline restoration `true`, owned cleanup `CLEAN`, Agent writes `0`,
  and Runbook executions `0`.

The successor manifest binds these bytes and the consumed marker. They are not
rewritten or rerun.

## What cannot be recovered

The tracked predecessor does not contain raw per-window results, and the
private `.local/product-v02` root is not present in this successor worktree.
Therefore this audit cannot recover per-source status, counts, truncation,
coverage, safe errors, or cold-start/query-timing attribution. It does not name
a measured connector-level cause.

## Code-path inference, not measurement

The frozen `PILOT_RUNTIME` connector advertises target-complete semantics but
is not eligible for historical baseline collection. If the unavailable
predecessor capability-matrix bytes marked Runtime available and
target-complete, the frozen builder would include it in the required set and
that conditional path would have the shape `REQUIRED_SOURCE_MISSING`.

This is recorded only as an unconfirmed `TRACKED_CODE_PATH_INFERENCE`. The
condition itself cannot be established from tracked artifacts. Without the raw
predecessor window and capability bytes it is not promoted to a measured cause,
and other simultaneous failures remain unknown.

## v0.1 comparison boundary

The accepted v0.1 reference produced 5/5 `DEMO_ONLY` windows for 20 normalized
services with Prometheus, OpenSearch, Jaeger, and HTTP health available. Its
earlier live attempts justify auditing per-query Prometheus coverage first.
They do not prove that PR #75 failed for the same reason; the predecessor
already contains the per-query coverage correction.

Exact structured facts and unavailable fields are recorded in
`product-v021-predecessor-baseline-audit.json`.

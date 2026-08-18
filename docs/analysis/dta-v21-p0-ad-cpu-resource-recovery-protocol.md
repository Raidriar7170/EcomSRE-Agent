# DTA v2.1 P0 PR-F Ad CPU Resource-Recovery Protocol

Status: **F0 protocol implementation**

This protocol implements DEC-044 and the exact
`dta-v21-p0-prf-ad-cpu-resource-recovery-v1` Goal amendment. It changes only
the PR-F Ad CPU business-impact and recovery oracle. PR-D and PR-E remain
immutable, and the held-out evaluation must not be rerun.

The earlier blocker was correct: accepted PR-D calibration established a safe,
measurable process-CPU fault but explicitly observed no business impact. The
parent Goal had required business-SLI recovery for every positive live slot,
which that evidence could not support. The amendment supersedes only that
parent requirement for Ad CPU, replacing it with resource-state recovery plus
business non-regression. It was accepted before the first PR-F live attempt,
so the oracle is frozen in advance rather than chosen after observing a run.

## Accepted calibration binding

The machine-readable protocol is
`config/dta-v21/live/ad-cpu-resource-recovery.v1.json`. It binds the accepted
PR-D capture closure by logical private URI, raw SHA-256, and semantic SHA-256;
the selected Ad baseline and fault observation hashes; the public calibration
limitations; and the exact accepted v2.1 measurement source. No absolute
private filesystem path appears in public evidence.

Accepted source bindings are:

```text
PR-D closure raw SHA-256:
264346ba1b49bd02743a2ef1b308992fcdfdf9a63085db20e1226466215cf0d2
PR-D closure semantic SHA-256:
ae2f447b468c8640d539f8193ff8c7b0800bf00edf8cee6b254c9df3c843d706
Ad baseline observation semantic SHA-256:
2ef6cab642f59edb1a232d8962d05884ded5cc107c8da5956bba33466f5fb799
Ad fault observation semantic SHA-256:
5df265c78152c76fd94bfeaa338aa154b721cfacc1e0e2a52ca485aa48c41299
calibration limitations raw SHA-256:
277caa488575bd55890ad9dc2575e6320e91ca124cadea78755875673e1ae31a
accepted measurement source raw SHA-256:
9901b6a120af0f74892118d3c4da6a9d93ab6e98c268943aa85c2b3889d988bc
```

The accepted rounded calibration is baseline CPU p95 `1.162%`, fault CPU p95
`406.326%`, capacity ratio `0.2709`, baseline latency p95 `3.386 ms`, and fault
latency p95 `3.296 ms`. Both accepted Ad observations have
`business_impact_observed=false`.

## Resource recovery oracle

Ad is `RESOURCE_ONLY`. The threshold is:

```text
min(1.162 + 10 percentage points, 406.326 * 0.10)
= min(11.162, 40.6326)
= 11.162 CPU percent
```

The only passing result requires exactly two consecutive fresh
post-mitigation windows from the same run and attempt. Each window is ten
seconds, contains five samples under the accepted query and CPU-percent unit,
uses the accepted maximum-of-five aggregation, has CPU p95 at or below
`11.162%`, and has capacity ratio at or below `0.5`.

## Business non-regression guardrail

Business latency p95 is a `NON_REGRESSION_GUARDRAIL`, not a recovery oracle.
The versioned wrapper retains the accepted substantive predicate: business
impact is observed only when latency p95 is at least baseline plus `5 ms` and
at least twice baseline. Each resource window must keep that predicate false,
service health must pass, and the endpoint must remain reachable.

The only positive Ad terminal is `AD_CPU_RESOURCE_RECOVERY_PASS`. Claim-safe
public output always states `business_impact_observed=false` and
`user_visible_recovery_claimed=false`; it makes no customer-impact recovery
claim.

The required recovery claim is limited to `RESOURCE_STATE_RECOVERED`.
`BUSINESS_SLI_RECOVERED`, `USER_IMPACT_RECOVERED`, and
`CUSTOMER_IMPACT_RECOVERED` are prohibited. This protocol has no held-out
effect, no PR-D effect, and no PR-E effect: the PR-D calibration remains
immutable, and the PR-E identities, seal, execution, score, outputs, and exact
negative claim remain immutable.

## Verification

CI verifies the public protocol, source bindings, progress state, Decision
Record, typed contracts, mutation cases, and deterministic historical
bindings. The local F0 gate additionally supplies the accepted private root to
verify the real PR-D closure bytes. Neither verification path executes
held-out evaluation, Docker, a Provider call, or a write action.

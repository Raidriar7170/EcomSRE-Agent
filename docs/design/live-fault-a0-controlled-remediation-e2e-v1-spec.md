# Live Fault → A0 → Controlled Remediation E2E v1

## Status and claim boundary

This successor begins at the frozen, Ready but unmerged PR #31 result head
`e28a1091acba7365d7f4deb2aa61fd39e90ae3ae`. It consumes the v3 semantic result
`ff299ed1ed0f7433702991fecfb1290e3439ed228b90796860c7dfd42cd4917c` and its
complete tracked-diff SHA-256
`772e4b74eba373a8af2d51ceb5c503ec8e692329eaa47d93c244392cff22cac5` without
rewriting either predecessor surface.

The sole success marker is
`LIVE_FAULT_A0_CONTROLLED_REMEDIATION_E2E_PASSED_READY_FOR_REVIEW`. Any result
remains a local, controlled demonstration only:

- `LIVE_LOCAL_SANDBOX_DEMO`
- `CONTROLLED_FAULT_INJECTION`
- `HUMAN_APPROVED_REMEDIATION`
- `NOT_PRODUCTION`
- `NOT_EXTERNAL_BENCHMARK`
- `NOT_SECURITY_VULNERABILITY_DETECTION`

## Frozen execution contract

The executor uses the existing local `linux/arm64` OpenTelemetry Demo 3.0.0
sandbox, one built-in `paymentFailure.defaultVariant` fault, 25 VUs, and its
two hash-bound whole flag documents. Only
`RESTORE_FROZEN_SERVICE_CONFIGURATION` is executable. Policy permits one
forward mutation and at most one compensating rollback; it exposes neither
shell nor arbitrary command arguments.

The original A0 Strong Single Prompt and strict output schema remain unchanged:
model `gpt-5.4-mini-2026-03-17`, one semantic live call, no specialists, no
fusion, no model fallback, and no schema or semantic retry. The synthetic
Provider preflight is the only additional Provider call and must have known
usage. Provider calls are serial, separated by at least five seconds, and use a
30-second timeout.

## Evidence and projection

All live raw observations, resolved Compose, image lock, flag documents,
Provider material, approval record, receipts, and journals are private `0700`
trees with `0600` files. The public result stores only aggregate counts and
claim boundaries.

The A0 Context is built solely from observed Metrics, Logs, and Traces. It
contains the observed checkout alert candidate when available, the top four
deterministically scored metric services, bounded anomalous logs, and traces
from at most three service queries (checkout plus the two leading metric
candidates). It never accepts scenario identifiers, flag names/values, action
names, approval text, source raw IDs, or user payload as model input. It exposes
three to eight canonical visible entities, at least two source types, resolvable
evidence references, and at most 98,304 tokens.

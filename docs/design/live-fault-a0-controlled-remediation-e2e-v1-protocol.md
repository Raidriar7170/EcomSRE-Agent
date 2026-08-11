# Live Fault → A0 → Controlled Remediation E2E v1 Protocol

## Invocation A — no fault, one stop condition

Invocation A verifies the exact successor branch, clean implementation head,
local Unix Docker boundary, pinned upstream, resolved Compose labels, cached
ARM64 images, baseline configuration, 90-second stabilization, two 30-second
preflight windows, and 3/3 typed source readiness. It may make at most two
no-fault probes and must stop after the first passing probe. It does not call a
Provider, inject a fault, create a remediation plan, or write a configuration.

After owned cleanup is `CLEAN`, Invocation A seals a private scenario lock,
plan template, and approval request, then returns exactly:

```text
LIVE_E2E_HUMAN_PREAUTHORIZATION_REQUIRED
```

## Human-only approval boundary

Codex must not execute the approval command. A human runs `approve` with a
nonempty approver name and the exact phrase
`APPROVE <scenario-id> <plan-template-sha256>`. The private approval record is
create-once and rejected when malformed, mismatched, or expired.

## Invocation B — one positive run

Invocation B first re-hashes every lock-bound successor source/config/test file,
checks the implementation commit is an ancestor of `HEAD`, validates the exact
approval binding, proves no owned resources exist, then performs one synthetic
typed Provider preflight. Only then may it start the sandbox and execute once:

```text
two baseline windows → frozen fault → two fault windows → A0 once
→ diagnosis gate → human policy gate → one baseline restore
→ two recovery windows → independent verification → owned cleanup
```

Two baseline/fault/recovery windows are each 30 seconds. Fault impact and
recovery use the frozen absolute-plus-multiplier thresholds. Any failed gate is
terminal for the create-once root: it does not permit a rerun, a second fault,
a second Provider call, another candidate, or another forward mutation.

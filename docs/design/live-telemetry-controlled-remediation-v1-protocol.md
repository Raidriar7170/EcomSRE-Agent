# Live Telemetry Controlled Remediation v1 Protocol

## Invocation A — no fault

Invocation A verifies the clean pinned submodule, local Unix-socket Docker on
`linux/arm64`, the resolved Compose contract, cached image identities, exact
loopback ports, dual resource labels, and private roots with directory mode
`0700` and file mode `0600`. It starts the sandbox once without a fault, waits
for health and at least 90 seconds of stabilization, verifies the baseline flag
through three independent surfaces, and captures nonempty target Metrics, Logs,
and Traces.

The sandbox is then restored and removed. Only after owned-resource cleanup is
clean does the CLI create a scenario lock, plan template, and approval request.
It stops at:

```text
SANDBOX_REMEDIATION_HUMAN_APPROVAL_REQUIRED
```

Codex must not run the `approve` command. The human supplies a nonempty name and
the exact phrase `APPROVE <scenario-id> <plan-template-sha256>`. The CLI writes
one create-once `HumanApprovalRecord`; it rejects an expired request, an
incorrect phrase, or an existing differing record.

## Invocation B — one positive run

Invocation B verifies the implementation commit is an ancestor of HEAD, every
tracked source/config hash in the scenario lock, the request hash, exact human
approval bindings and expiry, the local Docker boundary, and absence of owned
resources. Before Docker start or fault injection it performs one synthetic,
typed, non-scored Provider preflight with known usage and no retry or fallback.

The only positive run is:

```text
two baseline windows
-> one exact built-in fault mutation
-> two impact windows
-> one A0 semantic call
-> diagnosis gate
-> exact approval/policy gate
-> one baseline-restore mutation
-> two recovery windows
-> independent verification
-> optional one exact compensating rollback
-> baseline restoration and owned cleanup
```

No failed gate permits another fault, candidate, forward mutation, Provider
call, or rerun. The historical Phase 0 and Phase 3 control planes remain
unchanged.

## Private and public evidence

Raw resolved Compose, image lock, flag documents, telemetry, Provider response,
approval record, receipts, verification, rollback, and event journals remain
outside Git. Public results contain safe aggregates and the six evidence
boundary markers only. Failed results are retained; cleanup never deletes
evidence.

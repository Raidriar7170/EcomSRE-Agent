# Live Telemetry Controlled Remediation v1

## Status and boundary

This successor is one local Docker Compose demonstration over the repository's
read-only OpenTelemetry Demo 3.0.0 submodule at commit
`1755859a9de82c2e5e225be68abc401a5ebf2b4f`. It does not modify the Phase 0
acceptance path, the Phase 3 replay executor, the A0 Strong Single Prompt, or
any production environment.

The exact evidence classification is:

- `LIVE_LOCAL_SANDBOX_DEMO`
- `CONTROLLED_FAULT_INJECTION`
- `HUMAN_APPROVED_REMEDIATION`
- `NOT_PRODUCTION`
- `NOT_EXTERNAL_BENCHMARK`
- `NOT_SECURITY_VULNERABILITY_DETECTION`

## Frozen vertical slice

| Surface | Frozen value |
|---|---|
| Compose project | `ecomsre-live-sandbox-v1` |
| Platform | `linux/arm64` |
| Sandbox ownership | Compose project label plus `io.ecomsre.sandbox.id` |
| Fault | built-in `paymentFailure.defaultVariant`: `off` to `100%` |
| Target | `payment` |
| Impact SLI | request error rate |
| Diagnosis | existing A0 Strong Single, one semantic call |
| Action | `RESTORE_FROZEN_SERVICE_CONFIGURATION` |
| Approval | exact create-once human record |
| Forward mutations | one |
| Compensating rollback | at most one |

The baseline and fault are whole flag documents. Their canonical hashes are in
`scenario.json`, and they may differ only at the one registered field. The
controller accepts neither partial patches nor arbitrary values. A successful
write requires agreement between the private file, the flag UI read API, and
direct OFREP evaluation; HTTP success alone is not mutation proof.

## Real telemetry

The sandbox exposes only five fixed loopback ports. Prometheus provides span
request count, error count, latency, and runtime-target evidence. OpenSearch
provides OpenTelemetry logs. Jaeger provides spans. Each typed record retains
available service, instance, container, host, trace, and span identity fields.
Empty target Metrics, Logs, or Traces fail the preflight or positive run.

A0 receives only an observed alert, bounded live Metrics, bounded live Logs and
Traces, canonical visible entities, and source status. Scenario ID, sandbox ID,
expected root, flag name, and remediation action are excluded. Remediation is
admitted only when A0 returns `payment`, maps the failure to `APPLICATION`, and
cites valid Metrics plus at least one Logs or Traces reference.

## Independent recovery check

Two baseline, fault, and recovery windows are each 30 seconds. Fault impact
requires both fault windows to reach
`max(baseline + 0.05, baseline * 3)`. Recovery requires both recovery windows
to remain at or below `max(baseline + 0.02, baseline * 1.5)`, all services
healthy, exact ownership, and the exact baseline configuration hash.

The restricted executor has no argv or shell surface. A failed verification may
perform one exact rollback to the receipt's pre-action fault hash. Cleanup then
restores baseline and removes only resources proven by both ownership labels.

# Live Telemetry Instrumentation v2 Specification

## Status and claim boundary

This successor starts from PR #29 commit
`595001edac5b01561b9413f6811694536e6f4dbf` and preserves the v1 terminal
`BLOCKED_LIVE_TELEMETRY_SOURCE_UNAVAILABLE`. It repairs only local telemetry
instrumentation. It does not invoke a fault, Provider, model, approval, plan,
policy, executor, remediation, or rollback path.

The only successful terminal is:

```text
LIVE_TELEMETRY_INSTRUMENTATION_V2_READY_FOR_E2E
```

That terminal is limited to:

- `LIVE_LOCAL_SANDBOX_INSTRUMENTATION`
- `NO_FAULT_INJECTION`
- `NO_PROVIDER_CALL`
- `NO_MODEL_QUALITY_CLAIM`
- `NO_REMEDIATION`
- `NOT_PRODUCTION`
- `NOT_EXTERNAL_BENCHMARK`

## Frozen environment

The runtime reuses the v1 local Compose boundary, pinned OpenTelemetry Demo
`3.0.0` at commit `1755859a9de82c2e5e225be68abc401a5ebf2b4f`,
`linux/arm64`, the exact five loopback bindings, dual resource labels, cached
image verification, baseline configuration readback, and owned cleanup. The
four v2 configuration files add only target-service instrumentation, source
allowlists, bounded readiness, and safe reporting.

## Typed source contract

Metrics, Logs, and Traces each return one `SourceProbeResult` with a typed
terminal status, time window, attempt count, reachability, raw/parsed/target
counts, service-catalog facts, selected fields, identity-field presence,
private raw hashes, Evidence refs, invalid-ref count, and a safe reason code.

`AVAILABLE` requires a positive target-record count, at least one Evidence ref,
and zero invalid refs. A non-available result requires a safe reason. One source
failure does not erase either of the other two results; the aggregate gate is
computed only after all three terminalize.

### Prometheus

The adapter verifies the status/config, metric-name, instant-query, and
range-query APIs. Query templates preserve the `service_name` label and replace
one configuration-owned target placeholder. Required total, latency, health,
and cadence signals must match the target. An absent optional error series is a
valid zero only after the required total series is present. Canonical cadence
uses a five-second step and at least three finite target samples.

### OpenSearch

The adapter verifies `otel-logs-*`, reads `_field_caps`, and selects only
allowlisted compatible time, service, body, and severity fields. The target
query uses the selected time field for range and sort and the selected service
field for exact filtering. It does not assume `@timestamp`; the pinned Demo's
expected discovery is `observedTimestamp` when field caps proves it.

### Jaeger

The adapter queries `/jaeger/ui/api/services` before traces. Only a valid
catalog containing the configured target admits the fixed-window trace query,
whose limit is at most 100. Trace/span and runtime identity values remain
private.

## Readiness and evidence

After one 30-second capture window and 15-second ingestion grace, each source
repeats the same read-only semantics at five-second intervals, for at most
seven attempts and at most 45 seconds. The target and window do not change.

The runtime assigns deterministic `metric:`, `log:`, and `trace:` references.
Each reference resolves to source, private relative artifact key, raw SHA-256,
normalized-record SHA-256, window, and target service. The aggregate gate
reopens the resolver, rejects duplicate/invalid/cross-prefix refs, and freshly
checks the private raw bytes. Absolute private paths and raw bytes do not enter
public output.

## Public result

The public projection contains only safe source statuses and counts, selected
field names, readiness durations, cleanup truth, validation state, claim
boundaries, and a semantic SHA-256. A separate verifier recomputes the three
source gates, zero-mutation safety counters, canonical flag, clean cleanup, and
final verdict; it does not trust the verdict string alone.

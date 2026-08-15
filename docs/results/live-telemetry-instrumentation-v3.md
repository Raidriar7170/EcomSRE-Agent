# Live Telemetry Instrumentation V3 Result

**Verdict:** `LIVE_TELEMETRY_INSTRUMENTATION_V3_READY_FOR_E2E`

This result proves typed Metrics, Logs, and Traces instrumentation in the pinned local no-fault sandbox only.

| Source | Backend | Status | Target records | Attempts | Invalid refs |
|---|---|---:|---:|---:|---:|
| METRICS | PROMETHEUS_HTTP_API | AVAILABLE | 5 | 1 | 0 |
| LOGS | OPENSEARCH_HTTP_API | AVAILABLE | 28 | 1 | 0 |
| TRACES | JAEGER_QUERY_API | AVAILABLE | 14 | 1 | 0 |

## Safe source bindings

- OpenSearch time field: `observedTimestamp`
- OpenSearch service field: `resource.service.name.keyword`
- Capture window: `30s`
- Ingestion grace: `15s`
- Cleanup: `CLEAN` with owned containers/networks/volumes `0/0/0`.

## Claim boundary

- `LIVE_LOCAL_SANDBOX_INSTRUMENTATION`
- `NO_FAULT_INJECTION`
- `NO_PROVIDER_CALL`
- `NO_MODEL_QUALITY_CLAIM`
- `NO_REMEDIATION`
- `NOT_PRODUCTION`
- `NOT_EXTERNAL_BENCHMARK`

Semantic SHA-256: `ff299ed1ed0f7433702991fecfb1290e3439ed228b90796860c7dfd42cd4917c`

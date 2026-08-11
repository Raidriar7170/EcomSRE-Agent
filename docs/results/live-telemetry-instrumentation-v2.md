# Live Telemetry Instrumentation v2 Result

**Verdict:** `LIVE_TELEMETRY_INSTRUMENTATION_V2_READY_FOR_E2E`

This result proves typed Metrics, Logs, and Traces instrumentation in the pinned local no-fault sandbox only.

| Source | Backend | Status | Target records | Attempts | Invalid refs |
|---|---|---:|---:|---:|---:|
| METRICS | PROMETHEUS_HTTP_API | AVAILABLE | 5 | 1 | 0 |
| LOGS | OPENSEARCH_HTTP_API | AVAILABLE | 24 | 1 | 0 |
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

Semantic SHA-256: `fe8c79accb5deea044a5b75f486f8bf0d35de87891c95d7a3eb879d8b7a639d1`

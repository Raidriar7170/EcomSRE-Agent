# Selected Prometheus labels/templates

- Metrics: `service_name="{service}"` on span-metric series.
- Resources: `container_name="ecomsre-live-sandbox-v1-{service}"`.
- CPU: `container_cpu_usage_nanoseconds_total`, converted to percent.
- Memory: `container_memory_usage_total_bytes`.

# Sample count per requested Metric kind

- `ERROR_RATE`: 6
- `LATENCY_P95_MS`: 6
- `REQUEST_SUPPORT`: 6

# Resource record summary

- No Resource record was emitted during the initial focused smoke.

# Final source statuses

- Metrics: `SUCCESS_NONEMPTY`; checkout covered; 3 records; `safe_error_code=null`.
- Resources: `SUCCESS_EMPTY`; checkout not covered; 0 records; `safe_error_code=null`.

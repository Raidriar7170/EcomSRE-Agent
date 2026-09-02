# Selected Prometheus labels/templates

- Metrics: `service_name="{service}"` on span-metric series.
- Resources: `container_name="ecomsre-live-sandbox-v1-{service}"`.
- CPU: `container_cpu_usage_nanoseconds_total`, converted to percent.
- Memory: `container_memory_usage_total_bytes`.
- Jaeger: alias `checkout`; `minimum_duration_ms=0`.

# Sample count per requested Metric kind

- `ERROR_RATE`: 3
- `LATENCY_P95_MS`: 3
- `REQUEST_SUPPORT`: 3

# Resource record summary

- checkout: one 10-second record with 5 samples; CPU values are finite and
  memory values are non-negative integers.

# Final source statuses

- Metrics: `SUCCESS_NONEMPTY`; checkout covered; 3 records; `safe_error_code=null`.
- Resources: `SUCCESS_NONEMPTY`; checkout covered; 1 record; `safe_error_code=null`.
- Traces: `SUCCESS_NONEMPTY`; checkout covered; 12 valid records;
  `safe_error_code=null`; `truncated=true`.

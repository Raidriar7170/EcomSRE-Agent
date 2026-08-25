# DTA v2.3.2 `LOG_ERROR_CLUSTER` reproduction

This is a development-only reproduction. It does not continue either consumed
v2.3.1 study and is not eligible for final metrics.

The preserved `vx-113` input (`d11e4e86…`) and active ontology view
(`580c127e…`) reproduce the strict-arm failure as `KeyError:
LOG_ERROR_CLUSTER`. Immediately before the exception, memory SHA-256 was
`d006d496…` and the residual anomalies were exactly:

- `LOG_ERROR_CLUSTER` / `5874c82e…`
- `METRIC_ERROR_OUTLIER` / `953e20d0…`

The versioned `V23_STRICT_CONFLICT_GATE_TOTAL` path consumed the same case,
view, pre-error memory, anomaly IDs, and anomaly bytes. The exhaustive registry
resolved the cited log evidence through `LogCategoryV22.MEMORY_PRESSURE` to
`RESOURCE` (`6eed1f9b…`). The strict arm then reached the valid terminal
`CONFLICTING_EVIDENCE` with zero Provider calls and no `KeyError`.

The full machine-readable proof is in
`docs/analysis/dta-v232-keyerror-reproduction.json`.

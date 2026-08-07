# RCAEval RE2 v1 data card

## Authority

The data source is Zenodo record `10.5281/zenodo.14590730`. Adapter and scorer
semantics are locked to RCAEval publication branch `www25` at commit
`9d14687ce0644188f1f1a576fd3f57cd903af446`.

Archive SHA-256 values, the upstream MD5 checks, extracted-input manifests, case
counts, modality counts, timestamp bounds, and schema-manifest hashes are stored
in `config/rcaeval-re2-v1/dataset-lock.json`. The lock covers only RE2-OB and
RE2-SS during Work Package A.

## Data boundaries

- RE2-OB: development-visible, 90 cases, metrics/logs/traces expected.
- RE2-SS: development-visible, 90 cases, metrics/logs expected; traces are a
  typed unavailable source.
- RE2-TT: 90-case external holdout, not downloaded or inspected in Work Package
  A. Only the expected cardinality comes from the preregistered protocol.

All downloaded data, terminal journals, private mappings, and reports live
outside Git in an operator-selected private runtime root. The root path is not
tracked, and no archive or raw telemetry is a repository input.

## Known interpretation limits

The adapter uses the official service-folder labels only for development scoring
and evaluator-only post-lock scoring. Agent-visible incident and tool payloads do
not include those labels as metadata. Natural service names and error text that
already occur in telemetry remain observable, because suppressing them would
change the benchmark task.

Development pilot measurements are wiring and protocol evidence only. They are
not external holdout performance and cannot support public comparative claims.

## Work Package A audit snapshot

- RE2-OB: 90 cases, 5 services, 6 fault types, 90 trace-bearing cases,
  34 Metrics schema variants, 2 Logs schema variants, and 1 Traces schema
  variant.
- RE2-SS: 90 cases, 5 services, 6 fault types, no trace files, 35 Metrics schema
  variants, and 1 Logs schema variant.
- The upstream OB preprocessing includes two cases with rows lacking a Metrics
  timestamp; those rows are excluded from time-window queries rather than
  assigned a fabricated timestamp. Other missing metric values use the locked
  forward-fill then zero-fill policy.
- OB Logs and Traces can contain human-readable `time` columns alongside epoch
  columns. The adapter explicitly prefers `timestamp` for Logs and
  `startTimeMillis` for Traces.

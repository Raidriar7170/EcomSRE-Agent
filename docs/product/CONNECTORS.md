# Product Connector Configuration

Product MVP v0.1 accepts one connector of each kind: `PROMETHEUS`,
`OPENSEARCH`, `JAEGER`, `HTTP_HEALTH`, or one standalone `FIXTURE` connector.
Real and fixture connectors cannot be mixed. All network operations are bounded
HTTP reads; connector responses, records, fanout, windows, and timeouts have
closed limits.

## Local OTel profile

[`examples/product/environment.otel-demo.json`](../../examples/product/environment.otel-demo.json)
is the accepted local profile. From the Product containers it reaches the
owned sandbox through Docker Desktop's `host.docker.internal`:

- Prometheus: `http://host.docker.internal:19090`;
- OpenSearch: `http://host.docker.internal:19200`, index `otel-logs-*`;
- Jaeger: `http://host.docker.internal:11686/jaeger/ui`;
- frontend-proxy health: `http://host.docker.internal:18080/`.

These ports belong to the repository's dual-labelled local sandbox. Do not
reuse the profile for a remote or production environment.

## Prometheus

The connector discovers `service_name`, then executes the five required query
templates: request support, error rate, latency, CPU, and memory. Templates may
substitute only `{service}`, `{start}`, `{end}`, and `{step}`. Range results are
bounded by series, sample, response-byte, and Product evidence limits. Missing
series remain empty/unknown; they are never converted to normal evidence.
The label-values endpoint is endpoint-wide discovery, not proof that every
configured Metric or Resource template has samples for every discovered
service. Prometheus therefore preserves the exact per-query
`covered_services` and does not advertise target-complete coverage from label
discovery alone.

## OpenSearch

The connector performs one bounded aggregation for service discovery and
bounded `_search` calls. Configure an explicit index pattern plus timestamp,
service, optional service query/aggregation, severity, message, and optional
trace-ID fields. `*` and `_all` are rejected. The local profile reads
`resource.service.name` from `_source` while using its `.keyword` field for
terms/aggregation, and filters to warning/error severities so a healthy baseline
is not truncated by high-volume diagnostic logs.

## Jaeger

The connector discovers `/api/services` and queries `/api/traces` with service,
time, lookback, duration, tag, and limit bounds. It normalizes process identity,
causal parents, service paths, duration, and error tags. Cycles, missing parents,
oversized paths, or malformed spans become an explicit source failure.

## HTTP health

Each target binds a logical `service_id`, absolute HTTP(S) URL, accepted status
codes, optional timeout, and optional boolean JSON field. Partial target success
is represented as `PARTIAL`; timeouts and schema errors stay source failures.

## Identities and capabilities

Discovered aliases are normalized to lowercase logical service names or through
explicit source-specific rules. Alias collisions and unapproved many-to-one
mappings fail closed. Verification persists opaque service IDs, the source
alias map, coverage, target-completeness, observable predicates, mechanism
support, and `no_incident_eligible` in one SHA-bound capability matrix.

## Credentials

`credential_refs` accepts indirect `env:VARIABLE` references and bounded header
or bearer mappings. The database stores references only. Resolved secrets are
process-local, excluded from evidence and errors, and never enter Provider or
knowledge-evolution payloads.

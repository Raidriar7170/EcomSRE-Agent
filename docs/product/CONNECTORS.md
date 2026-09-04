# Product Connector Configuration

The current Product accepts at most one connector of each kind: `PROMETHEUS`,
`OPENSEARCH`, `JAEGER`, `HTTP_HEALTH`, `PILOT_RUNTIME`, and `FIXTURE`.
FIXTURE may be standalone or combined only with PILOT_RUNTIME, not other real
connectors. Network operations are bounded
HTTP reads; connector responses, records, fanout, windows, and timeouts have
closed limits.

## Local OTel profile

[`examples/product/environment.otel-demo.json`](../../examples/product/environment.otel-demo.json)
is the historical base example, not the complete measured v0.3 profile.
From the Product containers it reaches the
owned sandbox through Docker Desktop's `host.docker.internal`:

- Prometheus: `http://host.docker.internal:19090`;
- OpenSearch: `http://host.docker.internal:19200`, index `otel-logs-*`;
- Jaeger: `http://host.docker.internal:11686/jaeger/ui`;
- frontend-proxy health: `http://host.docker.internal:18080/`.

These ports belong to the repository's dual-labelled local sandbox. Do not
reuse the profile for a remote or production environment.

The [v0.3 profile builder](../../src/ecomsre/product/pilot/live_knowledge_evolution_v030.py)
adds queue lag and Kafka-native metrics, uses a ratio-based error-rate query,
changes log timestamp/projection to `@timestamp` / `OBSERVER_SYMPTOM_V1`,
and replaces frontend HTTP health with bound Runtime evidence. Its private
authority inputs are not a public one-command setup.

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

An optional `queue_lag` template supplies the current queue anomaly signal.

## OpenSearch

The connector performs one bounded aggregation for service discovery and
bounded `_search` calls. Configure an explicit index pattern plus timestamp,
service, optional service query/aggregation, severity, message, and optional
trace-ID fields. `*` and `_all` are rejected. The local profile reads
`resource.service.name` from `_source` while using its `.keyword` field for
terms/aggregation. The historical base example filters to warning/error severities so a healthy baseline
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

`PILOT_RUNTIME` reads a validated, authority-bound local Runtime snapshot;
it is not an HTTP probe or Docker-socket grant. See the
[adapter](../../src/ecomsre/product/connectors/pilot_runtime.py).
The frontend HTTP health example is not sufficient evidence for every service.

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

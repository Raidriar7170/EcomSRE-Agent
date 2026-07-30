# Phase 0 Deterministic Probe and Telemetry Design

## Scope

This design implements only the read-only Phase 0 observer surface required by
the frozen acceptance contract. It does not implement the acceptance runner,
change any upstream source, infer scenario truth, or introduce an external
service.

## Authority and discovery boundary

- OTel Demo `3.0.0` at commit
  `1755859a9de82c2e5e225be68abc401a5ebf2b4f` is the sole upstream authority.
- Static upstream references may create an `UNRESOLVED` candidate query
  fixture, but exact service, operation, metric, label, index, and timestamp
  fields become `FROZEN` only after current-run raw responses from the owned
  environment prove them.
- Every backend fixture declares its target, exact query/request template,
  expected response schema, upstream version and commit, applicable service,
  failure semantics, freshness semantics, and source facts. Prometheus also
  declares counter identity labels, error classification, scrape interval,
  boundary rule, cardinality rule, and reset/staleness policy. The probe
  declares method, path, input, response contract, exit semantics, attribution
  mechanism, and proof that the path produces or observes `GetAds`.
- `UNRESOLVED` and `CANDIDATE` fixtures are never valid inputs to readiness,
  measurement, acceptance, or an open-question closure.
- Promotion to `FROZEN` requires immutable current-run raw artifacts, exact
  emitted-identity samples, the counter-to-attempt/error mapping, probe-to-
  `GetAds` attribution, a fixture content hash, upstream and Compose hashes,
  and an explicit review decision. A frozen fixture is immutable; any field
  change creates a new version and requires the promotion proof again.
- No `app.*` compatibility fallback is allowed.
- A fixture hash and the frozen upstream/Compose hashes bind every query
  result.

## Observer boundary

- Observer modules receive only an authenticated ownership context, run and
  phase time window, versioned query fixture, loopback endpoint, and their own
  raw responses.
- They cannot import scenario-controller modules, read evaluator-only paths,
  inspect flagd state, or accept a feature-flag key/value.
- The deterministic probe starts with an `UNRESOLVED` local storefront request
  candidate. It may become `FROZEN` only when current owned-run telemetry
  proves that the exact request causes or observes a real Ad-service `GetAds`
  call. A successful storefront HTTP response alone is insufficient.
- Each probe attempt preserves the sanitized command/input, exact local
  request, bounded raw response, request and response timestamps, status,
  typed exit semantics, attributable correlation fields when present, and a
  machine-checkable hidden-input denial result. The same frozen contract must
  produce attributable observations in baseline, fault, and recovery.
- The probe is an independent business observation, never the statistical
  denominator or the scenario-state oracle.

## HTTP transport

- Only `http://127.0.0.1:<owned dynamic port>` or
  `http://[::1]:<owned dynamic port>` is accepted.
- Endpoint use requires an authenticated ownership manifest proving the exact
  service, target port, host port, protocol, and current run.
- Requests bypass proxies, reject redirects, use an absolute monotonic
  deadline, bound headers and body, preserve partial raw bytes and hashes, and
  close the connection on every path.
- Timeout, transport, HTTP, schema, freshness, ownership, and evidence
  persistence failures remain distinct fail-closed results.

## Prometheus semantics

- Prometheus is the sole source for `GetAds` attempts and errors.
- A measurement uses raw counter deltas between two actual Prometheus scrape
  samples for the same frozen series set. After stabilization, the newest
  complete scrape becomes the start anchor. The first later complete scrape
  whose delta reaches at least 200 attempts becomes the end anchor. The
  measured window is `(start_sample_timestamp, end_sample_timestamp]`; no
  `rate()`/`increase()` extrapolation is used for the acceptance count.
- The 180-second deadline is wall-clock time from acquisition of the start
  anchor. A sample after that deadline cannot complete the window.
- Total and error samples must share the same scrape timestamps, exact
  Ad-service/`GetAds` identity, and frozen label/cardinality policy. The total
  covers all frozen status series; errors cover the exact frozen error subset.
  Aggregation occurs only after validating every raw series. The selected
  series set must be present at both anchors unless the frozen fixture defines
  and proves an explicit zero-valued series rule.
- Every intermediate sample is inspected. Counter decrease/reset, duplicate or
  reordered timestamps, target restart, stale marker, series appearance or
  disappearance outside an approved zero rule, cardinality drift, scrape lag
  beyond the frozen tolerance, non-integral delta, or errors greater than total
  fails closed. Scrape interval and maximum lag are discovered and frozen from
  the owned environment rather than guessed.
- Every selected sample must be fresh for the current run and scenario phase;
  mere overlap is insufficient.
- The raw query and raw response are persisted before parsed counts are
  returned.

## Jaeger and OpenSearch gates

- Jaeger must return at least one newly generated Ad-service trace containing
  the frozen Ad-related operation/span inside the current run/phase window.
- OpenSearch must return at least one newly generated Ad-service log with the
  frozen service identity and timestamp field inside that window.
- Service identity plus run/phase time range is mandatory; trace/span/request
  correlation is recorded when upstream exposes it but is not required across
  all three backends.
- A record before the window, after the window, without exact identity, or from
  a previous run is rejected.

## Complete readiness handoff

- The Task 7 output is the existing `ReadinessEvidence` object, bound to the
  same authenticated `run_id` as the ownership manifest and every raw
  readiness artifact.
- `ownership_resources_complete` comes from exact equality between the
  authenticated post-up `AuthenticatedOwnershipContext.manifest` resource set
  and current no-trunc, label-bound Docker resource discovery for the same
  `run_id`. The pre-up ownership intent is not a resource inventory.
- `load_generator_ready` requires the owned load-generator service to be
  running/healthy and current-run evidence explicitly attributable to that
  load-generator's own readiness contract or emitted traffic. The independent
  storefront probe cannot substitute for load-generator readiness, and
  container state alone is insufficient.
- `collector_ready` requires the owned Collector service to be running and a
  current-run pipeline/ingestion proof from the frozen local interfaces;
  container state alone is insufficient.
- `prometheus_fresh`, `jaeger_fresh`, and `opensearch_fresh` come from the
  frozen backend gates above.
- The handoff passes only when all six fields are true for one run. Any missing,
  unresolved, stale, mismatched-run, or evidence-persistence result is false
  and the combined readiness fails closed; three backend successes alone are
  never overall readiness.

## Evidence and lifecycle

- Each attempt records `run_id`, cycle, scenario phase, fixture version/hash,
  backend, exact request/query, UTC and monotonic timing, HTTP status, bounded
  raw response artifact/hash, parsed identity/timestamps, decision, and reason.
- Probe and telemetry raw artifacts are observer-visible; no evaluator-only
  value is copied into them.
- Artifacts use the existing authenticated, append-only evidence store and
  project-local run directory.
- Offline tests use fixed response fixtures only. Real integration and
  `FROZEN` query state occur later against the safely owned environment.

## Rejected alternatives

- Browser/UI clicks, flagd reads, Docker log scraping as the business probe,
  historical data fallback, cross-window cumulative counters, and guessed
  telemetry names are rejected.
- Requiring the same request to appear in Prometheus, Jaeger, and OpenSearch is
  rejected because upstream sampling may differ.

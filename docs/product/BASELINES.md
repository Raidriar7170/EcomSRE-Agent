# Environment Baselines

Baselines are immutable, SHA-bound environment versions. Incident ingestion
requires one explicitly active baseline and freezes its ID/SHA together with
the current service-identity and capability SHAs.

## HISTORICAL

The default production-shaped policy is fixed at a 3,600-second lookback, six
windows, four required successful windows, and no warmup. It is still an MVP
engineering facility, not production validation. A build succeeds only when
each counted window has a valid, untruncated connector result and at least one
observation; target-complete sources must cover the requested catalog.

## DEMO_ONLY

The bounded live-demo policy has five windows, at most 180 seconds of lookback,
and exactly 180 seconds of warmup. It must be explicitly labelled
`DEMO_ONLY`. The manual acceptance retains all five window outcomes and requires
at least one complete successful window after the local Demo accumulates enough
history; the exact successful-window count remains visible. DEMO_ONLY evidence
must not be presented as a production baseline.

## Contents

A baseline stores:

- stable environment and service IDs;
- source-capability SHA;
- metric mean/deviation facts;
- trace-operation duration facts and observed topology;
- resource CPU/memory-slope facts when available;
- normalized healthy-log templates;
- schedule, successful-window count, timestamp, activation state, and content
  SHA.

Empty, unavailable, partial, schema-failed, or truncated data remains explicit.
`UNKNOWN` is not converted to negative evidence. Activating a new version
deactivates the previous one without rewriting it.

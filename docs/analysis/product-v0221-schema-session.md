# Product v0.2.2.1 OpenSearch Schema Session

Terminal: `BLOCKED_ECOMSRE_PRODUCT_V0221_SCHEMA_AMBIGUOUS`

- session: `product-v0221-schema-discovery-1`
- live schema sessions: `1 / 1` (consumed)
- request plans: `1 / 3` (`Plan A` only; no changed plan)
- read-only OpenSearch requests: `6 / 16`
- transport retries: `0 / 2`
- failure stage: `PROFILE_RESOLUTION`
- safe message: `OpenSearch Mapping and samples do not prove a unique profile`
- baseline unchanged: `true`
- owned cleanup: `CLEAN`
- fault / Baseline Readiness / knowledge-loop attempts: `0 / 0 / 0`
- Agent writes / Runbooks: `0 / 0`
- action authority: `NONE`
- rerun authority: `NONE`

Mapping, Field Caps, bounded sample, aggregation, timestamp-range, and profile
verification requests completed in memory. The required field candidates did
not establish a unique normalization profile. Because the exception occurred
before the raw capture phase, those response bytes were not retained; no
normalization profile, sanitized fixture, offline parser acceptance, or
connector smoke was produced.

The private start and completion sentinels remain frozen under
`.local/product-v0221/`. This result does not authorize a retry, Baseline
Readiness, fault work, Agent action, Runbook, Ready transition, or merge.

Report SHA-256: `b0233a695448fc88fdd3a3a7d0207cba5da3f8e7cde14bef90752bf3f907d2e5`

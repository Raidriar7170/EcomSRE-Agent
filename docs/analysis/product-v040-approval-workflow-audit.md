# Product v0.4 PR-B approval workflow audit

PR-B adds persisted candidates and operator approvals. It grants no attempt authorization and contains no executor, environment adapter or live mutation. Diagnosis objects remain unchanged and read-only. PR-A was squash merged as PR #91 at `a823456185aa88809a73eb193b63aefcc3afa745`; its exact reviewed tree passed GitHub CI with 6,388 tests passed and 21 skipped.

## API and storage contract

Candidate GET derives a safe projection without persisting remediation records. Candidate POST persists that projection and any eligible candidate. Both return only the closed, non-executable PR-A model. All three mutating routes (candidate creation, approval, revocation) require a configured admin token and a matching bearer token, including on loopback. Existing Product authentication behavior is unchanged. Invalid input and internal failures use the existing sanitized Product error envelope.

Approvals bind the exact candidate digest, LOW Payment rollback scope, empty parameters, one forward step, a UTC issue/creation anchor and an expiry of at most ten minutes. The API supports explicit local operator authorization and the activated Goal's prior user authorization. The latter is an attribution to prior activation, not a claim of contemporaneous live inspection. Approver labels are closed to `LOCAL_OPERATOR` and `Minghong Sun`; neither raw credentials nor free-form operator text enters public projections. Possession of the configured admin token is the API trust boundary. It is not an LLM decision interface.

Revocation appends an immutable object; the original approval's `revoked_at` stays null and its digest never changes. The status projection resolves the revocation and reports ACTIVE, EXPIRED, REVOKED or NOT_YET_VALID. `require_active_approval` rejects non-active and mismatched approvals inside the caller's transaction; it grants no authority. PR-C must combine this prerequisite with single-use consumption, state binding and authorization in its attempt transaction. A `single_use: true` declaration alone is not a consumption mechanism.

Each mutation requires an Idempotency-Key, stores its SHA-256 rather than raw value, and serializes lookup/create/validation/commit under BEGIN IMMEDIATE. Keys are scoped by operation across candidate IDs. Same key and same semantic request returns the original sealed object; a changed request returns IDEMPOTENCY_CONFLICT. Expired or revoked idempotent approval replay retains the original expiry and does not reactivate it. Cached responses must bind to the requested parents and the canonical persisted records. Candidate and approval reads also compare indexed digests and parent IDs. Revocation reads validate their approval parent. No request contains an execution command, target URL or extra Runbook parameter.

## Compatibility and allowed broad paths

`src/ecomsre/product/app.py` mounts the separate remediation router and constructs its repository; this is the necessary API composition point. `Dockerfile.product` copies the exact pinned registry artifact to the same application-relative location used locally. No image was built or started in this phase, and this packaging change is not runtime evidence.

The existing SQLite schema remains version 9 because accepted historical pilot contracts explicitly bind that version. Remediation adds independently versioned tables in the same database, preserving WAL, foreign keys and busy-timeout behavior. Its migration is transactional, checks the recorded migration SHA and exact table definitions on restart, and fails on unknown or altered schema. There is no change to historical migration files or frozen pilot contracts. Repository APIs expose no update/delete operation for authority records; this is application-level immutability, not a security claim against a database administrator rewriting the file.

## Verification and limits

Offline tests use actual Product SQLite parents, CAS evidence, API requests, restart and concurrent requests. They cover unauthenticated and no-token denial, candidate persistence, same-key reuse, conflicting payloads, bounded TTL, expiry/revocation rejection, parent mismatches, cached valid-object swaps, indexed digest tampering, transaction rollback, schema tampering and public projection safety. No Provider, Docker, fault injection or execution call is part of these tests.

PR-B reached its offline terminal after 190 focused tests, Ruff, scoped mypy and independent PASS / Must Fix 0 / Claim Accuracy PASS. Committed-content verification, exact-head GitHub CI and merge remain pending. No bounded-remediation recovery or overall v0.4 completion is claimed.

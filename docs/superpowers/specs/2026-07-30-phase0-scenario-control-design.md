# Phase 0 Scenario Control Design

## Status

Accepted for Phase 0 implementation by the frozen planning and Goal Mode
contracts. This document narrows implementation details; it does not change a
Decision or authorize Phase 1 work. Fixture implementation remains
`REVIEW_REQUIRED` until the current quality review accepts its evidence.

## Upstream facts

- Source: `third_party/opentelemetry-demo/src/flagd/demo.flagd.json`
- Source SHA-256:
  `bef4fa5da0ad8b1f64cc0d66fc66afaf7b9877c85895b78bf47d9a97577f9983`
- Schema: `https://flagd.dev/schema/v0/flags.json`
- Logical scenario: `adServiceFailure`
- Physical flag key: evaluator-only
- Baseline variant/value: `off` / `false`
- Fault variant/value: `on` / `true`
- flagd gRPC port: `8013`
- flagd OFREP port: `8016`
- Evaluation endpoint shape:
  `POST /ofrep/v1/evaluate/flags/<evaluator-only-key>` with `{}`.

The upstream UI write API rewrites the full file asynchronously and does not
provide the durability or acknowledgement required by Phase 0.

## Chosen approach

The host-side evaluator controller derives a project-owned, run-scoped flagd
configuration from the verified upstream source. It writes only under
`artifacts/phase0/evaluator-only/<run_id>/control/` using atomic, inode-bound
file operations. The control directory contains only `demo.flagd.json` in
steady state. The project Compose override mounts that directory read-only at
`/etc/flagd` and `/app/data`; flagd is explicitly started with
`file:./etc/flagd/demo.flagd.json`. Mounting the directory is required so a
reader that reopens the path observes the new inode after an atomic replacement.

An inject or reset transition succeeds only when both conditions hold:

1. the durable project-owned configuration has the intended state; and
2. a bounded direct OFREP evaluation observes the intended value from flagd.

The flagd UI API is not part of the control path. `docker exec`, shell-generated
commands, and upstream file mutation are prohibited.

The full resolved Compose stdout may contain the evaluator mount source,
physical key, or scenario identity. It is therefore stored only under the
evaluator capability. Observer evidence contains a structurally sanitized
configuration in which mount values and control semantics are replaced by an
opaque token, plus the resolved Compose hash, image source set, and Compose
source-file set. All observer lifecycle writes use `ObserverEvidenceStore`;
internal byte writers cannot bypass its recursive semantic guard.

## Component boundaries

- `ground_truth.py` owns the physical key/value, verified source schema/hash,
  run-scoped control file, atomic mutation, and OFREP read-back adapter.
- `ad_service_failure.py` owns only the logical transition state machine and
  sanitized result types. It does not import or expose physical ground truth.
- The CLI orchestration layer composes the two behind authenticated ownership,
  current preflight, and readiness gates.
- The OFREP adapter opens a direct socket only to a literal `127.0.0.1` or
  `::1` endpoint whose port came from authenticated ownership evidence. It does
  not use ambient proxy configuration, DNS names, or redirects. Connect, send,
  header, and every body read share one monotonic total deadline; the remaining
  timeout is recomputed before each blocking operation and the socket is always
  closed. Chunk size lines, framing bytes, trailer lines, trailer bytes, and
  counts have independent small bounds. A declared chunk larger than the
  remaining 64 KiB evidence budget is read only through byte 65,537 and is not
  drained. Timeout, framing, and parse failures retain every already received
  body byte up to the same bound, any already validated HTTP status, the
  partial-body hash, and truncation marker; they never replace partial raw
  evidence with an empty body or discard a known status.
- Observer-visible evidence contains only an opaque control event ID, UTC
  transition window, correlation metadata, success/failure, and a sanitized
  error category.
- Evaluator-only evidence contains the physical/logical mapping, expected
  transition, file hash, and raw read-back. Hidden transition records remain at
  `control-events.jsonl`; bounded raw OFREP attempts are append-only under
  `readbacks/`; best-effort partial diagnostics are separate under `emergency/`.
  Neither those paths nor their contents may appear in observer artifacts.
- The whole read-check-write-confirm transition is serialized by a run-scoped
  same-process thread lock and cross-process `flock`. The lock file is opened
  relative to the evaluator capability and must be a same-owner, regular,
  single-link `0600` file with no symlink traversal. Waiting for either lock is
  part of the action deadline. A waiter rereads state after acquisition. The
  guard remains held through mutation, raw read-back, and an evaluator-only
  `PREPARED` record containing an opaque preparation ID, monotonic transition
  sequence, and immutable observation/configuration hash. Unlock, descriptor
  close, same-process release, and registry release are independently attempted.
  Only confirmed cleanup permits linked terminal `FINALIZED` evidence and an
  observer success acknowledgement. Cleanup failure instead appends linked
  `CLEANUP_FAILED` or best-effort `INTERRUPTED` evidence; it can never persist a
  terminal or observer success. `KeyboardInterrupt` and `SystemExit` from
  cleanup are re-raised unchanged after all cleanup attempts. Concurrent
  terminal append order is interpreted through the preparation sequence/link,
  not physical JSONL order.
- Before mutation, the controller fsyncs an append-only `PENDING` intent with
  opaque event ID, explicit `UNKNOWN` mutation state, action, pre-state, target,
  UTC/monotonic start, monotonic deadline, and pre-mutation configuration hash.
  Normal confirmation appends a linked completion. A retry must reconcile any
  unresolved intent with a fresh read-back and append a linked recovery record
  before deciding whether the requested action is idempotently complete or may
  mutate.

## Failure behavior

- Missing ownership, preflight, readiness, or safe file capability: zero-write
  fail closed.
- Any adapter `UNKNOWN`, before or after mutation, immediately returns
  `MANUAL_INTERVENTION_REQUIRED / MUTATION_STATE_UNKNOWN` with exit 41. It is
  never polled into a later success, and a successful transition model cannot
  contain an unknown pre-state or mutation state. In particular, an
  `apply_state` result of `UNKNOWN` leaves the pending intent unresolved and
  performs no read-back or completion append.
- Inject deadline: `FAILED_ACCEPTANCE / INJECT_TIMEOUT`.
- Reset deadline: `FAILED_ACCEPTANCE / RESET_TIMEOUT`.
- Failure to acquire the run lock records a terminal pre-lock timeout with
  `PRELOCK_UNAVAILABLE`, `NOT_APPLIED`, and a null runtime hash. It does not
  invent a configuration hash or become an evidence-persistence failure.
- A read-back counts only if OFREP returns and the intended state is checked at
  or before the monotonic deadline. A target response that returns after the
  deadline is a timeout, never success.
- A durable write without matching OFREP read-back is failure evidence, never a
  successful transition.
- Mutation state is evaluator-only tri-state evidence: `NOT_APPLIED`, `APPLIED`,
  or `UNKNOWN`. An uncertain write is never represented as safely absent.
- Every OFREP attempt persists a response bounded to 64 KiB, HTTP status,
  base64 raw body, truncation marker, strict parsed fields, UTC and monotonic
  receipt time, raw-content SHA-256, typed error, and sanitized request
  metadata. Non-2xx, invalid, oversized, and transport responses remain raw
  evaluator evidence and cannot confirm a transition.
- Required evidence persistence failure before mutation is
  `FAILED_ACCEPTANCE / EVIDENCE_PERSISTENCE_FAILED` for an action. A read-only
  status evidence failure is
  `BLOCKED_ENVIRONMENT / EVIDENCE_PERSISTENCE_FAILED` with exit 20. Observer or
  evaluator capability initialization I/O failure is
  `BLOCKED_ENVIRONMENT / EVIDENCE_CAPABILITY_UNAVAILABLE` with exit 20. After
  an applied or uncertain mutation, required evidence failure is
  `MANUAL_INTERVENTION_REQUIRED / EVIDENCE_PERSISTENCE_FAILED` with exit 41 and
  a best-effort emergency diagnostic. No evidence exception may escape as an
  untyped CLI failure. An existing pending intent is prior mutation
  uncertainty: if target-state recovery is physically confirmed but its raw
  read-back or linked recovery record cannot be persisted, the result remains
  manual exit 41 with mutation state `UNKNOWN`, and the unresolved intent is
  retried on the next invocation. If recovery observation is itself `UNKNOWN`,
  the otherwise prohibited `before=UNKNOWN` plus `mutation=UNKNOWN` combination
  is valid only with the explicit unresolved-pending marker and this exact
  non-success manual-41 evidence-failure result.
- `KeyboardInterrupt` and `SystemExit` are never swallowed. If either occurs
  after mutation starts, the already-fsynced pending intent remains unresolved
  so the next invocation performs recovery-linked reconciliation instead of
  reporting a provenance-free `NOT_APPLIED`.
- Failed and partial transition artifacts are append-only and retained.

## Verification

Tests must cover upstream hash/schema, derived baseline, mount isolation,
the stale single-file inode reproducer and directory-reopen behavior,
idempotency, pre-state checks, atomic write safety, tri-state mutation evidence,
bounded raw read-back, non-2xx/invalid/oversized responses, post-call deadline
enforcement, proxy/redirect rejection, slow-drip total deadlines and closure,
chunk wire-budget limits, same-process concurrent inject/reset evidence order,
cross-process lock exclusion, cleanup fault isolation and lock-registry
reclamation, cleanup interruption propagation, pre-lock timeout evidence,
pending intent crash survival, recovery-linked retry and recovery evidence
failure including unknown observation, partial raw-body retention,
evidence-write failure mapping, inject/reset timeouts,
immediate unknown-state termination, hidden-truth import/path/data leakage,
resolved Compose capability separation, CLI zero-write gates, and CLI exit 41
after a mutated-state evidence failure. Real flagd integration remains a later
live gate and cannot be replaced by fixtures.

# EcomSRE-Agent Repository Instructions

## Authority

Use this order of authority:

1. The user's explicit, scoped request.
2. The accepted decisions in `docs/DECISIONS.md`.
3. Safety boundaries in `docs/SAFETY_BOUNDARIES.md`.
4. Phase acceptance contracts, beginning with `docs/PHASE_0_ACCEPTANCE.md`.
5. Architecture, charter, roadmap, and open questions.

Do not silently reinterpret an accepted decision. If implementation evidence
invalidates one, stop and propose a new Decision Record.

## Current state

The project is in `CANDIDATE_READINESS_DIAGNOSTICS_OFFLINE_REPAIR_READY`.

- The 13 decisions `DEC-001` through `DEC-013` are accepted.
- Phase 0 offline implementation and fixture-backed tests exist.
- Live bootstrap produced and verified a local `linux/arm64` candidate image
  lock.
- Historical non-canonical smoke
  `f1c9253b03dd4afca4284a89524562fb` terminated `UNSAFE` before readiness or
  measurement because observer-evidence sanitization prevented the authenticated
  post-up authority handoff.
- A post-terminal bounded repair removed that observer leakage, and the same
  authenticated run authority then completed a project-scoped stop. The smoke
  result remains `UNSAFE`; the later stop does not rewrite it.
- The follow-up offline repair closes the six static Must Fix items covering
  post-up failure classification, stop-authority retention, observer projection,
  append-only recovery sealing, image-lock test isolation, and named-volume
  ownership. These changes have offline test evidence only.
- The pre-smoke offline repair v2 closes four additional static Must Fix items:
  frozen-upstream Jaeger/Prometheus config-bind preservation, explicit
  compare-and-swap image-lock rotation, typed pre-mutation versus
  mutation-possible start disposition, and direct stop through
  `FreshStopAuthority`. These paths also have offline test evidence only.
- Required config binds are enforced by fixture-backed resolved mount-plan
  checks, but the repaired Compose plan has not been expanded by a real Docker
  runtime.
- Run `51002ad655ba4c65c1165be433664d7d` completed the separately authorized
  compare-and-swap migration from the legacy v1 binding to image-lock v2 with
  reason `RUN_INVARIANT_COMPOSE_CONTRACT_MIGRATION`. The current lock-content
  SHA-256 is
  `50f86b333fb6f1b66c16ff287a190995230b6ba2c1ec71cc0e56f38b783db5ac`;
  the exact legacy bytes remain in content-addressed history.
- The same non-canonical run
  `51002ad655ba4c65c1165be433664d7d` remains `FAILED`. Environment startup and
  authenticated ownership succeeded, but the 65-second candidate stabilization
  occurred after fresh preflight authority was acquired. The 30-second
  authority expired before lifecycle readiness, so no HTTP endpoint or
  propagation gate was attempted (`attempt_count=0`). This failure is not a
  successful smoke and must not be rewritten.
- The failed run completed fresh direct-stop authority, exact project-scoped
  container/network stop, owned named-volume cleanup, and final evidence seal.
  Those safety actions do not improve the run's `FAILED` verdict.
- The authority-TTL offline repair moves the 65-second candidate stabilization
  before fresh initial authority, removes that delay from the readiness
  collector, revalidates authority immediately after control-mutation readiness,
  and adds authenticated typed pre-HTTP failure matrices. These changes have
  offline tests only; they have not received live validation.
- The later non-canonical run `f5b0c63e18c156a3630bc769dc51b08d`
  remains `FAILED_SMOKE / INITIAL_CANDIDATE_READINESS_INCOMPLETE`. Across six
  candidate attempts, Prometheus, Jaeger, the direct probe, load-generator
  health, and OTel Collector health passed. OpenSearch freshness was present,
  but the v1 candidate parser did not recognize the actual hybrid source shape
  where `resource` contains the flattened key `service.name`. Task 7, baseline,
  fault, recovery, and final telemetry readiness were not executed. Exact safe
  stop, owned-volume cleanup, and final sealing succeeded without changing the
  failed verdict.
- The candidate-readiness diagnostics offline repair accepts only the approved
  flattened or nested OpenSearch service-identity shapes, fails closed on
  conflict/type/missing/shape errors, reuses the same parser in the OpenSearch
  adapter and Task 7 exact-identity check, and emits
  `phase0.candidate-initial-readiness.v2` with final-attempt endpoint and
  propagation diagnostics. Historical v1 evidence remains readable and is not
  rewritten. These changes have offline test evidence only.
- `OQ-001` is closed by the preserved real preflight fingerprint.
  `OQ-002` through `OQ-004` remain open.
- Phase 0 is incomplete. Formal three-cycle acceptance has not been run.
- PR disposition remains `Draft / REVIEW_REQUIRED`.
- Any future bounded smoke remains governed by
  `docs/PHASE_0_BOUNDED_REPAIR_SMOKE_PROMPT.md`.
- After sealed run `f5b0c63e18c156a3630bc769dc51b08d`, no additional smoke
  has been authorized. Phase 0 live revalidation is frozen. Do not infer a
  further smoke from offline repair, review, or green tests.
- The offline-repair scope itself does not authorize commit, push, or PR
  updates; publication requires a separate explicit user request. Publication
  does not authorize another smoke, deployment, release, formal three-cycle
  acceptance, or Phase 1 work.

Do not extend beyond the bounded-repair prompt. If a Phase 0 behavior is not
authorized by the planning packet and bounded-repair prompt, do not infer it.

## Scope discipline

- Keep phases literal. Do not pull later-phase agents, remediation, Kubernetes,
  AIOpsLab, Feature Service, or Ranking Service into Phase 0.
- Treat the explicit Phase 0 non-goals in
  `docs/PROJECT_CHARTER.md` as binding.
- Do not use the upstream OTel Demo Agent, MCP, or Chatbot as project
  functionality.
- Keep upstream OTel Demo `3.0.0` at commit
  `1755859a9de82c2e5e225be68abc401a5ebf2b4f` read-only.
- Never track upstream `main`, use `latest`, fall back to another release,
  enable amd64 emulation, or patch upstream to conceal a failed baseline.
- Preserve failed-run evidence. Never improve a result by deleting or
  selectively omitting runs.

## Safety

Before any environment command, follow `docs/SAFETY_BOUNDARIES.md`.

- Operate only on resources whose ownership is proven by project labels and
  manifests.
- Fail closed on unknown containers, networks, volumes, ports, files, or
  processes.
- Never stop, delete, or modify an unknown resource.
- Never use global Docker cleanup, arbitrary shell execution, host mutation,
  real credentials, cloud resources, or public write targets.
- `reset` restores scenario state; it does not delete evidence.
- Cleanup is a separate, explicit operation and remains project-scoped.

## Evidence and truth

- Machine-readable evidence is authoritative; UI screenshots are supplementary.
- Observer-visible and evaluator-only artifacts must remain separated.
- Agent-visible names, paths, tags, and URIs must not reveal scenario truth.
- Use UTC timestamps plus monotonic durations.
- Record schema versions, hashes, upstream commit, canonical Compose contract
  hash, per-run runtime Compose instance hash, image index digests, and
  resolved `linux/arm64` digests.
- Use the exact truth markers defined by the relevant acceptance or safety
  document. Do not smooth blocked or failed states into success language.

## Change discipline

- Keep diffs narrow and preserve unrelated user changes.
- Add tests and verification proportional to the active phase.
- Documentation may reference future interfaces, but must not claim that they
  exist.
- Update `docs/DECISIONS.md` when a binding choice changes.
- Update `docs/OPEN_QUESTIONS.md` when an unresolved item is resolved or a new
  stage-gated unknown is discovered.
- Do not duplicate normative rules across documents; link to the owning
  document.

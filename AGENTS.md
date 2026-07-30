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

The project is in `PHASE0_POST_UNSAFE_OFFLINE_REPAIR_READY`.

- The 12 decisions `DEC-001` through `DEC-012` are accepted.
- Phase 0 offline implementation and fixture-backed tests exist.
- Live bootstrap produced and verified a local `linux/arm64` candidate image
  lock.
- The single authorized non-canonical smoke
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
- The Compose override changed after the current image lock was created. The
  checked-in lock still binds the pre-repair resolved Compose hash. Before any
  future `up`, a separately authorized live task must re-resolve Compose and
  safely generate and verify a matching candidate lock; hash mismatch must fail
  closed.
- `OQ-001` is closed by the preserved real preflight fingerprint.
  `OQ-002` through `OQ-004` remain open.
- Phase 0 is incomplete. No second smoke or formal acceptance has been run.
- PR disposition remains `Draft / REVIEW_REQUIRED`.
- This bounded repair is governed by
  `docs/PHASE_0_BOUNDED_REPAIR_SMOKE_PROMPT.md`.
- The one-smoke allowance has been consumed. Do not run another smoke without
  new explicit authorization.
- The offline-repair scope itself does not authorize commit, push, or PR
  updates; publication requires a separate explicit user request. Publication
  does not authorize a second smoke, deployment, release, formal three-cycle
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
- Record schema versions, hashes, upstream commit, resolved Compose hash,
  image index digests, and resolved `linux/arm64` digests.
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

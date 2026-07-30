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

The project is in `PLANNING_FROZEN`.

- The 12 decisions `DEC-001` through `DEC-012` are accepted.
- The requested planning documents exist.
- Phase 0 implementation has not started.
- Passing documentation review does not authorize Docker, dependency
  installation, implementation, commit, push, PR, deployment, or release.

Do not enter Phase 0 work or goal mode without a new explicit user request.
The eight-file planning packet is the sole repository authority for Phase 0.
If a Phase 0 behavior is not authorized by that packet, do not infer it.

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

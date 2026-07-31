# EcomSRE-Agent Project Charter

## Status

| Item | State |
|---|---|
| Planning and decision freeze | accepted |
| Phase 0 implementation | bounded repair ended `UNSAFE`; review required |
| Phase 1–5 implementation | deferred |
| Production deployment | non-goal |

The binding decisions are indexed in [DECISIONS.md](DECISIONS.md). This charter
defines purpose and scope, not implementation details.

Decision references: `DEC-001`, `DEC-003`, `DEC-005`, and `DEC-009` through
`DEC-012`.

## Mission

EcomSRE-Agent will evaluate whether a verifiable, authority-separated
multi-agent system can improve incident response for e-commerce search,
advertising, and recommendation services under controlled, reproducible
conditions.

The project is not a role-playing demo. Multi-agent value must come from
parallel investigation, isolated tool contexts, structured evidence,
hypothesis adjudication, and separation of diagnosis, planning, execution, and
verification.

## Intended outcomes

- Reproducible incident environments and machine-readable evidence.
- Read-only telemetry and change tools with a versioned Evidence Contract.
- Equal-budget Single-Agent, Fixed Workflow, and dynamic Multi-Agent
  comparisons.
- A deterministic policy boundary around all writes.
- Limited claims supported by frozen, paired evaluation evidence.

## Current phase

The current phase is `AUTHORITY_TTL_OFFLINE_REPAIR_READY`. Historical
non-canonical runs retain their `UNSAFE` and `FAILED` outcomes. Run
`51002ad655ba4c65c1165be433664d7d` migrated the image lock to v2 but failed
before HTTP readiness because preflight authority expired during the old
stabilization ordering; its exact stop, owned-volume cleanup, and seal do not
change that verdict. The current repair has offline tests only and authorizes
no additional smoke. The disposition remains `REVIEW_REQUIRED`; `OQ-001` is
closed by the real preflight fingerprint, while `OQ-002` through `OQ-004`
remain open.

Phase 0 establishes a local, non-LLM fault loop on the frozen OpenTelemetry
Astronomy Shop baseline. Its normative acceptance contract is
[PHASE_0_ACCEPTANCE.md](PHASE_0_ACCEPTANCE.md).
The current bounded task is constrained by
[PHASE_0_BOUNDED_REPAIR_SMOKE_PROMPT.md](PHASE_0_BOUNDED_REPAIR_SMOKE_PROMPT.md).

## Later phases

- Phase 1: read-only tools, Evidence Contract, and Single-Agent baseline.
- Phase 2: dynamic investigation DAG, specialist agents, Evidence Store, and
  RCA Judge.
- Phase 3: restricted remediation, independent verification, and rollback.
- Phase 4: search, ads, and recommendation domain extensions.
- Phase 5: frozen paired evaluation and reproducible demonstration.

Entry and exit gates are defined in [ROADMAP.md](ROADMAP.md).

## Claims boundary

Phase 0 may establish only that the local environment and one advertising
availability proxy can complete a reproducible fault-and-recovery loop. It does
not establish:

- a full-site or commercial business SLO;
- multi-agent superiority;
- production readiness;
- safe autonomous remediation;
- generalization beyond the frozen environment.

Final claims remain limited to the frozen scenario suite and paired evaluation
protocol in `DEC-010` and `DEC-011`.

## Phase 0 non-goals

The following are explicitly outside Phase 0:

- LLM calls, model training, and model evaluation;
- Single-Agent or Multi-Agent diagnosis;
- Incident Commander, specialist Agents, and RCA Judge;
- LangGraph, CrewAI, AutoGen, or another agent framework;
- FastAPI or another application service layer;
- React or another project UI;
- Kubernetes and AIOpsLab;
- automatic remediation, Remediation Planner, Policy Gate, or Restricted Executor;
- custom Feature Service or Ranking Service;
- Agent performance or architecture-superiority conclusions;
- production-grade SRE, autonomous-SRE, or generalization claims.

Additional current non-goals are:

- production implementation during planning;
- use of upstream Agent, MCP, or Chatbot components;
- a private rewrite of the upstream Compose topology;
- real company systems, cloud resources, production credentials, or public
  write targets;
- arbitrary shell or general-purpose infrastructure automation.

## Unsupported in Phase 0

The first supported host is Apple Silicon running native `linux/arm64` through
Docker Desktop and Docker Compose v2. OrbStack, Podman, Colima, Intel Macs,
amd64 emulation, Linux, Windows, and other host combinations are unsupported,
not best-effort targets. See `DEC-001`.

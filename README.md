# EcomSRE-Agent

EcomSRE-Agent is a verifiable, authority-separated Agent runtime for
e-commerce incident diagnosis and replay-only restricted remediation. It uses
a custom lightweight Multi-Agent runtime, typed handoffs, a central budget,
run-scoped evidence, deterministic policy enforcement, and replayable reports.

This repository is an evidence-oriented local research system—not a production
autonomous SRE. Its public demo connects the implemented Phase 1–3 path without
Docker, provider credentials, live telemetry, or live mutation.

## One-command offline demo

```bash
uv sync --frozen --python 3.11
make agent-demo
```

The command runs the frozen `ad-partial-failure-complete` observer-visible
case through the real Dynamic Multi-Agent workflow and the real Phase 3
restricted-remediation runtime:

```text
Incident
→ Dynamic Multi-Agent diagnosis
→ structured RCA
→ deterministic Remediation Planner
→ Policy Gate
→ local-test approval
→ replay Restricted Executor
→ independent verification
→ REMEDIATION_VERIFIED
```

The concise result is printed to stdout. The complete deterministic report is
written to the ignored path
`artifacts/demo/agent-mainline-v1-report.json`.

Expected boundary markers include:

```text
Diagnosis backend: SCRIPTED_REPLAY
Remediation backend: REPLAY
Approval mode: LOCAL_TEST_AUTO_APPROVAL
Provider called: false
Docker called: false
Live execution: false
```

## Architecture

```mermaid
flowchart LR
    I["Incident"] --> D["Single / Fixed / Dynamic diagnosis"]
    D --> E["Run-scoped Evidence Store"]
    E --> J["RCA Judge"]
    J --> H["Typed Diagnosis Handoff"]
    H --> P["Deterministic Remediation Planner"]
    P --> G["Policy Gate"]
    G --> A["Human or explicit local-test approval"]
    A --> X["Restricted Executor"]
    X --> V["Independent Verifier"]
    V -->|"verified"| R["Terminal report"]
    V -->|"failed / inconclusive"| B["Exact compensating rollback"]
    B --> R

    classDef agent fill:#e8f1ff,stroke:#2457a7,color:#102a43;
    classDef deterministic fill:#eaf8ee,stroke:#237a3b,color:#123d20;
    class D,J agent;
    class E,H,P,G,A,X,V,B,R deterministic;
```

The Commander, Specialists, and RCA Judge are the Agent-facing diagnosis
components. Evidence resolution, budgets, handoffs, planning, policy,
execution, verification, and rollback are typed deterministic components. An
LLM or scripted model may propose only through its admitted response contract;
it cannot expand the Policy Gate or Executor authority.

## Current status

| Phase | Current truth |
| --- | --- |
| Phase 0 | `REVIEW_REQUIRED`; canonical acceptance is **not complete** and the historical `UNSAFE / FAILED / BLOCKED` evidence is preserved |
| Phase 1 | `PHASE1_SINGLE_AGENT_REPLAY_MVP_READY` |
| Phase 2 | `PHASE2_MULTI_AGENT_REPLAY_MVP_READY`; offline comparison and bounded provider gate verified, with no superiority claim |
| Phase 3 | `PHASE3_RESTRICTED_REMEDIATION_REPLAY_MVP_READY`; replay-only |
| Phase 4 | Not entered |
| Phase 5 | Not entered |

The authoritative detail lives in the [Roadmap](docs/ROADMAP.md),
[Decision Register](docs/DECISIONS.md),
[Phase 0 acceptance contract](docs/PHASE_0_ACCEPTANCE.md),
[Phase 2 closeout](docs/PHASE_2_CLOSEOUT.md), and
[Phase 3 disposition](docs/review-evidence/phase3-restricted-remediation/current-disposition.json).

## Evidence boundaries

The phases intentionally make different claims:

| Surface | Evidence-backed boundary |
| --- | --- |
| Phase 1 scripted replay | 7 frozen observer-visible cases |
| Phase 1 real-provider gate | 2 bounded cases: one positive and one negative |
| Phase 2 offline comparison | 7 cases × 3 variants: Single, Fixed, and Dynamic |
| Phase 2 real-provider gate | 4 bounded Fixed/Dynamic positive/negative runs |
| Phase 3 remediation | 6 deterministic replay cases; no Docker, provider, or live mutation |
| Agent Mainline V1 demo | One deterministic scripted replay integration case; not an evaluation or provider result |

The Phase 1 real-provider result is **not** 7/7.
The Phase 2 comparison does not establish Multi-Agent superiority.
Provider-smoke reports are bounded gates and are not silently substituted for
the offline comparison.

Observer-visible case files and evaluator-only truth use separate roots. The
demo loads only `config/phase1/replay-cases/agent-visible`; it does not import or
read evaluator ground truth. Reports contain typed summaries and semantic
digests, not raw provider transcripts, credentials, authorization headers, or
host-absolute paths.

## Quickstart and verification

Python 3.11 is explicit because the frozen `tiktoken==0.13.0` dependency is
locked to a cp311 wheel on the supported local platform.

```bash
uv sync --frozen --python 3.11

# Public integration path
make agent-demo

# Focused phase checks
make phase1-test
make phase2-test
make phase3-test

# Recompute and verify deterministic reports
make phase2-compare
make phase2-verify
make phase3-replay
make phase3-verify
```

No provider configuration is needed for any command above. Provider smoke is a
separately authorized and separately reported gate; it is not part of the demo
or CI.

## Results

The current integration baseline records:

| Check | Result |
| --- | ---: |
| Phase 1 tests | 877 passed |
| Phase 2 tests | 378 passed |
| Phase 3 tests | 27 passed |
| Agent Mainline V1 demo tests | 8 passed |
| Full repository tests | 2,127 passed |
| Phase 2 comparison | 7 × 3 report verified |
| Phase 2 real-provider gate | 4 bounded requirements passed |
| Phase 3 replay evaluation | 6 cases verified |

These counts are development evidence for the named revision. They are not a
release, production-readiness, Phase 0 acceptance, or model-quality claim.

## Why a custom runtime?

- **Typed handoffs instead of free-form agent chat.** Commander, Specialists,
  Judge, and remediation consume closed Pydantic contracts with run and
  incident identity checks.
- **One central budget boundary.** Single, Fixed, and Dynamic variants share
  the same outer model-call, tool-call, and token caps so orchestration is not
  treated as free.
- **Deterministic authority after diagnosis.** Planner, Policy Gate, approval
  binding, Restricted Executor, Verifier, and rollback are not LLM agents.
- **Run-scoped evidence capabilities.** Agents cite evidence references that
  must resolve to immutable bodies in the current run's Evidence Store.
- **Small, inspectable core.** The project does not depend on LangGraph,
  CrewAI, or AutoGen for orchestration; it implements only the scheduling,
  budget, evidence, and authority contracts required by its evaluation.

## Repository map

```text
src/ecomsre/phase1/   Single-Agent RCA and read-only evidence contracts
src/ecomsre/phase2/   Fixed/Dynamic workflows, Commander, Specialists, Judge
src/ecomsre/phase3/   Planner, Policy Gate, replay executor, verifier, rollback
src/ecomsre/demo/     Thin public Phase 2 → Phase 3 offline integration
config/phase1/        Frozen observer-visible replay cases
eval/                 Evaluator-only scoring surfaces; never read by the demo
tests/                 Contract, replay, isolation, and regression checks
```

## Limitations

- Phase 0's live environment has not passed canonical acceptance.
- Remediation is process-local and replay-only; it does not write Docker,
  feature flags, cloud systems, or production resources.
- The public demo uses an evidence-driven deterministic scripted backend. It
  exercises integration behavior but does not replace the frozen Phase 2
  comparison baseline or the bounded real-provider gate.
- Phase 5's frozen 12+ template superiority evaluation has not been run.
- No production write capability, live remediation, release, or deployment is
  claimed.

See [Safety Boundaries](docs/SAFETY_BOUNDARIES.md) for the permanent
prohibitions and exact authority model.

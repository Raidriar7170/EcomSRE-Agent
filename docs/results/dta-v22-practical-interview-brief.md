# DTA v2.2 Practical Interview Brief

## 30-second summary

I inherited a diagnosis Agent whose strict v2.2 research harness was blocked by
Provider protocol and evidence-governance gates. I separated the useful
algorithmic core from that machinery, kept runtime-owned state and a zero-write
boundary, added a simple H/A/E alias adapter, and ran one fixed 12-case Flat
versus Planner replay. Planner produced three exact outcomes versus Flat's one
and met the small practical threshold, but absolute accuracy remained weak, so
I report it as an engineering experiment rather than a research win.

## 90-second walkthrough

The controller, not the model, owns authority. It bootstraps a bounded topology,
builds a closed hypothesis catalog and a canonical read-action catalog, projects
only compact aliases to the Provider, validates one returned decision, and
either admits a typed terminal or dispatches exactly one authorized replay read.
Only an exact matching outcome updates Salient Memory and the Belief Ledger.
Flat sees the same canonical state without the ledger; Planner-Lite also sees a
compact working-hypothesis and coverage view.

The original branch optimized for strict schema probing, identity manifests,
private evidence seals, and campaign verification before it had completed the
basic experiment. I preserved that blocked PR as negative evidence and built a
practical successor without those gates. An 8-transition Provider smoke passed,
the 8-case development campaign completed, and the frozen 12-case evaluation
ran once. There were zero transport failures, uncaught exceptions, duplicate
reads, or Agent writes. Planner met the preregistered threshold with Macro-F1
0.1333 versus 0.0 and equal reads, but only one of eight incident mechanisms
was correct, which is the more important limitation.

## Evolution: v2 → v2.1 → v2.2

- **v2:** established typed diagnosis-to-action contracts and a bounded Agent
  path, including real local demonstrations, but mixed diagnosis quality with
  downstream remediation evidence.
- **v2.1:** froze the Agent and exposed capability limits. No-Fault produced a
  false-positive Diagnosis and Ad CPU stopped on a duplicate read. Those
  negative results were preserved instead of tuned away.
- **strict v2.2:** introduced Planner/Flat controller concepts but coupled
  progress to Provider probes, manifests, private evidence, and exact campaign
  gates. PR #60 remained blocked.
- **practical v2.2:** recovered the controller algorithm, simplified only the
  Provider boundary and evaluation governance, kept runtime safety, and finally
  executed the comparison.

## Why the v2.1 Planner failed

The v2.1 behavior was model-led rather than a durable runtime state machine. It
could repeat a read, lose evidence context, or commit a diagnosis without a
controller-owned coverage model. The frozen negative cases showed that a prompt
alone was not a reliable planner.

## Why strict v2.2 got stuck

The strict branch made model/output-mode probing, identity binding, manifests,
private attempt evidence, and campaign verification prerequisites for the core
experiment. Those controls were internally coherent but formed a large
Provider compatibility gate. The research path therefore stopped at
`BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE` before completing the practical
Flat-versus-Planner evaluation.

## What was simplified

- one configured OpenAI-compatible Provider adapter;
- static tool-call shape with ordinary JSON fallback;
- compact H/A/E aliases mapped locally to canonical IDs;
- at most one semantic repair, separate from exact transport retries;
- a simple SHA-256 evaluation manifest;
- public replay normalization with synthetic cases labeled explicitly.

No Provider probe, identity manifest, private evidence seal, attempt artifact,
or campaign verifier was carried over.

Public v2/v2.1 captures also required three explicit practical compatibility
clauses: configuration from strong error metric plus first-error trace, memory
from strong growth plus healthy runtime, and bounded No-Incident admission.
Those clauses are documented as a successor policy, not represented as the
unchanged strict PR-C policy.

## Core technical pieces

### Runtime-managed belief state

The Belief Ledger tracks the working hypothesis, executed actions, covered
capabilities, evidence cost, and repair use. Provider output cannot directly
mutate it.

### Canonical Action Catalog

Reads are generated from topology and capability registry, then masked by
budget and prior coverage. A Provider selects an alias; the adapter resolves it
to the exact canonical action before dispatch.

### Salient Memory

Read outcomes are normalized to stable evidence refs, predicates, concise
facts, and loss records. Full raw telemetry is not sent back to the model.

### Simple Provider adapter

The adapter accepts a forced static function call or normal JSON, rejects
unknown or wrong-kind aliases, retries only 429/5xx/timeout/reset transport
failures, and never logs the key or Authorization header.

## Actual fixed evaluation

| Metric | Flat | Planner-Lite |
| --- | ---: | ---: |
| Exact completion | 1/12 | 3/12 |
| Valid terminal | 9/12 | 10/12 |
| Mechanism accuracy | 0.0000 | 0.1250 |
| Mechanism Macro-F1 | 0.0000 | 0.1333 |
| Post-repair protocol success | 1.0000 | 1.0000 |
| Mean reads | 0.0000 | 0.0000 |
| Total tokens | 17,883 | 20,201 |
| Uncaught exceptions / Agent writes | 0 / 0 | 0 / 0 |

The practical Planner threshold was met, but the result is best described as a
small positive signal inside a mostly negative quality result.

## Safety boundary

This sprint was replay-only. It invoked no Docker, Runbook, Agent write,
restricted executor, or live remediation. A Provider can propose only an H/A/E
selection; local code owns aliases, budgets, dispatch, memory updates, terminal
admission, and the zero-write guarantee.

## Five likely interviewer questions

1. **Why not repair the strict harness?**  Because it blocked algorithm
   validation on evidence-governance infrastructure. I preserved it and tested
   the narrower hypothesis with a simpler boundary.
2. **Is Planner better?**  It met the predefined small-set threshold and had
   three exact results versus one, but one correct incident is insufficient for
   a broad superiority claim.
3. **How do you prevent hallucinated tool use?**  The Provider sees aliases;
   local code resolves them against a turn-specific canonical catalog, rejects
   stale or unknown IDs, and binds outcomes to the exact dispatch.
4. **Why were there no adaptive reads in the fixed set?**  The model usually
   abstained from bootstrap. That is a real negative result and the main target
   for future development-only prompt work.
5. **What would you do next?**  Acquire more independent replay captures,
   preregister a new prompt that encourages bounded evidence acquisition, and
   evaluate once on a new held-out set without weakening semantic admission.

## Engineering lesson

> The original research harness over-optimized provenance and strict protocol
> binding. I separated core algorithm validation from evidence-governance
> infrastructure, kept the zero-write boundary, and completed the actual
> Flat-vs-Planner experiment through a simpler adapter.

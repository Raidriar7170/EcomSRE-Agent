# DTA v2.2.5 Real-Fault Shadow Interview Brief

## 30-second version

I built a bounded real-telemetry transfer study comparing the current v2.2.5
runtime-guided `BUNDLE_ONE` path with a **v2-style Flat Adaptive baseline using
the v2.1 CPU-capable ontology**. I reused the existing owned Ad CPU lifecycle,
captured one real baseline and one real CPU fault, rendered them through two
opaque swapped service maps, and ran the exact eight-run paired schedule once.
The fault was real and safely restored, but both arms scored 0/4 exact and all
runs failed protocol validation. Current also recorded no successful bundle
read in either live shadow, so I retained the negative transfer terminal rather
than tuning or rerunning. Cleanup was `CLEAN`, non-owned changes were zero, and
Agent writes and Runbooks remained zero.

## Personal contribution

- Reused the existing v2.1 owned Docker, Ad CPU fault, restoration, and cleanup
  lifecycle instead of building a second fault system.
- Added a capture-only successor that exposes no Agent write, ActionProposal,
  Runbook, or remediation path.
- Built shared physical/opaque capture contracts, two inverse identity maps,
  complete Provider-payload linting, and a truth-after-pair boundary.
- Built a canonical frozen-capture backend plus the comparison-only v2-style
  Flat runner and the current snapshot/live action adapter.
- Separated shared capture cost, semantic evidence actions, and
  target-equivalent reads.
- Bound model, prompts, scorer, schedule, captures, truth, and one-shot claims
  before the final paired run.
- Preserved the first admission-only infrastructure terminal, proved it had no
  Docker/fault/Provider boundary, and consumed the single authorized
  replacement only after exact reconciliation.
- Preserved the negative model/runtime result, exact eight-run journal, and
  cleanup evidence without rerunning for a better score.

## Architecture to explain

```text
one owned v2.1 Sandbox lifecycle
  -> exact healthy baseline proof
  -> one baseline physical capture
  -> optional Current live baseline shadow
  -> existing AD_CPU_SATURATION injection and impact proof
  -> one fault physical capture
  -> exactly one Current live fault shadow via LocalSandboxReadBackend
  -> exact baseline restore and owned CLEAN cleanup
  -> two inverse opaque maps over each physical state
  -> four frozen cases x two read-only diagnosis arms
  -> truth loaded only after each pair
  -> one preregistered scorer
```

The primary paired comparison controls environment bytes. The live shadow tests
adapter transfer only. The alias swap is an identity counterfactual; the
physical fault target was always Ad.

## Strongest evidence

- Real fault separation: target CPU baseline maximum `3.403%`, fault samples
  `400.784%` to `406.107%`; comparator fault maximum `2.514%`.
- Exact accounting: 2 physical captures, 4 opaque cases, 8 paired runs,
  `execution_count=1`, truth-load ordinals `2/4/6/8`.
- Fairness: same per-case bytes, aliases, Runtime/Metrics bootstrap, model,
  ontology, action/read budgets, and evaluator truth.
- Safety: one live fault shadow, one optional live baseline shadow, zero Agent
  writes, zero ActionProposals, zero Runbooks, exact baseline restoration,
  `CLEAN`, zero non-owned changes.
- Negative-result integrity: 8 protocol failures, 0 transport failures, 0
  retries, and no study rerun.

## What the result means

`DTA_V225_REAL_FAULT_TRANSFER_NOT_SUPPORTED` means the current snapshot and
live paths did not produce the required exact terminals on this real CPU-fault
surface. `CURRENT_RUNTIME_DESCRIPTIVE_ADVANTAGE` is only the frozen cost-rule
outcome: exact quality tied at zero while Current made fewer calls and reads by
failing before Provider selection. It is not evidence that Current diagnosed
better.

## Safe claims and non-claims

Safe: exact one-campaign/one-execution accounting, real owned CPU capture,
opaque paired-byte fairness, fail-closed read-only behavior, preserved negative
result, exact restoration, and project-scoped cleanup.

Do not claim: the exact original v2 identity, successful real-telemetry
transfer, a causal or statistical architecture advantage, alias-robust process
behavior, multiple independent fault episodes, physical-target
counterfactuals, remediation quality, production readiness, or real-world
generalization.

# RCAEval Multi-Agent Communication Audit

## Verdict

**METRICS_ARBITRATION** — A deterministic metrics rule shows robust positive net rescue.

This is consumed OB/SS development evidence and a post-hoc diagnostic, not external validation or primary inference. Provider calls: **0**.

| Preserved candidate | Terminals | Completed | Failed |
|---|---:|---:|---:|
| candidate-3 | 60 | 60 | 0 |
| candidate-4 | 60 | 59 | 1 |
| candidate-5 | 60 | 60 | 0 |

## What the real v2 path communicates

The Initial call is the Strong Single contract over the full bounded `ArchitectureContext`; it is not the older Adaptive-v1 `InitialDiagnosisInput`. Candidate-3 and Candidate-4 use a free single-source specialist contract. Candidate-5 changes only Logs to an Initial-vs-Metrics-Alternative pairwise contract; Trace remains the free contract. Fusion is deterministic and receives more provenance than the Logs pairwise verifier.

The pairwise Logs verifier receives both identities, the Initial indicator, Logs evidence, and visible Logs references. It does **not** receive Metrics rank/score/margin, Gate reasons, Initial confidence/explanation/evidence references, or the Metrics selection rationale. Sidecars preserve hashes and accounting, not request/response bodies, so those envelopes are reconstructed from typed contracts and bounded raw inputs.

## Counterfactual gates

- Robust Metrics rules: `M1, M2, M3`; selected: `M3`.
- Trace visibility gate: `False`; genuine causal information: `False`.
- Relaxed Fusion shows positive net rescue: `True`.
- Communication repair eligible: `False`; new cross-source verifier redundant: `True`.

For selected rule M3, root-only results are:

| Preserved candidate | Completed | Initial root correct | M3 root correct | Overrides | Rescues | Damages | Net rescue |
|---|---:|---:|---:|---:|---:|---:|---:|
| candidate-3 | 60 | 49 | 57 | 8 | 8 | 0 | 8 |
| candidate-4 | 59 | 51 | 57 | 6 | 6 | 0 | 6 |
| candidate-5 | 60 | 45 | 57 | 12 | 12 | 0 | 12 |

M3 overrides the root only when the deterministic Metrics alternative is rank 1, normalized top-1/top-2 margin is at least 0.25, and the Initial service is absent from the top two. The Initial indicator is frozen. Across the three preserved candidates M3 yields zero root damage and positive net rescue; this is development evidence, not a claim of held-out generalization.

## Specialist, Fusion, and Trace evidence

Candidate-4 produced 44 free-generation hypotheses. Truth-matching hypotheses appeared in 8 calls overall, but correct alternatives for Initial-wrong cases appeared at rank 1 / any rank in 0 / 0 calls.

Candidate-5 had 23 pairwise calls. Both candidates were Logs-visible in 2/23; candidate provenance, strength, Gate reason, and Initial rationale were each present in 0 of those calls. Causal-role comparisons below are explicitly heuristic because evaluator truth provides root identity, not a propagated-symptom oracle.

Current Fusion replay was value-identical in 60/60. F1/F2/F3 each produced net rescue `0` / `1` / `0`; bottleneck verdict: `FUSION_IS_BOTTLENECK`.

Among 15 Initial-wrong cases with a truth-matching Metrics alternative, it was Trace-visible in 2/15, with 2 co-visible pairs. The support gate failed, and the bounded projection lacks causal edges/propagation roles.

## Static message-contract ablation

| Contract | Fields | Provenance | Source sufficient | Both candidates visible | Mean serialized bytes | Evidence duplication |
|---|---:|---:|---:|---:|---:|---:|
| C0 | 5 | 0/23 | 0/23 | 2/23 | 1261.5 | 138/276 |
| C1 | 9 | 0/23 | 0/23 | 2/23 | 1395.1 | 138/276 |
| C2 | 9 | 23/23 | 0/23 | 2/23 | 1390.6 | 138/276 |
| C3 | 7 | 0/23 | 0/23 | 2/23 | 2773.3 | 163/376 |
| C4 | 14 | 23/23 | 13/23 | 23/23 | 2633.6 | 138/414 |
| C5 | 13 | 23/23 | 2/23 | 6/23 | 986.3 | 60/120 |

These are static envelope measurements over preserved inputs, not generated answers. C0 is the current Logs pairwise envelope; C1 adds Gate context; C2 adds Metrics provenance; C3 adds Initial rationale; C4 combines Gate + Metrics + bounded Metrics/Logs; C5 is the analogous Metrics + Trace pairwise envelope.

## Interpretation

A communication defect is real only when a source can compare the candidates, the correct alternative is already available, a missing field changes the action boundary, and the resulting override survives root-damage accounting. More context alone is not evidence for a new Agent. The existing Initial already sees all bounded sources, while the bounded Trace projection lacks caller/callee edges, error propagation, and explicit root-versus-symptom roles.

Ranked communication verdict:

1. `SOURCE_SIGNAL_INSUFFICIENT` — `SUPPORTED`.
2. `MULTI_STAGE_REDUNDANCY` — `SUPPORTED`.
3. `MESSAGE_CONTRACT_LOSS` — `PARTIALLY_SUPPORTED`.
4. `SPECIALIST_TASK_DEFINITION` — `UNRESOLVED`.
5. `FUSION_OVERSTRICT` — `SUPPORTED`.

The recommended runtime shape retains one Strong Single model call and adds only deterministic M3 root arbitration; it does not retain a model-based Multi-Agent root arbiter. Expected model calls: **1**. Implementation of that runtime change is outside this audit and remains unauthorized.

## Boundaries

No candidate was rerun. No new Provider or Agent was invoked. No RE2-TT or new data was read. Public artifacts contain aggregate counts/rates only; case-level identities, service names, evidence references, rationales, and raw outputs remain in the Git-external private audit root.

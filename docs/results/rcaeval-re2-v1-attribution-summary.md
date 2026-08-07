# RCAEval RE2 v1 frozen-result post-hoc attribution

> **POST_HOC_EXPLORATORY · NOT_PRIMARY_INFERENCE · NO_HOLDOUT_RERUN**

This report attributes the already frozen negative result. It made no Provider calls, did not rerun or retry RE2-TT, and did not change prompts, models, tools, records, locks, the Final Report, or the primary inference.

## Frozen result remains unchanged

- Dynamic − Single Root Service AC@1: **-0.1889**.
- Frozen 95% CI: **[-0.2889, -0.0889]**.
- Primary superiority supported: **No**.
- Cost-quality supported: **No**.
- Frozen records / attempt markers: **270 / 270**; attribution Provider calls: **0**.

## Architecture decomposition

| Architecture | Root service | Pair | Completed-only service | Failures | Reliability ceiling* |
|---|---:|---:|---:|---:|---:|
| Single | 84/90 (0.9333) | 49/90 (0.5444) | 84/89 (0.9438) | 1 | 85/90 (0.9444) |
| Fixed | 67/90 (0.7444) | 36/90 (0.4000) | 67/81 (0.8272) | 9 | 76/90 (0.8444) |
| Dynamic | 67/90 (0.7444) | 37/90 (0.4111) | 67/79 (0.8481) | 11 | 78/90 (0.8667) |

* Exploratory upper bound that assumes every terminal failure becomes correct; it is not model performance.

Fixed's 17-case correct-count gap decomposes into 8 excess terminal failures plus 9 excess completed-but-wrong outcomes. Dynamic's 17-case gap decomposes into 10 plus 7. Both identities reconcile exactly.

## Six required questions

### Q1. How much of the negative result comes from Terminal Failure?

Single / Fixed / Dynamic terminal failures are 1 / 9 / 11. Relative to Single, Fixed adds 8 failures and Dynamic adds 10. Even the all-failures-correct ceilings are 76/90 (0.8444) and 78/90 (0.8667); failures alone therefore do not fully explain either 17-case gap.

### Q2. Do Multi-Agent arms still degrade on Completed Runs only?

Yes. Completed-only Root Service accuracy is Single 84/89 (0.9438), Fixed 67/81 (0.8272), and Dynamic 67/79 (0.8481). This is descriptive post-hoc evidence, not a new primary inference.

### Q3. Does Fixed turn Single-correct answers wrong with the same three sources?

Yes: 11 of 90 paired outcomes were both completed, Single-correct, and Fixed-wrong. Because Single and Fixed receive Metrics, Logs, and Traces, these are classified as SAME_SOURCE_SET_SEMANTIC_DEGRADATION. The precise internal mechanism remains UNOBSERVABLE_FROM_FROZEN_ARTIFACTS.

### Q4. Did Dynamic materially save tools?

Dynamic has 0 two-tool runs, 86 three-tool runs, and 4 other runs. Total calls are 262; mean reduction versus Single is 8/270 (0.0296), and the paired median reduction is 0.0000. This supports DYNAMIC_ROUTE_DEGENERACY_SUPPORTED for the frozen distribution only. The exact skipped source is UNOBSERVABLE_FROM_FROZEN_ARTIFACTS.

### Q5. Where does Root Cause Pair accuracy fail?

The aggregate JSON reports each fault's raw-schema → Metrics top-6 → final-selection funnel and full indicator confusion matrices. Memory is classified **TOOL_RANKING_GAP**: raw 15/15 (1.0000), top-6 0/15 (0.0000). Socket is classified **MIXED**: raw 15/15 (1.0000), top-6 6/15 (0.4000). This separates raw signal gaps, ranking losses, and final indicator reasoning without inventing Provider traces.

### Q6. Is Train Ticket deterministically harder than OB / SS at the tool layer?

Any-source root-service Coverage@6 is TT 90/90 (1.0000), OB 90/90 (1.0000), and SS 90/90 (1.0000); by contrast, truth-indicator Metrics top-6 coverage is TT 59/90 (0.6556), OB 82/90 (0.9111), and SS 83/90 (0.9222). The adjudicated distribution-shift verdict is **mixed**. SS Traces remain SOURCE_UNAVAILABLE, not zero-valued evidence. Without full-protocol OB/SS Provider evaluation, no accuracy-point effect can be claimed.

## Hypothesis matrix

| ID | Result | Confidence | Evidence level |
|---|---|---|---|
| H1 | supported | high | LEVEL_1_DIRECT |
| H2 | supported | high | LEVEL_1_DIRECT + LEVEL_2_DETERMINISTIC_RECONSTRUCTION |
| H3 | supported | high | LEVEL_1_DIRECT |
| H4 | not_supported | high | LEVEL_2_DETERMINISTIC_RECONSTRUCTION |
| H5 | supported | high | LEVEL_2_DETERMINISTIC_RECONSTRUCTION |
| H6 | mixed | medium | LEVEL_2_DETERMINISTIC_RECONSTRUCTION |
| H7 | unobservable | low | UNOBSERVABLE |
| H8 | unobservable | none | UNOBSERVABLE |
| H9 | mixed | medium | LEVEL_3_INDIRECT_INFERENCE |

Detailed supporting and contradicting observations, limits, and next experiments are in the aggregate JSON.

### H1 — Multi-call reliability amplification

- Supporting: Fixed and Dynamic used more model calls and had 9 and 11 failures, versus 1 for Single.
- Contradicting: The frozen artifacts do not expose a per-operation hazard rate.
- Cannot conclude: More calls are not proven causal, and the failing operation is unobservable.
- Next experiment: Persist per-operation status and estimate transport/schema hazard by stage on development data.

### H2 — Same-source Multi-Agent semantic degradation

- Supporting: 11 both-completed outcomes were Single-correct and Fixed-wrong.
- Contradicting: Fixed recovered 2 Single-wrong outcomes.
- Cannot conclude: Specialist anchoring, Judge anchoring, context redundancy, and label bias cannot be separated.
- Next experiment: Persist intermediate assessments and run architecture-blind Judge ablations on development data.

### H3 — Dynamic route degeneracy

- Supporting: 86 of 90 Dynamic runs used three tools.
- Contradicting: 4 runs used one tool, but all were terminal failures; no two-tool route was observed.
- Cannot conclude: The exact skipped source and the counterfactual accuracy of another route are unobservable.
- Next experiment: Persist Commander decisions and evaluate truly sequential routes on development data.

### H4 — Root-service tool projection misses

- Supporting: None in frozen artifacts.
- Contradicting: 14 Fixed completed-wrong runs had the truth service visible in at least one projection.
- Cannot conclude: Visibility does not prove the model attended to or correctly interpreted the evidence.
- Next experiment: Measure source-specific recall and feed ranked supporting and contradicting evidence.

### H5 — Indicator pipeline failure

- Supporting: Memory classification is TOOL_RANKING_GAP; Socket classification is MIXED.
- Contradicting: Other fault families retain non-zero pair accuracy.
- Cannot conclude: No Provider-internal reasoning trace identifies why a visible indicator was rejected.
- Next experiment: Add deterministic metric-to-indicator candidates and test the full raw/top-6/final funnel.

### H6 — Cross-system tool distribution shift

- Supporting: Truth-indicator Metrics top-6 coverage is TT=0.6555555555555556, OB=0.9111111111111111, SS=0.9222222222222223.
- Contradicting: Any-source root-service Coverage@6 is TT=1.0, OB=1.0, SS=1.0; system schemas and trace availability also differ.
- Cannot conclude: No full-protocol OB/SS Provider accuracy exists, so performance-point impact is unknown.
- Next experiment: Create a development-only semantic evaluation shared across OB, SS, and TT-like cases.

### H7 — Specialist-to-Judge anchoring

- Supporting: Same-source semantic degradation is compatible with anchoring.
- Contradicting: No frozen SpecialistAssessment or Judge input is persisted.
- Cannot conclude: UNOBSERVABLE_FROM_FROZEN_ARTIFACTS
- Next experiment: Persist SpecialistAssessment, CommanderDecision, and exact Judge input.

### H8 — Architecture-aware Judge bias

- Supporting: None in frozen artifacts.
- Contradicting: No architecture-blind ablation is part of the frozen run.
- Cannot conclude: UNOBSERVABLE_FROM_FROZEN_ARTIFACTS
- Next experiment: Blind architecture labels while holding evidence and prompt content constant.

### H9 — Model capability is the primary bottleneck

- Supporting: Single pair accuracy remains materially below its root-service accuracy.
- Contradicting: Single root-service correctness is 84/90, while both Multi-Agent arms are lower; architecture therefore adds a separate observed degradation.; Dynamic recovered 0 Single-wrong outcomes but damaged more Single-correct outcomes.
- Cannot conclude: The frozen comparison does not isolate model capacity from prompting and architecture.
- Next experiment: Use development-only controlled prompts with identical evidence and architecture-blind scoring.

## Evidence gaps

Specialist candidate accuracy, Judge follow-rate, the exact Provider failure stage, exact invalid-schema field, Dynamic skipped source, Specialist/Judge anchoring, and architecture-aware Judge bias are UNOBSERVABLE_FROM_FROZEN_ARTIFACTS.

## Evidence-ranked next-version recommendations

### P0

- **Persist SpecialistAssessment, CommanderDecision, Judge inputs, and per-operation status.** Observed mechanism: Internal stage attribution is currently unobservable despite elevated Multi-Agent failures. Expected benefit: Makes stage-specific reliability and anchoring hypotheses directly testable. Risk: More restricted review storage increases leakage-control obligations. Development test: Hash-bound replay verifies complete stage records without Agent-visible truth leakage. New external holdout: No for instrumentation validation; yes for final performance claims.

- **Repair and directly test the metric indicator pipeline.** Observed mechanism: The raw/top-6/final funnel and zero Memory/Socket pair scores expose indicator loss. Expected benefit: Raises pair accuracy without changing root-service selection. Risk: Rule-based mappings may overfit benchmark naming conventions. Development test: Per-fault raw coverage, top-6 coverage, confusion, and unseen-name robustness. New external holdout: Yes before any new external pair-accuracy claim.

### P1

- **Use Single-first adaptive escalation with Metrics-only termination and truly sequential routing.** Observed mechanism: Most Dynamic runs acquired all three sources while Multi-stage semantics damaged Single-correct cases. Expected benefit: Protects strong Single outcomes and spends extra calls only on uncertainty. Risk: An incorrect confidence gate may suppress useful escalation. Development test: Calibrated escalation precision/recall, route counts, accuracy, and failure accounting. New external holdout: Yes for architecture superiority or cost-quality claims.

- **Provide top-k hypotheses with supporting/contradicting evidence and an architecture-blind Judge.** Observed mechanism: Truth services were often visible when Fixed still selected the wrong service. Expected benefit: Reduces premature commitment and tests whether labels influence fusion. Risk: Larger structured context may increase cost and schema failures. Development test: Same-evidence blind ablation with rescue/damage pairwise accounting. New external holdout: Yes for final architecture claims.

### P2

- **Replace homogeneous model-only specialists with algorithmic metric, log-delta, and trace-root rankers.** Observed mechanism: Source projection and final fusion failures require source-specific, testable hypotheses. Expected benefit: Adds genuinely heterogeneous signals and deterministic failure localization. Risk: Specialist heuristics may encode dataset-specific assumptions. Development test: Source-isolated recall, calibration, causal-role labels, and cross-system robustness. New external holdout: Yes after development selection is frozen.

### P3

- **Adopt strict structured output where supported, transport-only retry under a new protocol, parallel Fixed specialists, and reduced repeated context.** Observed mechanism: Multi-Agent arms amplify Provider operations, latency, tokens, and terminal failures. Expected benefit: Improves run reliability and cost without hiding semantic failures. Risk: Retries change the estimand and parallelism can create new rate-limit behavior. Development test: Per-operation failure, retry disposition, latency, token, and schema accounting. New external holdout: Yes because retry and execution semantics define a new protocol.

## Review disposition

`POST_HOC_ATTRIBUTION_REPORT_READY_FOR_HUMAN_REVIEW`

Human review is required. This report does not authorize merge, rerun, retry, release, or a replacement primary claim.

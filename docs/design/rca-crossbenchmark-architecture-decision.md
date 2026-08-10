# RCA Cross-Benchmark Architecture Decision

Status: **FROZEN — STRONG_SINGLE_HIERARCHICAL**

Classification: `CONSUMED_CROSS_BENCHMARK_DEVELOPMENT, POST_HOC_ARCHITECTURE_ATTRIBUTION, NOT_EXTERNAL_VALIDATION, NOT_PRIMARY_INFERENCE`.

This record is the append-only corrected successor to an invalid GT-derived/unfrozen analysis attempt. Frozen thresholds were not changed.


## Decision

Select `A0` / `STRONG_SINGLE_HIERARCHICAL` as the only `unified-hierarchical-rca-v1` architecture. This decision uses frozen consumed-development evidence only, preserves the official results, and is not external validation or primary inference.

Rejected options: A2, A3, A4, A5. A1 is historical comparison only and is not selectable.

## Evidence

- Entity hierarchy: RCA100 denominator `103`; multi-level exact, service, workload, node, and topology-component diagnostics are frozen in the aggregate report.
- Propagation and visibility: all source funnels, causal roles, Strong Single failures, M3 failures, and fault-phrase relations are frozen in the aggregate report with explicit denominators.
- Communication: the Trace/Topology Causal Verifier was assessed only as an oracle upper bound; no verifier output or Provider call was fabricated.
- Cross-benchmark counterfactual: all A0–A5 outcomes cover the fixed 103 + 60 + 60 + 60 + 60 + 120 records.
- Robustness: `205` grouped leave-one-out folds are frozen.
- Cost: the selected architecture's expected call count is the value frozen for `A0`; no new tool or Provider operation is introduced in replay.

## Remaining uncertainty

All evidence is post-hoc and consumed-development. Generalization and live verifier behavior remain unmeasured. A future live development evaluation requires separate authorization and must not be represented by this replay.

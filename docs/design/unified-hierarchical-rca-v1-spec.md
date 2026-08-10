# Unified Hierarchical RCA v1 Specification

Version: `unified-hierarchical-rca-v1`
Selected option: `A0` / `STRONG_SINGLE_HIERARCHICAL`

## Typed input

The runtime accepts a benchmark-independent typed case projection: canonical entity layer, explicit hierarchy/service ancestry, typed fault ontology, Metrics Top-6 ranks and margin, propagation disposition, first-anomaly timing, causal source support, and evidence visibility. Benchmark or system identity is never a routing feature.

## Decision rule

Frozen strategy: `KEEP_INITIAL`. Root provenance is emitted for every decision. Fault ontology is preserved from the Strong Single initial diagnosis; root arbitration never rewrites the frozen fault phrase.

## Safety and cost

- No arbitrary root generation; outputs are the Initial root or a frozen candidate.
- No Provider construction or call is permitted in offline replay.
- Missing or insufficient evidence fails closed to the Initial root.
- Expected model calls: `1`.
- The exact runtime outcome must equal the frozen Phase G counterfactual for every consumed record.

## Evidence boundary

This specification is derived from consumed-development, post-hoc attribution. It is not external validation, primary inference, a release claim, or authorization for live evaluation.

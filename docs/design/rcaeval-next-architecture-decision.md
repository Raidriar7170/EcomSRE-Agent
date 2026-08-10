# RCAEval Next Architecture Decision

## Decision

`METRICS_ARBITRATION`

A deterministic metrics rule shows robust positive net rescue.

Exactly one option is selected. The rejected options remain useful hypotheses, not approved runtime work.

The selected M3 rule has preserved-candidate net rescue `8`, `6`, and `12`, with root damage `0`, `0`, and `0`. Candidate-5 clears the primary `rescue > damage`, `net >= 2`, `damage <= 2` gate; Candidate-3/4 clear the robustness gate.

## Decision order

1. Choose `METRICS_ARBITRATION` only when one frozen M-rule has positive, low-damage rescue across preserved candidates.
2. Otherwise choose `METRICS_PLUS_TRACE_VERIFICATION` only when Trace visibility clears the gate **and** the bounded projection contains genuine causal direction/propagation information.
3. Otherwise choose `COMMUNICATION_REPAIRED_CROSS_SOURCE_VERIFIER` only when missing message fields create at least four actionable cases, relaxed Fusion has positive net rescue, and the new verifier is not redundant with Initial.
4. Otherwise choose `STRONG_SINGLE_RECOMMENDED`.

## Why communication alone is insufficient

Candidate-5 omitted Metrics provenance, Gate reasons, and Initial rationale from its Logs pairwise input, so the communication diagnosis is real and testable. But the Initial already consumes the full bounded ArchitectureContext. A new cross-source verifier would therefore repeat the same bounded evidence unless it receives new causal structure. Current Trace summaries provide per-service anomaly summaries rather than caller/callee edges or error propagation. The decision consequently follows measured rescue/damage and redundancy, not Agent count.

Static communication repair eligibility is `False`; C4 redundancy is `True`. Trace support is `False` with genuine causal information `False`. The best relaxed Fusion net rescue is `1`. These fail the three alternative decision gates.

## Runtime shape and cost

- Multi-Agent root arbitration retained: **No**.
- Recommended roles: one Strong Single model proposal plus one deterministic Metrics root arbiter.
- Expected model calls: **1**.
- Implementation scope if separately authorized: root-only M3 arbitration with the Initial indicator frozen, typed provenance, and explicit damage monitoring.

## Non-authorization

This decision does not authorize Candidate-6, a new runtime Agent, any Provider call, candidate rerun, RE2-TT access, new data, release, merge, or PR #19 modification.

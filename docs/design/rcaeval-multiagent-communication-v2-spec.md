# RCAEval Multi-Agent Communication v2 Contract

## Status and scope

This is a post-hoc design contract over consumed OB/SS development evidence. It records what an evidence-aware communication boundary would have to preserve; it does not authorize a runtime change, Provider call, new Agent, evaluation, or release. The selected next architecture is `METRICS_ARBITRATION`.

## Current stage graph

`bounded evidence -> Strong Single Initial -> deterministic Gate -> deterministic Metrics alternative -> source-bound specialist -> deterministic Fusion -> deterministic Indicator -> final diagnosis`

The Strong Single Initial consumes the complete bounded `ArchitectureContext` (Metrics, Logs, and available Traces). Gate consumes the Initial diagnosis plus deterministically reconstructed Metrics rank/margin and source-conflict features. Candidate-5 Logs pairwise consumes only: incident, Initial identity, Metrics-alternative identity, Initial indicator, bounded Logs evidence, and exact visible Logs refs. The free Trace verifier remains source-only. Neither verifier receives evaluator truth.

## Unique responsibilities and visibility

### Strong Single Initial

Unique responsibility: produce the only model-authored initial root/indicator proposal and citations. It sees the incident and full bounded `ArchitectureContext`. It does not see evaluator truth, future Gate results, a named Metrics alternative, or arbitration outcomes.

### Deterministic Gate

Unique responsibility: decide direct return versus a bounded source route. It sees the Initial diagnosis, deterministic Metrics ranking/margin, evidence-support and source-conflict flags, and source availability. It has no Provider call and never sees evaluator truth.

### Deterministic Metrics candidate producer / arbiter

Unique responsibility: produce ranked service candidates with `DETERMINISTIC_METRICS` provenance and, under the selected M3 frontier, decide a root-only override. It sees locked Metrics inputs and the Initial identity/rank. It does not change the Initial indicator and does not consume Logs/Trace model prose.

### Source verifier (contract only; not selected)

Unique responsibility would be to compare exactly two provenance-labelled candidates within one declared source. It may cite only refs in its source-visible allowlist. It must not invent cross-source support, reinterpret Metrics provenance as Logs/Trace evidence, or see evaluator truth. Current evidence does not authorize adding this model call.

### Deterministic Fusion and Indicator resolution

Unique responsibility: enforce override preconditions and retain `KEEP_INITIAL` on inconclusive, unsupported, non-visible, conflicting, or source-mismatched output. It has no Provider call. Indicator resolution is separate from root arbitration.

## Proposed communication envelope

If a future verifier is authorized, its minimum typed envelope must carry:

1. both candidate identities and explicit provenance (`MODEL_INITIAL` versus `DETERMINISTIC_METRICS`);
2. Metrics alternative rank, score, normalized margin, and Initial rank;
3. Gate route, reason codes, and risk flags;
4. Initial confidence, explanation, and cited refs;
5. source-specific evidence plus an exact visible-ref allowlist;
6. an explicit source limitation saying which claims the verifier cannot adjudicate.

Evidence refs remain source-authoritative. Metrics provenance may justify *comparison* but cannot be cited as Logs or Trace evidence. Output must label each candidate `ROOT_CANDIDATE`, `PROPAGATED_SYMPTOM`, or `UNCERTAIN`; separate supporting and contradicting refs; and return `INITIAL`, `ALTERNATIVE`, or `INCONCLUSIVE`.

## Fusion rules

`KEEP_INITIAL` is the default. `OVERRIDE_INITIAL` requires: Gate instability, a deterministic Metrics alternative, explicit alternative preference, alternative root role, non-empty source-visible support, and either an Initial propagated-symptom role or source-visible contradiction. `INCONCLUSIVE`, missing provenance, non-visible refs, or source mismatch must keep the Initial. Indicator arbitration remains separate and keeps the Initial indicator in these root-only counterfactuals.

## Call graph and failure semantics

Expected semantic calls remain 1 for direct return, 2 for one-source verification, and 3 for both-source verification. A future implementation must not add a second general cross-source model call unless non-redundant causal information is proved. Terminal failures retain no semantic imputation. Sidecar hashes/accounting cannot be treated as request or response contents.

## Selected architecture

`METRICS_ARBITRATION` keeps one model call and adds a deterministic root-only M3 rule: Metrics Top-1, margin at least 0.25, and Initial rank absent or greater than 2. Candidate provenance is explicit and no new message is sent. The Initial indicator remains fixed. Multi-Agent root arbitration is not retained because source comparison and non-redundancy gates failed.

# RCA100 Metrics Arbitration v1 Protocol

Status: `FROZEN_EXTERNAL_HOLDOUT_PROTOCOL`

This protocol evaluates the PR #21 deterministic Metrics Arbitration M3 on the
official 103-incident RCA100 benchmark. Every case uses one Strong Single model
call to produce an Initial diagnosis and one deterministic, root-only M3
decision to produce the Final diagnosis. Specialist, Commander, Fusion, fallback
model, semantic retry, schema retry, result-driven retry, and case replacement
are forbidden.

The runtime model receives only the task alert, bounded Metrics, bounded Logs,
and bounded Traces. Events and full Alerts are excluded. Topology is used only
by deterministic code for exact entity canonicalization, explicit aliases, and
parent mapping; graph edges are not an additional model reasoning source.

## Frozen projections and M3

The alert anchor priority is structured task trigger, task-scoped alert time,
and finally the task-window midpoint. Metrics uses the complete task-visible
window: pre is before the anchor and post is at or after it. A series requires
at least three samples on each side. F0 is
`abs(post_mean-pre_mean)/max(abs(pre_mean),1e-9)`, and the best series defines
each entity score. Logs reuse deterministic pattern-count summaries; Traces
reuse deterministic duration-change plus status-count summaries. Each modality
is bounded to six evidence records.

M3 overrides only when the Initial entity is absent from the Metrics top six or
ranked outside the top two, the normalized top-one/top-two margin is at least 0.25, and
the Metrics top entity differs from Initial. No Metrics ranking is a typed KEEP.
M3 never changes the model-produced fault type. Override evidence is replaced
with legal Metrics evidence supporting the selected entity.

## One-shot execution

All 103 cases are admitted in a fixed seeded private schedule. Concurrency is
one and request starts are separated by at least five seconds. A semantic
operation receives at most one allowlisted, byte-identical transport retry.
The first case-stage HTTP 429 that remains terminal after that retry stops new
case admission. Other typed case failures remain in the fixed denominator.

The complete runtime and configuration are committed and pass CI before the
Provider is constructed. A synthetic non-case capacity request must return a
valid typed response with known usage in one attempt and no HTTP 429. Every run
attempt, Provider attempt, diagnosis, M3 decision, Final diagnosis, and terminal
is create-once and Git-external.

Before that capacity request, a separate synthetic RCA100-shaped non-case is
materialized under the private control root and executes the full Task →
projections → mock Strong Single → M3 → terminal path. The label-blind 721-file
case tree is freshly SHA-256 verified at freeze and preflight with the frozen
`SORTED_RELATIVE_PATH_NUL_SHA256_NEWLINE_V1` algorithm and digest
`aca130e350330000e0d9bc575606e3a5378178b6d7e0c2afb5cf13910596fea9`;
checking only the acquisition lock declaration is insufficient. The original
acquisition provenance digest `8ab512...` remains separately preserved.

## Isolation and scoring

Case labels are not materialized in the runtime source and are acquired in a
separate evaluator checkout only after all 103 terminals are locked. Provider
credentials are removed before acquisition and evaluation. Primary correctness
uses exact entity ID, explicit topology same-as identity, or—only when an ID is
unavailable—Unicode-NFC, trimmed, case-folded exact entity-name equality. Fault
type uses the same text normalization without synonyms or fuzzy matching.

The primary endpoint is the paired change in Root Entity Hit@1 from Initial to
Final over the fixed denominator of 103. Inference uses 10,000 paired bootstrap
replicates with seed 20260810, a 95% percentile interval, and an exact two-sided
McNemar sensitivity test. Superiority requires a positive point difference and
a strictly positive lower confidence bound. Localization and Identification
are descriptive secondary results. The official Process component and
composite are `OFFICIAL_COMPOSITE_NOT_AVAILABLE` because no label-blind,
pre-execution implementation of the checkpoint scorer was available.

Root Damage Rate is Damage divided by the number of Initial-correct cases, with
zero defined when that denominator is zero. Descriptive subgroup records are
frozen for fault category, exact normalized fault type, root entity domain/type,
alert entity type, M3 action, M3 applicability, exact Initial rank, Metrics
availability, and normalized-margin bins `NONE`, `[0.00,0.25)`, `[0.25,0.50)`,
and `[0.50,+inf)`. Every subgroup record carries its denominator and cannot
alter the primary inference.

After terminal and answer-key locks exist, both report construction and final
verification freshly recompute the input, terminal, run-attempt,
Provider-sidecar, and answer-key tree hashes. Final verification independently
recomputes case scores, paired statistics, aggregate sections, execution
integrity, and public-report content before create-once advancement to
`FINAL_REPORT_FROZEN`.

Only aggregate public artifacts are allowed. They exclude any per-case
identifier, prediction, answer, entity, case-linked fault phrase, reasoning, raw
evidence, private path, credential, or Provider endpoint. This one-shot result
must be reported whether it supports, weakly suggests, or does not support M3.

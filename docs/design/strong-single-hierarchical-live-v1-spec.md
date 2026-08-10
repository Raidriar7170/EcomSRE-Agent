# Strong Single Hierarchical Live v1 — Implementation Specification

## B0 model input

B0 receives the frozen Strong Single system prompt, the alert/prompt, the visible
entity catalog, up to six bounded records from each of Metrics, Logs, and Traces,
and source availability. It receives no H1 section.

## H1 model input

H1 receives byte-equivalent B0 raw evidence and the same output tool schema. Its only
additional system guidance is to distinguish symptom from root, respect hierarchy
and service ancestry, use propagation when available, avoid selecting an operation or
pod merely for the strongest metric, return one visible root-eligible entity, and
express the fault using the generic local-resource/network/dependency/propagation/
application/unknown ontology without inferring a benchmark label.

The additional user payload has these semantic entity-card columns:

1. `entity_ref`
2. `layer`
3. `service_ancestor_or_none`
4. `parent_ref_or_none`
5. `relation_to_alert`
6. `topology_distance_or_none`
7. `visible_sources`
8. `first_anomaly_source_or_none`

The v3 wire encoding preserves those fields through dictionaries, positional rows,
source bitmasks, and card-index relations. It is global and benchmark-independent.

## Shared output

Both arms call `submit_strong_single_diagnosis` with the same validation schema:

- `root_cause_entity_ref`
- `fault_type`
- `confidence`
- `evidence_refs`
- `reasoning_steps`
- `summary`

Every root and evidence reference is validated against the model-visible input. H1
additionally requires a root-eligible entity. The shared six-field model schema is
unchanged. Runtime deterministically appends entity layer, service ancestor, model
root provenance, fault-ontology class, and root visibility summary to each completed
private terminal; these are not model-generated fields.

## Runtime records

Each scheduled arm has a create-once run attempt and terminal. A completed terminal
contains the typed diagnosis, exactly one semantic operation, zero Specialist/Fusion
calls, Provider attempt/retry counts, known/conservative token accounting, input and
output tokens when known, request hash, latency, schedule/implementation bindings,
and deterministic diagnosis metadata. Typed failures remain in the
fixed denominator. Existing terminals are returned without new network I/O. A started
arm without a terminal is sealed `INTERRUPTED_NO_REISSUE`.

## Integrity and state chain

The private state chain is append-only:

`SCHEDULE_FROZEN → CONTEXT_AUDITED → IMPLEMENTATION_FROZEN → CI_ADMITTED → INPUTS_REVERIFIED →`
`PROVIDER_PREFLIGHT_PASSED → TUNE_EXECUTED → TUNE_TERMINALS_LOCKED →`
`GROUND_TRUTH_ACQUIRED_AFTER_TUNE_LOCK → TUNE_SCORED`.

If TUNE encounters a final HTTP 429, the separate terminal branch is
`PROVIDER_PREFLIGHT_PASSED → TUNE_ABORTED_HTTP429`. Its abort lock binds the partial
schedule prefix, terminal/run-attempt/Provider trees, implementation, inputs,
preflight, and execution summary. It does not acquire Ground Truth, score, publish a
model-quality result, or resume Provider admission.

On a passing scored TUNE it continues through candidate freeze and the one-shot
Regression. A scored TUNE Gate failure proceeds directly to canonical aggregate
publication; the HTTP429 abort branch does not. Public verification is the only
operation that creates `PUBLIC_RESULT_FROZEN`.

Protected implementation paths and all eight configuration hashes are bound to the
implementation commit. Provider admission requires the exact clean implementation
commit. After Provider execution, the exact public surface is three TUNE-only result
paths or five paths when Regression was scored; no stale optional Regression path is
allowed. Protected code/config hashes must still match.

## Evaluator boundary

Runtime OB/SS discovery produces only `TelemetryCase` records and selects consumed
cases from prior terminal `case_id` values; it never materializes `DevCase` or reads
label fields. The scorer is imported only by post-terminal-lock scoring commands. RCA100 uses the
frozen answer-envelope loader and exact entity/same-as/name matching. Service, layer,
ancestor, descendant, and downstream dimensions come from the frozen explicit
hierarchy. OB/SS compares normalized exact service and fault strings. No fuzzy,
embedding, synonym, or case-specific rescue is allowed.

## Canonical publication

Before either publication or verification, the canonical verifier validates every
state-to-lock link, source/answer/terminal/scoring hash, and independently re-runs the
frozen evaluator from raw locked terminals plus post-lock truth. It exact-compares
both aggregate and case-score vectors, then regenerates JSON, Markdown, and the
Chinese Human Brief, compares every byte, scans all outputs for identity/private/
credential markers, and binds the exact public hashes before the frozen state
advances.

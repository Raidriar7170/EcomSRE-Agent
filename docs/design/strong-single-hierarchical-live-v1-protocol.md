# Strong Single vs Strong Single Hierarchical Live Development Protocol v1

Status: pre-Provider frozen implementation protocol.

This protocol compares two independent one-call Strong Single arms on consumed
development data. It is not external validation and does not support an external
superiority claim.

## Question and boundaries

The only question is whether deterministic, label-blind hierarchy and propagation
context improves Strong Single diagnosis while preserving one model call. B0 is the
baseline Strong Single input. H1 adds a root-eligible entity index, canonical layer,
service ancestor, parent, alert relation, topology distance, exact source visibility,
first-anomaly source, and a bounded deterministic propagation summary.

Both arms use the same model, temperature, top-p, output schema, bounded Metrics,
Logs, and Traces evidence, evidence limits, and completion budget. Each arm performs
one independent semantic model operation. Specialist calls, Fusion calls, fallback
models, semantic retries, schema retries, and post-model overrides are forbidden.

Provider payloads contain no benchmark identity, source task/case identity, score,
Ground Truth root, or Ground Truth fault. Runtime modules do not import the
evaluator-only scorer. RCA100 answers and OB/SS labels are loaded only after the
TUNE terminal tree is locked. OB/SS TUNE/Regression membership is projected from
the prior consumed terminal records by opaque `case_id`; runtime case discovery is
ordinal and never parses the label-bearing directory names.

RE2-TT, new external data, A2 development, applicability gates, new architecture
frontiers, Multi-Agent candidates, and new harness frameworks are out of scope.

## Context construction

Root-eligible layers are SERVICE, WORKLOAD, NODE, DATABASE, CACHE, MESSAGE_QUEUE,
NETWORK_COMPONENT, CLUSTER, and INFRASTRUCTURE. OPERATION, POD, CONTAINER, and
UNKNOWN remain evidence nodes but are not root candidates by default.

The H1 entity index deterministically includes:

1. every entity in B0 bounded evidence;
2. the alert entity;
3. explicit parents and service ancestors;
4. root-eligible entities within graph distance two of an alert/evidence entity;
5. root-eligible entities with exact Metrics, Logs, Traces, Events, or Alerts
visibility.

Graph distance and `topology_distance_or_none` use only explicit parent and topology
adjacency. Trace parent-child and first-observed temporal edges are propagation-only:
they may appear in the bounded propagation summary but cannot create topology
neighbors or distance-based entity inclusion. A visible entity's explicit service
ancestor is included even when the source does not provide a corresponding parent
edge.

Ordering is direct evidence descending, source count descending, alert distance
ascending, root-eligible layer priority, and stable entity reference. The entity cap
is 64. Each entity card has exactly the eight protocol fields. Propagation is capped
at 12 relations and is limited to directed topology, trace parent-child, explicit
dependency, deterministic first-observed ordering, undirected, or unknown relations.

The model serialization uses one column header, dictionary-encoded entity/layer/
relation values, a fixed source-order bitmask, and card-index propagation references.
Nulls and all eight semantic card fields are preserved. This encoding was selected
before implementation freeze after append-only no-Provider audits; it does not change
the semantic context projection.

The authoritative append-only context audit is created from the final label-free
case projection and the final v3 dictionary/bitmask serializer. Four earlier
pre-freeze control generations remain preserved and are not execution-eligible:
v1 is `SUPERSEDED_PRE_PROVIDER_LABEL_BOUNDARY_REPAIR`, v2 is
`SUPERSEDED_PRE_IMPLEMENTATION_CONTEXT_AUDIT_SCHEMA_REPAIR`, v3 is
`SUPERSEDED_PRE_IMPLEMENTATION_PROVIDER_PAYLOAD_IDENTITY_REPAIR`, and v4 is
`SUPERSEDED_PRE_IMPLEMENTATION_CONTROL_GENERATION_REPAIR`. Each stopped before
Provider and Ground Truth access. The clean v5 control is the sole authoritative,
execution-eligible generation.

The final create-once v5 audit covers all 163 TUNE cases: B0/H1 valid contexts are
163/163; H1 entity count has mean 27.226993865030675 and maximum 53; mean
propagation relation count is 9.239263803680982 with maximum 12; truncation,
duplicate entity, and invalid reference counts are zero; maximum estimated H1 input
is 6304 tokens; and the estimated H1/B0 mean input ratio is 1.322520977196969. Its
aggregate SHA-256 is `04e2f4a42fc8aa0a5f16161e946f43cddce8bfdd829b013a1a661d22549835cd`
and its lock/state binding SHA-256 is
`efd0e2f7396e4d0e4d2c25a541315b19d1d2cb97956418632fbba1a1ba30e7cc`.
No Provider or Ground Truth was used in the audit.

## Schedule and Provider policy

TUNE contains 103 consumed RCA100 pairs and 60 consumed OB/SS TUNE pairs. Seed
20260812 randomizes case order. Odd pairs run B0 then H1; even pairs run H1 then B0.
There are 326 planned semantic operations. Regression, only if every TUNE Gate
passes, contains 120 consumed OB/SS pairs, seed 20260813, alternating order, and 240
planned semantic operations.

The model is `gpt-5.4-mini-2026-03-17`, temperature 0, top-p 1, concurrency 1,
minimum spacing 5 seconds, and maximum completion 2048 tokens. At most one
allowlisted byte-identical transport retry is permitted. The first final HTTP 429
stops new pair admission.

For Regression, any pairs after the first final HTTP 429 receive explicit
`NOT_ADMITTED_AFTER_HTTP429` fixed-denominator terminal dispositions. This stops new
admission without hard-coding the Regression verdict; the frozen Gate still permits
up to two HTTP 429 terminals and requires at least 114 completions per arm.

Before any case admission, the implementation commit and protected file/config
hashes must match, applicable Draft-PR CI must be successful, all three raw input
trees must be freshly rehashed, and one synthetic B0 plus one synthetic H1 request
must each return a valid typed response with known positive usage and no HTTP 429.

## Evaluation and one-shot rules

TUNE uses fixed denominators of 103 RCA100 and 60 OB/SS pairs. RCA100 reports exact
root, service-ancestor root, pair, layer mismatch, ancestor/descendant error, and
downstream symptom selection. OB/SS reports root service and root/fault pair.
Completion, Provider attempts/retries, prompt/completion tokens, latency, H1/B0 cost
ratios, 10,000 paired bootstrap replicates, and exact McNemar values are descriptive.

The exact TUNE and Regression Gates are machine-readable in the tracked gate files.
If any TUNE condition fails, the exact marker is
`HIERARCHICAL_STRONG_SINGLE_LIVE_TUNE_NOT_PASSED`; H1 is not modified or rerun and
Regression is forbidden. Only a passing TUNE may create the candidate lock. A failed
Regression uses `HIERARCHICAL_STRONG_SINGLE_REGRESSION_NOT_PASSED` and is not rerun.

Every attempt, terminal, schedule, input re-verification, implementation, CI,
preflight, scorer, and public projection is create-once and hash-bound outside Git.
Existing attempts and terminals are revalidated field-by-field against arm, pair,
opaque ID, schedule hash, and implementation lock before reuse or terminal lock. An
interrupted started arm is terminalized without reissuing its Provider request.

## Public claim boundary

Public results are aggregate-only. Before publication or freeze, the verifier checks
the complete state/lock chain, freshly rehashes source and answer trees, revalidates
raw terminal bindings, independently recomputes aggregate and case-score vectors,
and then regenerates every public byte. They contain no source/case/run identity,
case-level prediction or answer,
entity/fault per case, raw evidence, private path, Provider endpoint, or credential.
The live-evaluation PR remains unmerged under this protocol.

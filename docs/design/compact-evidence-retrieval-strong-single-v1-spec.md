# Compact Evidence-Retrieval Strong Single v1

## Status and boundary

This specification defines exactly one development candidate: `C1 / COMPACT_EVIDENCE_RETRIEVAL_STRONG_SINGLE`. A0 remains the engineering fallback. This work does not resume A2, H1, Specialist, Commander, Judge, Fusion, or multi-agent development.

The experiment is a consumed development evaluation, not external validation. It permits one offline admissibility audit and, only after that gate passes, one 163-pair live TUNE run. There is no candidate iteration, live Smoke, rerun, Regression, merge, release, or tag in this goal.

## Runtime architecture

Bounded label-free telemetry is projected into a deterministic candidate source. The retriever selects at most 12 root-eligible candidates. One Strong Single model call selects a strict `C01`–`C12` identifier. Runtime then maps that identifier to the private canonical entity reference.

Per arm:

- semantic model calls: 1
- Specialist calls: 0
- Fusion calls: 0
- post-hoc Metrics override: none
- freely generated C1 entity references: forbidden

## Frozen retrieval policy

Root-eligible layers are `SERVICE`, `WORKLOAD`, `NODE`, `DATABASE`, `CACHE`, `MESSAGE_QUEUE`, `NETWORK_COMPONENT`, `CLUSTER`, and `INFRASTRUCTURE`. `OPERATION`, `POD`, `CONTAINER`, and `UNKNOWN` remain evidence or relation nodes; only their explicit eligible ancestors may become candidates.

The six deterministic buckets are:

1. R1: direct root-eligible Metrics, Logs, Traces, Events, or Alerts evidence.
2. R2: explicit service, workload, or node ancestors of evidence entities.
3. R3: root-eligible upstream dependencies at directed distance at most two, using only directed topology, trace parent-to-child, or explicit dependency edges.
4. R4: root-eligible entities with deterministic earliest metric, error-log, failed/slow-span, or event anomaly.
5. R5: Metrics Top-6 mapped to the nearest root-eligible ancestor and deduplicated.
6. R6: the alert entity or its nearest root-eligible ancestor.

Slots are fixed at R1=4, R2/R3=3, R4=2, R5=2, R6=1. Unfilled slots are refilled in that same priority order. Ordering is source count descending, direct evidence count descending, first anomaly ascending with missing last, topology distance ascending with missing last, Metrics rank ascending with missing last, and canonical entity reference ascending. A service ancestor may contribute at most three cards; an entity layer may contribute at most six. No entity is invented when fewer candidates exist.

## Model-visible card and output

Cards expose the stable candidate ID, display label, layer, service ancestor, retrieval reasons, visible sources, Metrics rank and margin, first-anomaly offset, alert relation, and at most three visible evidence references. They do not expose the private entity mapping, benchmark identity, truth, correctness, or the allocation bucket.

The C1 output contains only `root_candidate_id`, `fault_type`, `confidence`, one to four visible and unique `evidence_refs`, and a summary of at most 400 characters. The runtime validates candidate membership and evidence visibility before deterministic ID-to-entity mapping. There is no reasoning array and no semantic or schema retry.

## Admissibility

The one Provider-free audit covers the consumed RCA100 103 cases and OB/SS TUNE 60 cases only. Admission requires RCA100 exact recall at least 60/103 and at least 15 above the frozen PR #24 model-visible exact count, RCA100 service recall at least 80/103, OB/SS service recall 60/60, no invalid refs or duplicate IDs, no more than 12 cards, and mean estimated C1/B0 input ratio at most 1.15.

If any check fails, the terminal disposition is `COMPACT_RETRIEVAL_ADMISSIBILITY_NOT_PASSED_KEEP_A0`; the retrieval policy and gate are not modified and no Provider request is made.

## Consumed audit disposition

The single admissibility audit completed with `COMPACT_RETRIEVAL_ADMISSIBILITY_NOT_PASSED_KEEP_A0`. RCA100 exact recall was 64/103 against the frozen legacy-visible 44/103, but RCA100 service recall was 68/103, OB/SS service recall was 58/60, and the mean estimated C1/B0 input ratio was 1.3914. The failed checks remain published as negative development evidence. No Provider preflight or live case was admitted.

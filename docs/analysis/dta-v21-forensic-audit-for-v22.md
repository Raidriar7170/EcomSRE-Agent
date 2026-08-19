# DTA v2.1 Forensic Audit for the v2.2 Successor

Status: `FROZEN_HISTORY_REVIEWED / PR_A_ONLY / NO_RERUN`

This audit records the code paths that motivate DTA v2.2. It does not modify,
rerun, relabel, or replace any DTA v2 or v2.1 result. The public held-out result
remains `DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED`; the engineering
terminal remains
`DTA_V21_P0_ENGINEERING_CLOSEOUT_WITH_FROZEN_AGENT_CAPABILITY_LIMITATIONS`.

The linked private-evidence summary contains aggregate counts only. No raw
Provider response, per-case mapping, rationale, private path, or credential is
published.

## Frozen empirical facts

- Held-out schedule: 8 cases x 3 arms = 24 scored entries.
- Evidence-Guided Planner protocol acceptance: 2/8 (25%).
- Flat Adaptive protocol acceptance: 7/8 (87.5%).
- One-shot Full Context protocol acceptance: 4/8 (50%).
- Planner mechanism accuracy and semantic evidence validity: 0%.
- One-shot role in v2.1 was materially advantaged by evaluator-selected
  `full_context_tools`; v2.2 classifies it as an oracle-context anchor.
- Compact/no-compaction development evidence did not establish token savings.
- Live No-Fault produced a false-positive Diagnosis but safe `NO_ACTION`.
- Live Ad CPU stopped on `DUPLICATE_READ_REQUEST` before Diagnosis or action.

The aggregate private failure taxonomy is
[`dta-v21-private-failure-taxonomy-summary.json`](dta-v21-private-failure-taxonomy-summary.json).

## Protocol and schema findings

| ID | Finding | Code-path evidence | v2.2 disposition |
|---|---|---|---|
| V21-P01 | The first read requires an ACTIVE fault hypothesis; No-Incident is not a hypothesis. | `src/ecomsre/dta_v2/v21/planner_contracts.py` — `DiagnosticHypothesisV21`, `EvidencePlanDecisionV21.require_plan_semantics` | Runtime creates a closed hypothesis catalog including `NO_INCIDENT` and `UNRESOLVED`. |
| V21-P02 | Every turn rewrites the complete hypothesis table, evidence refs, gaps, and terminal shape. | `planner_contracts.py` — `EvidencePlanDecisionV21` | Model emits one lightweight `ControllerDecisionV22`; runtime owns the ledger. |
| V21-P03 | Run ID, turn ordinal, canonical order, union, digest, and budgets are model-facing invariants. | `planner_contracts.py` — fields and `require_plan_semantics`; `agent.py` — planner loop | Runtime injects mechanical state and hashes. |
| V21-P04 | Gaps identify only a source, not a source-target-question capability. | `planner_contracts.py` — `unresolved_evidence_sources` and `evidence_gap_sources` | Action IDs bind source, targets, canonical query, coverage, and cost. |
| V21-P05 | Belief status is only `ACTIVE` or `REJECTED`. | `planner_contracts.py` — `HypothesisStatusV21` | Runtime derives `UNTESTED`, `PARTIALLY_SUPPORTED`, `SUPPORTED`, and `CONTRADICTED`. |
| V21-P06 | `bounded_rationale` is required and hashed but is not durable next-turn state. | `planner_contracts.py`; `context_projection.py` state models | Remove rationale from the controller contract; retain typed evidence and ledger state. |
| V21-P07 | Benign output/planning errors terminate a run without a safe correction. | `agent.py` — planner validation failure handling | Permit one no-tool correction for enumerated decision-shape errors. |
| V21-P08 | Duplicate prevention relies on prior hashes plus a terminal validator. | `prompts.py`; `context_projection.py`; `planner.py` — `validate_planner_read_request_v21` | Remove executed/dominated actions from the next catalog. |
| V21-S01 | Provider structured output uses `strict: False` while local semantic validation is strict. | `agent_provider.py` — `_response_format` and parse/validation paths | Probe strict capability; freeze one shared mode before evaluation. |
| V21-S02 | Planner and Flat use different output schemas and error surfaces. | `agent_provider.py`; `agent.py` arm-specific paths | All adaptive arms use `ControllerDecisionV22`. |
| V21-S03 | Models generate fixture-sensitive query parameters. | `agent_provider.py` read request models; `evaluation_replay.py` exact filtering | Models select only canonical `action_id`; resolver owns parameters. |
| V21-S04 | Only exact normalized duplicates are rejected; semantic dominance is absent. | `planner.py`; `agent.py` duplicate checks | `ActionCoverageV22` masks executed and dominated actions. |

## Context and memory findings

| ID | Finding | Code-path evidence | v2.2 disposition |
|---|---|---|---|
| V21-C01 | Compact Context is a typed projection, not a conversation summary. | `context_projection.py` — `CompactInvestigationStateV21` | Name the successor `SalientEvidenceMemoryV22` and evaluate it as a representation. |
| V21-C02 | Old log facts keep severity but omit message template/error/downstream semantics. | `context_projection.py` — `_fact(DiagnosticLogRecord)` | Preserve normalized template, typed category, downstream, and count. |
| V21-C03 | Trace facts omit operation and causal path. | `context_projection.py` — `_fact(TraceNeighborhoodRecord)` | Preserve operation, path, parent-child edge, first error, and relative latency. |
| V21-C04 | Metric `value` and `sample_count` are encoded together without an unsupported state. | `context_projection.py` — `_fact(MetricRecord)` | `sample_count=0` produces `UNSUPPORTED`, never a normal value predicate. |
| V21-C05 | Only `newest_observation` remains full in Compact mode. | `context_projection.py`; `agent.py` — `newest` loop state | Retain predicates, refs, Top-K facts, and loss metadata across all reads. |
| V21-C06 | No baseline-relative anomaly feature is stored. | `context_projection.py` fact models | Add versioned delta/ratio/z-score and support strength. |
| V21-C07 | Full versus Compact changes both representation and end-to-end trajectories. | `context_projection.py` compact/no-compaction builders; `agent.py` | Add fixed-trajectory serialization and a visible 2x2 behavioral factorial. |
| V21-C08 | Four-read horizon makes schema overhead large relative to compaction benefit. | `agent.py` read budget; public ablation report | Keep the claim bounded and require preregistered memory gates. |

## Replay and tool findings

| ID | Finding | Code-path evidence | v2.2 disposition |
|---|---|---|---|
| V21-T01 | Fixtures are indexed by tool, then filtered incompletely. | `src/ecomsre/dta_v2/v21/evaluation_replay.py` — `ReplayCaseReadBackendV21.__init__` | Keep complete captures but make every canonical query deterministically query-specific. |
| V21-T02 | A trace target present anywhere causes the entire fixture to be re-anchored. | `evaluation_replay.py` — `TraceNeighborhoodRequest` branch | Build a bounded connected neighborhood without rewriting anchors. |
| V21-T03 | Trace `service_scope` can include the complete path and over-admit roots. | `context_projection.py` — `_service_scope`; `candidate_filter.py` | Predicates bind first-error/edge semantics and exact target. |
| V21-T04 | Successful empty logs are returned as `SOURCE_UNAVAILABLE`. | `evaluation_replay.py` — `SearchLogsRequest` branch | Distinguish `SUCCESS_EMPTY` from source failure. |
| V21-T05 | Unsupported metric samples can appear as numeric zero facts. | `context_projection.py` — metric facts | Use explicit `support_status`. |
| V21-T06 | Static source fixtures reduce planning value and make broad reads cheap. | `evaluation_replay.py`; evaluation case `full_context_tools` | Require planning-required cases and weighted minimal-path regret. |

## Diagnosis, evidence, and action findings

| ID | Finding | Code-path evidence | v2.2 disposition |
|---|---|---|---|
| V21-D01 | `UNKNOWN` can appear in a completed fault Diagnosis. | `contracts.py` fault ontology and terminal validation | Terminals are `DIAGNOSED`, `NO_INCIDENT`, `ABSTAIN`, `FAILED`; no actionable UNKNOWN. |
| V21-D02 | Evidence validity checks refs/sources/targets, not mechanism support. | `evaluation_contracts.py` — `build_evaluation_score_v21` | Deterministic semantic predicates and clause admission. |
| V21-D03 | Candidate filtering accepts source coverage plus broad service scope. | `candidate_filter.py` | Require resolved predicates, an accepted clause, registry, and exact target. |
| V21-D04 | Required evidence is one fixed source set. | `contracts.py` Runbook requirements; `evaluation_contracts.py` expected sources | Use versioned alternative DNF-style clauses. |
| V21-D05 | No-Fault lacks explicit broad negative coverage admission. | `evaluation_contracts.py` No-Fault scoring; planner hypothesis model | Require all-candidate bootstrap coverage, health, sample support, and no strong anomaly. |
| V21-D06 | Action Selection receives resolved reference metadata, so semantic safety must be earlier. | `agent_contracts.py`; `agent.py` candidate view | Keep Action Selection candidate-bound and place semantic admission before it. |

## Evaluation and scoring findings

| ID | Finding | Code-path evidence | v2.2 disposition |
|---|---|---|---|
| V21-E01 | Primary arms change controller, schema, memory, and failure surface together. | `evaluation_agents.py`; arm-specific `agent.py` paths | Shared schema/bootstrap/catalog/memory/budget/admission; ledger view is the primary difference. |
| V21-E02 | One-shot tools are selected by evaluator metadata. | `evaluation_contracts.py` — `full_context_tools`; `evaluation_agents.py` materialization | Label `ORACLE_CONTEXT_UPPER_BOUND`; tool selection is N/A. |
| V21-E03 | `action_precision` can be true when both expected disposition and Runbook are null. | `evaluation_contracts.py` — `build_evaluation_score_v21` | Score actions only on an applicable denominator. |
| V21-E04 | Expected source inclusion is treated as evidence validity. | `evaluation_contracts.py` | Score predicate support and clause satisfaction separately. |
| V21-E05 | Required arm-by-slice cross-tabs are absent. | `evaluation_campaign.py` aggregate construction | Publish arm x family/mechanism/slice/planning/protocol-code tables. |
| V21-E06 | Eight held-out cases give 12.5-point resolution. | public held-out report | Use 24 development and 24 held-out cases, still claiming only a bounded portfolio. |
| V21-E07 | One sealed run does not measure Provider stability. | held-out schedule/report | Measure repeated visible-development stability; preserve one held-out execution. |
| V21-E08 | Protocol and semantic failure are not separated enough. | `EvaluationScoreV21` and aggregates | Publish first-pass, corrected, end-to-end, and conditional semantic diagnostics. |
| V21-E09 | Costs are not normalized per correct outcome. | `evaluation_campaign.py` aggregates | Add tokens/latency/evidence cost per correct; zero-correct is infinity/not estimable. |
| V21-E10 | No minimal sufficient path or planning regret is scored. | scorer contracts | Derive minimal path from frozen clauses, availability, and costs. |
| V21-E11 | No stage-wise oracle separates policy, diagnosis, CandidateSet, and action. | end-to-end campaign/scorer | Add stage-wise oracle metrics without replacing end-to-end success. |
| V21-E12 | Ad CPU business SLI is not a valid required support predicate. | live CPU protocol and capability closeout | CPU uses resource-only support plus healthy runtime; business SLI is a guardrail. |

## Audit conclusion

The v2.1 result does not reject evidence planning in general. It rejects the
preregistered advantage claim for the frozen v2.1 system and exposes combined
protocol, representation, replay, semantic-admission, model, and scoring
limitations. DTA v2.2 therefore changes the interface and experimental design
before considering training. Negative v2.2 empirical results remain valid
engineering completion and must not trigger retry-until-pass.

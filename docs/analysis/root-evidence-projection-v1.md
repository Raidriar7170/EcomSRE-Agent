# Root Evidence Projection v1

Classification: `CONSUMED_CROSS_BENCHMARK_DEVELOPMENT`, `DETERMINISTIC_RETRIEVAL_DEVELOPMENT`, `NOT_EXTERNAL_VALIDATION`, `NOT_PRIMARY_INFERENCE`.

This is the single frozen, no-Provider projection/index pass. PR #27 remains the frozen negative compact-retrieval reference (exact 64/103; service 68/103).

Verdict: `ROOT_EVIDENCE_PROJECTION_GATE_NOT_PASSED_STOP_LLM_RCA_OPTIMIZATION`.

```json
{
  "candidate_index_policy": "COMPACT_ROOT_CANDIDATE_INDEX_V1",
  "claim_boundary": {
    "external_claim": false,
    "live_evaluation": false,
    "policy_reruns": 0,
    "provider_calls": 0,
    "re2_tt_access": false,
    "regression_access": false
  },
  "classification": [
    "CONSUMED_CROSS_BENCHMARK_DEVELOPMENT",
    "DETERMINISTIC_RETRIEVAL_DEVELOPMENT",
    "NOT_EXTERNAL_VALIDATION",
    "NOT_PRIMARY_INFERENCE"
  ],
  "frozen_reference": {
    "pr27_exact_recall": 64,
    "pr27_service_recall": 68
  },
  "gate": {
    "checks": {
      "benchmark_id_branches_zero": true,
      "duplicate_candidate_ids_zero": true,
      "duplicate_canonical_candidates_zero": true,
      "ground_truth_dependent_branches_zero": true,
      "invalid_evidence_refs_zero": true,
      "max_candidates_at_most_12": true,
      "max_token_ratio_at_most_1_25": true,
      "mean_token_ratio_at_most_1_15": true,
      "median_token_ratio_at_most_1_15": true,
      "obss_index_60_of_60": true,
      "obss_projection_60_of_60": true,
      "p95_token_ratio_at_most_1_20": true,
      "rca100_exact_improvement_at_least_11": true,
      "rca100_index_exact_at_least_75": true,
      "rca100_index_service_at_least_90": false,
      "rca100_median_exact_rank_at_most_3": true,
      "rca100_projection_exact_at_least_85": true,
      "rca100_projection_service_at_least_95": true,
      "rca100_service_improvement_at_least_22": false
    },
    "passed": false,
    "verdict": "ROOT_EVIDENCE_PROJECTION_GATE_NOT_PASSED_STOP_LLM_RCA_OPTIMIZATION"
  },
  "missing_cause_aggregate": {
    "TOP12_ORDERING_DROPPED": 24
  },
  "obss": {
    "denominator": 60,
    "index_recall_at_12": 60,
    "missing": 0,
    "projection_service_coverage": 60
  },
  "projection_policy": "CANONICAL_ROOT_EVIDENCE_PROJECTION_V1",
  "rca100": {
    "denominator": 103,
    "exact_improvement_vs_pr27": 15,
    "index_exact_recall_at_12": 79,
    "index_service_recall_at_12": 79,
    "median_exact_rank": 3,
    "missing_exact": 24,
    "missing_service": 24,
    "projection_exact_coverage": 103,
    "projection_service_coverage": 103,
    "service_improvement_vs_pr27": 11
  },
  "schema_version": "root-evidence-projection.aggregate.v1",
  "structural_integrity": {
    "benchmark_id_branches": 0,
    "duplicate_candidate_ids": 0,
    "duplicate_canonical_candidates": 0,
    "ground_truth_dependent_branches": 0,
    "invalid_evidence_refs": 0,
    "max_candidate_count": 12
  },
  "token_accounting": {
    "b0_mean": 3260.6319018404906,
    "c1_mean": 3519.472392638037,
    "per_system_ratio_mean": {
      "obss": 1.1132119669559253,
      "rca100": 1.0686264230761167
    },
    "ratio_max": 1.1249389946315276,
    "ratio_mean": 1.085038279718991,
    "ratio_median": 1.0746697300402068,
    "ratio_p95": 1.1238509917755202,
    "serialization": "SORTED_UTF8_CANONICAL_FULL_REQUEST_JSON",
    "tokenizer": "tiktoken o200k_base"
  }
}
```

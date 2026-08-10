# Strong Single vs Strong Single Hierarchical — TUNE

This is a consumed development evaluation, not external validation.
Each arm used one independent model call with alternating pair order, the 
same model/output schema/raw bounded evidence, and zero override, Specialist, 
or Fusion calls.

## Frozen result

- Verdict: `"HIERARCHICAL_STRONG_SINGLE_LIVE_TUNE_NOT_PASSED"`
- RCA100 aggregate: `{"ancestor_error_delta": 0, "b0_ancestor_error": 0, "b0_descendant_error": 4, "b0_downstream_symptom_selection": 18, "b0_fault_correct": 0, "b0_pair_correct": 0, "b0_root_correct": 22, "b0_service_root_correct": 26, "denominator": 103, "descendant_error_delta": -4, "downstream_symptom_selection_delta": 11, "entity_layer_mismatch_delta": -13, "h1_ancestor_error": 0, "h1_descendant_error": 0, "h1_downstream_symptom_selection": 29, "h1_fault_correct": 0, "h1_pair_correct": 0, "h1_root_correct": 21, "h1_service_root_correct": 21, "pair_damage": 0, "pair_net_rescue": 0, "pair_rescue": 0, "root_damage": 7, "root_net_rescue": -1, "root_rescue": 6, "service_root_damage": 10, "service_root_net_rescue": -5, "service_root_rescue": 5}`
- OB/SS aggregate: `{"ancestor_error_delta": 0, "b0_ancestor_error": 0, "b0_descendant_error": 0, "b0_downstream_symptom_selection": 0, "b0_fault_correct": 0, "b0_pair_correct": 0, "b0_root_correct": 37, "b0_service_root_correct": 37, "denominator": 60, "descendant_error_delta": 0, "downstream_symptom_selection_delta": 0, "entity_layer_mismatch_delta": 2, "h1_ancestor_error": 0, "h1_descendant_error": 0, "h1_downstream_symptom_selection": 0, "h1_fault_correct": 0, "h1_pair_correct": 0, "h1_root_correct": 30, "h1_service_root_correct": 30, "pair_damage": 0, "pair_net_rescue": 0, "pair_rescue": 0, "root_damage": 8, "root_net_rescue": -7, "root_rescue": 1, "service_root_damage": 8, "service_root_net_rescue": -7, "service_root_rescue": 1}`
- Combined aggregate: `{"ancestor_error_delta": 0, "b0_ancestor_error": 0, "b0_descendant_error": 4, "b0_downstream_symptom_selection": 18, "b0_fault_correct": 0, "b0_pair_correct": 0, "b0_root_correct": 59, "b0_service_root_correct": 63, "denominator": 163, "descendant_error_delta": -4, "downstream_symptom_selection_delta": 11, "entity_layer_mismatch_delta": -11, "h1_ancestor_error": 0, "h1_descendant_error": 0, "h1_downstream_symptom_selection": 29, "h1_fault_correct": 0, "h1_pair_correct": 0, "h1_root_correct": 51, "h1_service_root_correct": 51, "pair_damage": 0, "pair_net_rescue": 0, "pair_rescue": 0, "root_damage": 15, "root_net_rescue": -8, "root_rescue": 7, "service_root_damage": 18, "service_root_net_rescue": -12, "service_root_rescue": 6}`
- Cost: `{"b0": {"completed": 163, "known_usage": 163, "mean_input_tokens": 3103.2638036809817, "mean_latency_seconds": 4.286324704005926, "mean_output_tokens": 466.55214723926383}, "h1": {"completed": 129, "known_usage": 129, "mean_input_tokens": 4374.527131782946, "mean_latency_seconds": 4.55215007425358, "mean_output_tokens": 467.4573643410853}, "h1_to_b0_input_token_ratio": 1.4096536448477361, "h1_to_b0_latency_ratio": 1.0620170865728438}`
- Execution: `{"fusion_calls": 0, "http_429": 0, "obss_completed_b0": 60, "obss_completed_h1": 58, "provider_attempts": 326, "rca100_completed_b0": 103, "rca100_completed_h1": 71, "schema_privacy_schedule_failure": 34, "semantic_model_operations": 326, "specialist_calls": 0, "status_counts": {"COMPLETED": 292, "INPUT_PROJECTION_FAILURE": 0, "INTERRUPTED": 0, "INVALID_SCHEMA": 34, "NOT_ADMITTED": 0, "PRIVACY_FAILURE": 0, "PROTOCOL_VIOLATION": 0, "PROVIDER_FAILURE": 0, "TIMEOUT": 0}, "terminal_count": 326, "transport_retries": 0}`
- Descriptive paired inference: `{"bootstrap_replicates": 10000, "bootstrap_seed": 20260812, "ci_lower": -0.10429447852760736, "ci_upper": 0.006134969325153374, "denominator": 163, "mcnemar_exact_p_value": 0.13380050659179688, "point_difference": -0.049079754601226995}`

## Claim boundary

No case-level identity, prediction, answer, entity, raw evidence, private 
path, Provider endpoint, or credential is published. RE2-TT and new external 
data were not accessed. This result does not establish external superiority.

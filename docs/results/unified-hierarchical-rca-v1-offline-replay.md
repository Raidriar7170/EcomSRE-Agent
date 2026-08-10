# Unified Hierarchical RCA v1 Offline Replay

Classification: `CONSUMED_CROSS_BENCHMARK_DEVELOPMENT, POST_HOC_ARCHITECTURE_ATTRIBUTION, NOT_EXTERNAL_VALIDATION, NOT_PRIMARY_INFERENCE`.

This is aggregate-only, consumed-development, post-hoc evidence. It does not revise the frozen benchmark results and is not external validation.

## Canonical aggregate

```json
{
  "arbitration": "NO_OVERRIDE",
  "classification": [
    "CONSUMED_CROSS_BENCHMARK_DEVELOPMENT",
    "POST_HOC_ARCHITECTURE_ATTRIBUTION",
    "NOT_EXTERNAL_VALIDATION",
    "NOT_PRIMARY_INFERENCE"
  ],
  "communication_envelope": "NOT_APPLICABLE_A0",
  "correction_disclosure": {
    "original_invalid_attempt_preserved": true,
    "status": "CORRECTED_V3_APPEND_ONLY_SUCCESSOR",
    "supersedes": "CORRECTED_V2_GOAL_COVERAGE_INCOMPLETE",
    "thresholds_changed": false
  },
  "datasets": {
    "RCA100": {
      "denominator": 103,
      "exact_final": {
        "denominator": 103,
        "numerator": 16,
        "value": 0.1553398058252427
      },
      "exact_initial": {
        "denominator": 103,
        "numerator": 16,
        "value": 0.1553398058252427
      },
      "root_damage": {
        "denominator": 16,
        "numerator": 0,
        "value": 0.0
      },
      "root_net_rescue": 0,
      "root_rescue": {
        "denominator": 87,
        "numerator": 0,
        "value": 0.0
      }
    },
    "candidate-3": {
      "denominator": 60,
      "exact_final": {
        "denominator": 60,
        "numerator": 49,
        "value": 0.8166666666666667
      },
      "exact_initial": {
        "denominator": 60,
        "numerator": 49,
        "value": 0.8166666666666667
      },
      "root_damage": {
        "denominator": 49,
        "numerator": 0,
        "value": 0.0
      },
      "root_net_rescue": 0,
      "root_rescue": {
        "denominator": 11,
        "numerator": 0,
        "value": 0.0
      }
    },
    "candidate-4": {
      "denominator": 60,
      "exact_final": {
        "denominator": 60,
        "numerator": 51,
        "value": 0.85
      },
      "exact_initial": {
        "denominator": 60,
        "numerator": 51,
        "value": 0.85
      },
      "root_damage": {
        "denominator": 51,
        "numerator": 0,
        "value": 0.0
      },
      "root_net_rescue": 0,
      "root_rescue": {
        "denominator": 9,
        "numerator": 0,
        "value": 0.0
      }
    },
    "candidate-5": {
      "denominator": 60,
      "exact_final": {
        "denominator": 60,
        "numerator": 45,
        "value": 0.75
      },
      "exact_initial": {
        "denominator": 60,
        "numerator": 45,
        "value": 0.75
      },
      "root_damage": {
        "denominator": 45,
        "numerator": 0,
        "value": 0.0
      },
      "root_net_rescue": 0,
      "root_rescue": {
        "denominator": 15,
        "numerator": 0,
        "value": 0.0
      }
    },
    "pr21-regression": {
      "denominator": 120,
      "exact_final": {
        "denominator": 120,
        "numerator": 95,
        "value": 0.7916666666666666
      },
      "exact_initial": {
        "denominator": 120,
        "numerator": 95,
        "value": 0.7916666666666666
      },
      "root_damage": {
        "denominator": 95,
        "numerator": 0,
        "value": 0.0
      },
      "root_net_rescue": 0,
      "root_rescue": {
        "denominator": 25,
        "numerator": 0,
        "value": 0.0
      }
    },
    "pr21-tune": {
      "denominator": 60,
      "exact_final": {
        "denominator": 60,
        "numerator": 51,
        "value": 0.85
      },
      "exact_initial": {
        "denominator": 60,
        "numerator": 51,
        "value": 0.85
      },
      "root_damage": {
        "denominator": 51,
        "numerator": 0,
        "value": 0.0
      },
      "root_net_rescue": 0,
      "root_rescue": {
        "denominator": 9,
        "numerator": 0,
        "value": 0.0
      }
    }
  },
  "decision_name": "STRONG_SINGLE_HIERARCHICAL",
  "evaluation_version": "unified-hierarchical-rca-v1",
  "exact_match": {
    "denominator": 463,
    "numerator": 463,
    "value": 1.0
  },
  "fault_ontology": "TYPED_DETERMINISTIC",
  "fusion": "KEEP_INITIAL",
  "implementation_counterfactual_exact_match": true,
  "new_external_data_accessed": false,
  "provider_calls": 0,
  "provider_objects_constructed": 0,
  "re2_tt_accessed": false,
  "root_provenance": "MODEL_INITIAL",
  "schema_version": "unified-hierarchical-rca-v1.offline-replay.v1",
  "selected_option": "A0",
  "semantic_operations": 0
}
```

# RCA100 Entity Hierarchy Attribution

Classification: `CONSUMED_CROSS_BENCHMARK_DEVELOPMENT, POST_HOC_ARCHITECTURE_ATTRIBUTION, NOT_EXTERNAL_VALIDATION, NOT_PRIMARY_INFERENCE`.

This is aggregate-only, consumed-development, post-hoc evidence. It does not revise the frozen benchmark results and is not external validation.

## Canonical aggregate

```json
{
  "classification": [
    "CONSUMED_CROSS_BENCHMARK_DEVELOPMENT",
    "POST_HOC_ARCHITECTURE_ATTRIBUTION",
    "NOT_EXTERNAL_VALIDATION",
    "NOT_PRIMARY_INFERENCE"
  ],
  "correction_disclosure": {
    "original_invalid_attempt_preserved": true,
    "status": "CORRECTED_V3_APPEND_ONLY_SUCCESSOR",
    "supersedes": "CORRECTED_V2_GOAL_COVERAGE_INCOMPLETE",
    "thresholds_changed": false
  },
  "denominator": 103,
  "error_relations": {
    "historical_m3_damage": {
      "PREDICTED_DESCENDANT": {
        "denominator": 6,
        "numerator": 2,
        "value": 0.3333333333333333
      },
      "SAME_COMPONENT_UNDIRECTED": {
        "denominator": 6,
        "numerator": 4,
        "value": 0.6666666666666666
      }
    },
    "historical_m3_overrides": {
      "PREDICTED_DESCENDANT": {
        "denominator": 36,
        "numerator": 3,
        "value": 0.08333333333333333
      },
      "SAME_COMPONENT_UNDIRECTED": {
        "denominator": 36,
        "numerator": 33,
        "value": 0.9166666666666666
      }
    },
    "historical_m3_wrong_to_wrong": {
      "PREDICTED_DESCENDANT": {
        "denominator": 30,
        "numerator": 1,
        "value": 0.03333333333333333
      },
      "SAME_COMPONENT_UNDIRECTED": {
        "denominator": 30,
        "numerator": 29,
        "value": 0.9666666666666667
      }
    },
    "initial_wrong": {
      "CONNECTED_DOWNSTREAM": {
        "denominator": 87,
        "numerator": 15,
        "value": 0.1724137931034483
      },
      "CONNECTED_UPSTREAM": {
        "denominator": 87,
        "numerator": 2,
        "value": 0.022988505747126436
      },
      "PREDICTED_DESCENDANT": {
        "denominator": 87,
        "numerator": 8,
        "value": 0.09195402298850575
      },
      "SAME_COMPONENT_UNDIRECTED": {
        "denominator": 87,
        "numerator": 57,
        "value": 0.6551724137931034
      },
      "UNRELATED": {
        "denominator": 87,
        "numerator": 1,
        "value": 0.011494252873563218
      },
      "UNRESOLVED": {
        "denominator": 87,
        "numerator": 4,
        "value": 0.04597701149425287
      }
    }
  },
  "focus_denominators": {
    "historical_m3_damage": 6,
    "historical_m3_overrides": 36,
    "historical_m3_wrong_to_wrong": 30,
    "initial_wrong": 87
  },
  "granularity_mismatch_contribution": {
    "denominator": 87,
    "numerator": 8,
    "value": 0.09195402298850575
  },
  "metrics_top1_layer_distribution": {
    "OPERATION": {
      "denominator": 103,
      "numerator": 47,
      "value": 0.4563106796116505
    },
    "SERVICE": {
      "denominator": 103,
      "numerator": 25,
      "value": 0.24271844660194175
    },
    "UNKNOWN": {
      "denominator": 103,
      "numerator": 31,
      "value": 0.30097087378640774
    }
  },
  "multi_level_accuracy": {
    "exact": {
      "historical_m3": {
        "denominator": 103,
        "numerator": 10,
        "value": 0.0970873786407767
      },
      "initial": {
        "denominator": 103,
        "numerator": 16,
        "value": 0.1553398058252427
      },
      "metrics_top1": {
        "denominator": 103,
        "numerator": 7,
        "value": 0.06796116504854369
      }
    },
    "same_node": {
      "historical_m3": {
        "denominator": 103,
        "numerator": 0,
        "value": 0.0
      },
      "initial": {
        "denominator": 103,
        "numerator": 0,
        "value": 0.0
      },
      "metrics_top1": {
        "denominator": 103,
        "numerator": 0,
        "value": 0.0
      }
    },
    "same_topology_component": {
      "historical_m3": {
        "denominator": 103,
        "numerator": 99,
        "value": 0.9611650485436893
      },
      "initial": {
        "denominator": 103,
        "numerator": 98,
        "value": 0.9514563106796117
      },
      "metrics_top1": {
        "denominator": 103,
        "numerator": 95,
        "value": 0.9223300970873787
      }
    },
    "same_workload": {
      "historical_m3": {
        "denominator": 103,
        "numerator": 0,
        "value": 0.0
      },
      "initial": {
        "denominator": 103,
        "numerator": 0,
        "value": 0.0
      },
      "metrics_top1": {
        "denominator": 103,
        "numerator": 0,
        "value": 0.0
      }
    },
    "service": {
      "historical_m3": {
        "denominator": 103,
        "numerator": 17,
        "value": 0.1650485436893204
      },
      "initial": {
        "denominator": 103,
        "numerator": 23,
        "value": 0.22330097087378642
      },
      "metrics_top1": {
        "denominator": 103,
        "numerator": 17,
        "value": 0.1650485436893204
      }
    }
  },
  "schema_version": "rca100-entity-hierarchy-attribution.v1",
  "target_layer_distribution": {
    "NODE": {
      "denominator": 103,
      "numerator": 15,
      "value": 0.14563106796116504
    },
    "SERVICE": {
      "denominator": 103,
      "numerator": 87,
      "value": 0.8446601941747572
    },
    "WORKLOAD": {
      "denominator": 103,
      "numerator": 1,
      "value": 0.009708737864077669
    }
  },
  "topology_depth": {
    "denominator": 103,
    "maximum": 2,
    "mean": 1.145631067961165,
    "minimum": 1
  }
}
```

# RCA Cross-Benchmark Architecture Frontier

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
  "consumed_denominators": {
    "candidate_3": 60,
    "candidate_4": 60,
    "candidate_5": 60,
    "pr21_regression": 120,
    "pr21_tune": 60,
    "rca100": 103
  },
  "correction_disclosure": {
    "original_invalid_attempt_preserved": true,
    "status": "CORRECTED_V3_APPEND_ONLY_SUCCESSOR",
    "supersedes": "CORRECTED_V2_GOAL_COVERAGE_INCOMPLETE",
    "thresholds_changed": false
  },
  "grouped_robustness": [
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F001",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F002",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 460,
      "held_out_group": "F004",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F005",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 457,
      "held_out_group": "F006",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 451,
      "held_out_group": "F007",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F009",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 458,
      "held_out_group": "F010",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F011",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F012",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 452,
      "held_out_group": "F014",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 452,
      "held_out_group": "F016",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F018",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F020",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F022",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 459,
      "held_out_group": "F023",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F025",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 451,
      "held_out_group": "F026",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 459,
      "held_out_group": "F029",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F031",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 455,
      "held_out_group": "F034",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 457,
      "held_out_group": "F036",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F039",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F050",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F051",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F052",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F056",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F057",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "cpu",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "delay",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "disk",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "loss",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "mem",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "socket",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 444,
      "held_out_group": "OPERATION",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 431,
      "held_out_group": "POD",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 58,
      "held_out_group": "SERVICE",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 456,
      "held_out_group": "UNKNOWN",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 360,
      "held_out_group": "RCA100",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 283,
      "held_out_group": "RE2-OB",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 283,
      "held_out_group": "RE2-SS",
      "option": "A0",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F001",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F002",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 460,
      "held_out_group": "F004",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F005",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 457,
      "held_out_group": "F006",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 451,
      "held_out_group": "F007",
      "option": "A1",
      "root_damage": 5,
      "root_net_rescue": 44,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F009",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 458,
      "held_out_group": "F010",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F011",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F012",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 452,
      "held_out_group": "F014",
      "option": "A1",
      "root_damage": 4,
      "root_net_rescue": 45,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 452,
      "held_out_group": "F016",
      "option": "A1",
      "root_damage": 5,
      "root_net_rescue": 44,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F018",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F020",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F022",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 459,
      "held_out_group": "F023",
      "option": "A1",
      "root_damage": 5,
      "root_net_rescue": 44,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F025",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 451,
      "held_out_group": "F026",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 459,
      "held_out_group": "F029",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F031",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 455,
      "held_out_group": "F034",
      "option": "A1",
      "root_damage": 5,
      "root_net_rescue": 44,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 457,
      "held_out_group": "F036",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F039",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F050",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F051",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F052",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F056",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F057",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "cpu",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 37,
      "root_rescue": 43
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "delay",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 29,
      "root_rescue": 35
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "disk",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 42,
      "root_rescue": 48
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "loss",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 22,
      "root_rescue": 28
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "mem",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 39,
      "root_rescue": 45
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "socket",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 40,
      "root_rescue": 46
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 444,
      "held_out_group": "OPERATION",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 431,
      "held_out_group": "POD",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 58,
      "held_out_group": "SERVICE",
      "option": "A1",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 456,
      "held_out_group": "UNKNOWN",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 43,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 360,
      "held_out_group": "RCA100",
      "option": "A1",
      "root_damage": 0,
      "root_net_rescue": 49,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 283,
      "held_out_group": "RE2-OB",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": 41,
      "root_rescue": 47
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 283,
      "held_out_group": "RE2-SS",
      "option": "A1",
      "root_damage": 6,
      "root_net_rescue": -4,
      "root_rescue": 2
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F001",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F002",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 460,
      "held_out_group": "F004",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F005",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 457,
      "held_out_group": "F006",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 451,
      "held_out_group": "F007",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F009",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 458,
      "held_out_group": "F010",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F011",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F012",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 452,
      "held_out_group": "F014",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 452,
      "held_out_group": "F016",
      "option": "A2",
      "root_damage": 1,
      "root_net_rescue": 48,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F018",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F020",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F022",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 459,
      "held_out_group": "F023",
      "option": "A2",
      "root_damage": 1,
      "root_net_rescue": 48,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F025",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 451,
      "held_out_group": "F026",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 459,
      "held_out_group": "F029",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F031",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 455,
      "held_out_group": "F034",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 457,
      "held_out_group": "F036",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F039",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F050",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F051",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F052",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F056",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F057",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "cpu",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 41,
      "root_rescue": 43
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "delay",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 33,
      "root_rescue": 35
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "disk",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 46,
      "root_rescue": 48
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "loss",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 26,
      "root_rescue": 28
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "mem",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 43,
      "root_rescue": 45
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "socket",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 44,
      "root_rescue": 46
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 444,
      "held_out_group": "OPERATION",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 431,
      "held_out_group": "POD",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 58,
      "held_out_group": "SERVICE",
      "option": "A2",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 456,
      "held_out_group": "UNKNOWN",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 47,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 360,
      "held_out_group": "RCA100",
      "option": "A2",
      "root_damage": 0,
      "root_net_rescue": 49,
      "root_rescue": 49
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 283,
      "held_out_group": "RE2-OB",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 45,
      "root_rescue": 47
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 283,
      "held_out_group": "RE2-SS",
      "option": "A2",
      "root_damage": 2,
      "root_net_rescue": 0,
      "root_rescue": 2
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F001",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F002",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 460,
      "held_out_group": "F004",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F005",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 457,
      "held_out_group": "F006",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 451,
      "held_out_group": "F007",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F009",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 458,
      "held_out_group": "F010",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F011",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F012",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 452,
      "held_out_group": "F014",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 452,
      "held_out_group": "F016",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F018",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F020",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F022",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 459,
      "held_out_group": "F023",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F025",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 451,
      "held_out_group": "F026",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 459,
      "held_out_group": "F029",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F031",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 455,
      "held_out_group": "F034",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 457,
      "held_out_group": "F036",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F039",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F050",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F051",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F052",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F056",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F057",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "cpu",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "delay",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "disk",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "loss",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "mem",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "socket",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 444,
      "held_out_group": "OPERATION",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 431,
      "held_out_group": "POD",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 58,
      "held_out_group": "SERVICE",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 456,
      "held_out_group": "UNKNOWN",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 360,
      "held_out_group": "RCA100",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 283,
      "held_out_group": "RE2-OB",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 283,
      "held_out_group": "RE2-SS",
      "option": "A3",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F001",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F002",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 460,
      "held_out_group": "F004",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F005",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 457,
      "held_out_group": "F006",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 451,
      "held_out_group": "F007",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F009",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 458,
      "held_out_group": "F010",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F011",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F012",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 452,
      "held_out_group": "F014",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 452,
      "held_out_group": "F016",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F018",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F020",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 461,
      "held_out_group": "F022",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 459,
      "held_out_group": "F023",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F025",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 451,
      "held_out_group": "F026",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 459,
      "held_out_group": "F029",
      "option": "A4",
      "root_damage": 1,
      "root_net_rescue": -1,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F031",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 455,
      "held_out_group": "F034",
      "option": "A4",
      "root_damage": 1,
      "root_net_rescue": -1,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 457,
      "held_out_group": "F036",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F039",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F050",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F051",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F052",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F056",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 462,
      "held_out_group": "F057",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "cpu",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "delay",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "disk",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "loss",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "mem",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
      "denominator": 403,
      "held_out_group": "socket",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 444,
      "held_out_group": "OPERATION",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 431,
      "held_out_group": "POD",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 58,
      "held_out_group": "SERVICE",
      "option": "A4",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
      "denominator": 456,
      "held_out_group": "UNKNOWN",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 360,
      "held_out_group": "RCA100",
      "option": "A4",
      "root_damage": 0,
      "root_net_rescue": 0,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 283,
      "held_out_group": "RE2-OB",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    },
    {
      "axis": "LEAVE_ONE_SYSTEM_OUT",
      "denominator": 283,
      "held_out_group": "RE2-SS",
      "option": "A4",
      "root_damage": 2,
      "root_net_rescue": -2,
      "root_rescue": 0
    }
  ],
  "new_external_data_accessed": false,
  "options": {
    "A0": {
      "all_consumed": {
        "correct_override": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        },
        "denominator": 463,
        "downstream_symptom_selection": {
          "denominator": 463,
          "numerator": 27,
          "value": 0.058315334773218146
        },
        "entity_layer_error": {
          "denominator": 463,
          "numerator": 64,
          "value": 0.13822894168466524
        },
        "entity_layer_error_among_final_wrong": {
          "denominator": 156,
          "numerator": 64,
          "value": 0.41025641025641024
        },
        "exact_final": {
          "denominator": 463,
          "numerator": 307,
          "value": 0.6630669546436285
        },
        "exact_initial": {
          "denominator": 463,
          "numerator": 307,
          "value": 0.6630669546436285
        },
        "expected_tool_use": {
          "case_denominator": 463,
          "deterministic_component_evaluations": {
            "case_denominator": 463,
            "mean_per_case": 2.0,
            "total": 926
          },
          "deterministic_components": [
            "CANONICAL_ENTITY_HIERARCHY",
            "FAULT_ONTOLOGY_CLASSIFIER"
          ],
          "external_tool_calls": {
            "case_denominator": 463,
            "mean_per_case": 0.0,
            "total": 0
          }
        },
        "override": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        },
        "pair_damage": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        },
        "pair_net_rescue": 0,
        "pair_rescue": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        },
        "root_damage": {
          "denominator": 307,
          "numerator": 0,
          "value": 0.0
        },
        "root_net_rescue": 0,
        "root_rescue": {
          "denominator": 156,
          "numerator": 0,
          "value": 0.0
        },
        "service_final": {
          "denominator": 463,
          "numerator": 314,
          "value": 0.6781857451403888
        },
        "service_initial": {
          "denominator": 463,
          "numerator": 314,
          "value": 0.6781857451403888
        },
        "wrong_override": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        }
      },
      "expected_model_calls": 1,
      "name": "STRONG_SINGLE_HIERARCHICAL",
      "obss_fixtures": {
        "candidate-3": {
          "correct_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 11,
            "numerator": 0,
            "value": 0.0
          },
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
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 2.0,
              "total": 120
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 60,
            "numerator": 49,
            "value": 0.8166666666666667
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 49,
            "value": 0.8166666666666667
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "candidate-4": {
          "correct_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 1,
            "value": 0.016666666666666666
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 9,
            "numerator": 1,
            "value": 0.1111111111111111
          },
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
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 2.0,
              "total": 120
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "candidate-5": {
          "correct_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 3,
            "value": 0.05
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 15,
            "numerator": 0,
            "value": 0.0
          },
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
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 2.0,
              "total": 120
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 60,
            "numerator": 45,
            "value": 0.75
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 45,
            "value": 0.75
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "pr21-regression": {
          "correct_override": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 120,
          "downstream_symptom_selection": {
            "denominator": 120,
            "numerator": 3,
            "value": 0.025
          },
          "entity_layer_error": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 25,
            "numerator": 0,
            "value": 0.0
          },
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
          "expected_tool_use": {
            "case_denominator": 120,
            "deterministic_component_evaluations": {
              "case_denominator": 120,
              "mean_per_case": 2.0,
              "total": 240
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER"
            ],
            "external_tool_calls": {
              "case_denominator": 120,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 120,
            "numerator": 95,
            "value": 0.7916666666666666
          },
          "service_initial": {
            "denominator": 120,
            "numerator": 95,
            "value": 0.7916666666666666
          },
          "wrong_override": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          }
        },
        "pr21-tune": {
          "correct_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 9,
            "numerator": 0,
            "value": 0.0
          },
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
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 2.0,
              "total": 120
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        }
      },
      "rca100": {
        "correct_override": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "denominator": 103,
        "downstream_symptom_selection": {
          "denominator": 103,
          "numerator": 15,
          "value": 0.14563106796116504
        },
        "entity_layer_error": {
          "denominator": 103,
          "numerator": 63,
          "value": 0.6116504854368932
        },
        "entity_layer_error_among_final_wrong": {
          "denominator": 87,
          "numerator": 63,
          "value": 0.7241379310344828
        },
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
        "expected_tool_use": {
          "case_denominator": 103,
          "deterministic_component_evaluations": {
            "case_denominator": 103,
            "mean_per_case": 2.0,
            "total": 206
          },
          "deterministic_components": [
            "CANONICAL_ENTITY_HIERARCHY",
            "FAULT_ONTOLOGY_CLASSIFIER"
          ],
          "external_tool_calls": {
            "case_denominator": 103,
            "mean_per_case": 0.0,
            "total": 0
          }
        },
        "override": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "pair_damage": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "pair_net_rescue": 0,
        "pair_rescue": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
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
        },
        "service_final": {
          "denominator": 103,
          "numerator": 23,
          "value": 0.22330097087378642
        },
        "service_initial": {
          "denominator": 103,
          "numerator": 23,
          "value": 0.22330097087378642
        },
        "wrong_override": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        }
      },
      "selectable": true
    },
    "A1": {
      "all_consumed": {
        "correct_override": {
          "denominator": 463,
          "numerator": 49,
          "value": 0.10583153347732181
        },
        "denominator": 463,
        "downstream_symptom_selection": {
          "denominator": 463,
          "numerator": 18,
          "value": 0.038876889848812095
        },
        "entity_layer_error": {
          "denominator": 463,
          "numerator": 71,
          "value": 0.15334773218142547
        },
        "entity_layer_error_among_final_wrong": {
          "denominator": 113,
          "numerator": 71,
          "value": 0.6283185840707964
        },
        "exact_final": {
          "denominator": 463,
          "numerator": 350,
          "value": 0.755939524838013
        },
        "exact_initial": {
          "denominator": 463,
          "numerator": 307,
          "value": 0.6630669546436285
        },
        "expected_tool_use": {
          "case_denominator": 463,
          "deterministic_component_evaluations": {
            "case_denominator": 463,
            "mean_per_case": 3.0,
            "total": 1389
          },
          "deterministic_components": [
            "CANONICAL_ENTITY_HIERARCHY",
            "FAULT_ONTOLOGY_CLASSIFIER",
            "HISTORICAL_METRICS_M3"
          ],
          "external_tool_calls": {
            "case_denominator": 463,
            "mean_per_case": 0.0,
            "total": 0
          }
        },
        "override": {
          "denominator": 463,
          "numerator": 85,
          "value": 0.183585313174946
        },
        "pair_damage": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        },
        "pair_net_rescue": 8,
        "pair_rescue": {
          "denominator": 463,
          "numerator": 8,
          "value": 0.017278617710583154
        },
        "root_damage": {
          "denominator": 307,
          "numerator": 6,
          "value": 0.019543973941368076
        },
        "root_net_rescue": 43,
        "root_rescue": {
          "denominator": 156,
          "numerator": 49,
          "value": 0.3141025641025641
        },
        "service_final": {
          "denominator": 463,
          "numerator": 357,
          "value": 0.7710583153347732
        },
        "service_initial": {
          "denominator": 463,
          "numerator": 314,
          "value": 0.6781857451403888
        },
        "wrong_override": {
          "denominator": 463,
          "numerator": 36,
          "value": 0.07775377969762419
        }
      },
      "expected_model_calls": 1,
      "name": "HISTORICAL_M3",
      "obss_fixtures": {
        "candidate-3": {
          "correct_override": {
            "denominator": 60,
            "numerator": 8,
            "value": 0.13333333333333333
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 3,
            "numerator": 0,
            "value": 0.0
          },
          "exact_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "exact_initial": {
            "denominator": 60,
            "numerator": 49,
            "value": 0.8166666666666667
          },
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 3.0,
              "total": 180
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "HISTORICAL_METRICS_M3"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 8,
            "value": 0.13333333333333333
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 1,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 1,
            "value": 0.016666666666666666
          },
          "root_damage": {
            "denominator": 49,
            "numerator": 0,
            "value": 0.0
          },
          "root_net_rescue": 8,
          "root_rescue": {
            "denominator": 11,
            "numerator": 8,
            "value": 0.7272727272727273
          },
          "service_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 49,
            "value": 0.8166666666666667
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "candidate-4": {
          "correct_override": {
            "denominator": 60,
            "numerator": 6,
            "value": 0.1
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 1,
            "value": 0.016666666666666666
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 1,
            "value": 0.016666666666666666
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 3,
            "numerator": 1,
            "value": 0.3333333333333333
          },
          "exact_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "exact_initial": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 3.0,
              "total": 180
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "HISTORICAL_METRICS_M3"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 6,
            "value": 0.1
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 2,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "root_damage": {
            "denominator": 51,
            "numerator": 0,
            "value": 0.0
          },
          "root_net_rescue": 6,
          "root_rescue": {
            "denominator": 9,
            "numerator": 6,
            "value": 0.6666666666666666
          },
          "service_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "candidate-5": {
          "correct_override": {
            "denominator": 60,
            "numerator": 12,
            "value": 0.2
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 3,
            "numerator": 0,
            "value": 0.0
          },
          "exact_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "exact_initial": {
            "denominator": 60,
            "numerator": 45,
            "value": 0.75
          },
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 3.0,
              "total": 180
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "HISTORICAL_METRICS_M3"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 12,
            "value": 0.2
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 1,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 1,
            "value": 0.016666666666666666
          },
          "root_damage": {
            "denominator": 45,
            "numerator": 0,
            "value": 0.0
          },
          "root_net_rescue": 12,
          "root_rescue": {
            "denominator": 15,
            "numerator": 12,
            "value": 0.8
          },
          "service_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 45,
            "value": 0.75
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "pr21-regression": {
          "correct_override": {
            "denominator": 120,
            "numerator": 17,
            "value": 0.14166666666666666
          },
          "denominator": 120,
          "downstream_symptom_selection": {
            "denominator": 120,
            "numerator": 3,
            "value": 0.025
          },
          "entity_layer_error": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 8,
            "numerator": 0,
            "value": 0.0
          },
          "exact_final": {
            "denominator": 120,
            "numerator": 112,
            "value": 0.9333333333333333
          },
          "exact_initial": {
            "denominator": 120,
            "numerator": 95,
            "value": 0.7916666666666666
          },
          "expected_tool_use": {
            "case_denominator": 120,
            "deterministic_component_evaluations": {
              "case_denominator": 120,
              "mean_per_case": 3.0,
              "total": 360
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "HISTORICAL_METRICS_M3"
            ],
            "external_tool_calls": {
              "case_denominator": 120,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 120,
            "numerator": 17,
            "value": 0.14166666666666666
          },
          "pair_damage": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 3,
          "pair_rescue": {
            "denominator": 120,
            "numerator": 3,
            "value": 0.025
          },
          "root_damage": {
            "denominator": 95,
            "numerator": 0,
            "value": 0.0
          },
          "root_net_rescue": 17,
          "root_rescue": {
            "denominator": 25,
            "numerator": 17,
            "value": 0.68
          },
          "service_final": {
            "denominator": 120,
            "numerator": 112,
            "value": 0.9333333333333333
          },
          "service_initial": {
            "denominator": 120,
            "numerator": 95,
            "value": 0.7916666666666666
          },
          "wrong_override": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          }
        },
        "pr21-tune": {
          "correct_override": {
            "denominator": 60,
            "numerator": 6,
            "value": 0.1
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 3,
            "numerator": 0,
            "value": 0.0
          },
          "exact_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "exact_initial": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 3.0,
              "total": 180
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "HISTORICAL_METRICS_M3"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 6,
            "value": 0.1
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 1,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 1,
            "value": 0.016666666666666666
          },
          "root_damage": {
            "denominator": 51,
            "numerator": 0,
            "value": 0.0
          },
          "root_net_rescue": 6,
          "root_rescue": {
            "denominator": 9,
            "numerator": 6,
            "value": 0.6666666666666666
          },
          "service_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        }
      },
      "rca100": {
        "correct_override": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "denominator": 103,
        "downstream_symptom_selection": {
          "denominator": 103,
          "numerator": 8,
          "value": 0.07766990291262135
        },
        "entity_layer_error": {
          "denominator": 103,
          "numerator": 70,
          "value": 0.6796116504854369
        },
        "entity_layer_error_among_final_wrong": {
          "denominator": 93,
          "numerator": 70,
          "value": 0.7526881720430108
        },
        "exact_final": {
          "denominator": 103,
          "numerator": 10,
          "value": 0.0970873786407767
        },
        "exact_initial": {
          "denominator": 103,
          "numerator": 16,
          "value": 0.1553398058252427
        },
        "expected_tool_use": {
          "case_denominator": 103,
          "deterministic_component_evaluations": {
            "case_denominator": 103,
            "mean_per_case": 3.0,
            "total": 309
          },
          "deterministic_components": [
            "CANONICAL_ENTITY_HIERARCHY",
            "FAULT_ONTOLOGY_CLASSIFIER",
            "HISTORICAL_METRICS_M3"
          ],
          "external_tool_calls": {
            "case_denominator": 103,
            "mean_per_case": 0.0,
            "total": 0
          }
        },
        "override": {
          "denominator": 103,
          "numerator": 36,
          "value": 0.34951456310679613
        },
        "pair_damage": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "pair_net_rescue": 0,
        "pair_rescue": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "root_damage": {
          "denominator": 16,
          "numerator": 6,
          "value": 0.375
        },
        "root_net_rescue": -6,
        "root_rescue": {
          "denominator": 87,
          "numerator": 0,
          "value": 0.0
        },
        "service_final": {
          "denominator": 103,
          "numerator": 17,
          "value": 0.1650485436893204
        },
        "service_initial": {
          "denominator": 103,
          "numerator": 23,
          "value": 0.22330097087378642
        },
        "wrong_override": {
          "denominator": 103,
          "numerator": 36,
          "value": 0.34951456310679613
        }
      },
      "selectable": false
    },
    "A2": {
      "all_consumed": {
        "correct_override": {
          "denominator": 463,
          "numerator": 49,
          "value": 0.10583153347732181
        },
        "denominator": 463,
        "downstream_symptom_selection": {
          "denominator": 463,
          "numerator": 23,
          "value": 0.04967602591792657
        },
        "entity_layer_error": {
          "denominator": 463,
          "numerator": 61,
          "value": 0.13174946004319654
        },
        "entity_layer_error_among_final_wrong": {
          "denominator": 109,
          "numerator": 61,
          "value": 0.5596330275229358
        },
        "exact_final": {
          "denominator": 463,
          "numerator": 354,
          "value": 0.7645788336933045
        },
        "exact_initial": {
          "denominator": 463,
          "numerator": 307,
          "value": 0.6630669546436285
        },
        "expected_tool_use": {
          "case_denominator": 463,
          "deterministic_component_evaluations": {
            "case_denominator": 463,
            "mean_per_case": 4.0,
            "total": 1852
          },
          "deterministic_components": [
            "CANONICAL_ENTITY_HIERARCHY",
            "FAULT_ONTOLOGY_CLASSIFIER",
            "HIERARCHY_GUARD",
            "METRICS_ARBITRATION"
          ],
          "external_tool_calls": {
            "case_denominator": 463,
            "mean_per_case": 0.0,
            "total": 0
          }
        },
        "override": {
          "denominator": 463,
          "numerator": 62,
          "value": 0.13390928725701945
        },
        "pair_damage": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        },
        "pair_net_rescue": 8,
        "pair_rescue": {
          "denominator": 463,
          "numerator": 8,
          "value": 0.017278617710583154
        },
        "root_damage": {
          "denominator": 307,
          "numerator": 2,
          "value": 0.006514657980456026
        },
        "root_net_rescue": 47,
        "root_rescue": {
          "denominator": 156,
          "numerator": 49,
          "value": 0.3141025641025641
        },
        "service_final": {
          "denominator": 463,
          "numerator": 359,
          "value": 0.775377969762419
        },
        "service_initial": {
          "denominator": 463,
          "numerator": 314,
          "value": 0.6781857451403888
        },
        "wrong_override": {
          "denominator": 463,
          "numerator": 13,
          "value": 0.028077753779697623
        }
      },
      "expected_model_calls": 1,
      "name": "HIERARCHY_GUARDED_METRICS_ARBITRATION",
      "obss_fixtures": {
        "candidate-3": {
          "correct_override": {
            "denominator": 60,
            "numerator": 8,
            "value": 0.13333333333333333
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 3,
            "numerator": 0,
            "value": 0.0
          },
          "exact_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "exact_initial": {
            "denominator": 60,
            "numerator": 49,
            "value": 0.8166666666666667
          },
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 4.0,
              "total": 240
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 8,
            "value": 0.13333333333333333
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 1,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 1,
            "value": 0.016666666666666666
          },
          "root_damage": {
            "denominator": 49,
            "numerator": 0,
            "value": 0.0
          },
          "root_net_rescue": 8,
          "root_rescue": {
            "denominator": 11,
            "numerator": 8,
            "value": 0.7272727272727273
          },
          "service_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 49,
            "value": 0.8166666666666667
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "candidate-4": {
          "correct_override": {
            "denominator": 60,
            "numerator": 6,
            "value": 0.1
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 1,
            "value": 0.016666666666666666
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 1,
            "value": 0.016666666666666666
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 3,
            "numerator": 1,
            "value": 0.3333333333333333
          },
          "exact_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "exact_initial": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 4.0,
              "total": 240
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 6,
            "value": 0.1
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 2,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "root_damage": {
            "denominator": 51,
            "numerator": 0,
            "value": 0.0
          },
          "root_net_rescue": 6,
          "root_rescue": {
            "denominator": 9,
            "numerator": 6,
            "value": 0.6666666666666666
          },
          "service_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "candidate-5": {
          "correct_override": {
            "denominator": 60,
            "numerator": 12,
            "value": 0.2
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 3,
            "numerator": 0,
            "value": 0.0
          },
          "exact_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "exact_initial": {
            "denominator": 60,
            "numerator": 45,
            "value": 0.75
          },
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 4.0,
              "total": 240
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 12,
            "value": 0.2
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 1,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 1,
            "value": 0.016666666666666666
          },
          "root_damage": {
            "denominator": 45,
            "numerator": 0,
            "value": 0.0
          },
          "root_net_rescue": 12,
          "root_rescue": {
            "denominator": 15,
            "numerator": 12,
            "value": 0.8
          },
          "service_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 45,
            "value": 0.75
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "pr21-regression": {
          "correct_override": {
            "denominator": 120,
            "numerator": 17,
            "value": 0.14166666666666666
          },
          "denominator": 120,
          "downstream_symptom_selection": {
            "denominator": 120,
            "numerator": 3,
            "value": 0.025
          },
          "entity_layer_error": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 8,
            "numerator": 0,
            "value": 0.0
          },
          "exact_final": {
            "denominator": 120,
            "numerator": 112,
            "value": 0.9333333333333333
          },
          "exact_initial": {
            "denominator": 120,
            "numerator": 95,
            "value": 0.7916666666666666
          },
          "expected_tool_use": {
            "case_denominator": 120,
            "deterministic_component_evaluations": {
              "case_denominator": 120,
              "mean_per_case": 4.0,
              "total": 480
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION"
            ],
            "external_tool_calls": {
              "case_denominator": 120,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 120,
            "numerator": 17,
            "value": 0.14166666666666666
          },
          "pair_damage": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 3,
          "pair_rescue": {
            "denominator": 120,
            "numerator": 3,
            "value": 0.025
          },
          "root_damage": {
            "denominator": 95,
            "numerator": 0,
            "value": 0.0
          },
          "root_net_rescue": 17,
          "root_rescue": {
            "denominator": 25,
            "numerator": 17,
            "value": 0.68
          },
          "service_final": {
            "denominator": 120,
            "numerator": 112,
            "value": 0.9333333333333333
          },
          "service_initial": {
            "denominator": 120,
            "numerator": 95,
            "value": 0.7916666666666666
          },
          "wrong_override": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          }
        },
        "pr21-tune": {
          "correct_override": {
            "denominator": 60,
            "numerator": 6,
            "value": 0.1
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 3,
            "numerator": 0,
            "value": 0.0
          },
          "exact_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "exact_initial": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 4.0,
              "total": 240
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 6,
            "value": 0.1
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 1,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 1,
            "value": 0.016666666666666666
          },
          "root_damage": {
            "denominator": 51,
            "numerator": 0,
            "value": 0.0
          },
          "root_net_rescue": 6,
          "root_rescue": {
            "denominator": 9,
            "numerator": 6,
            "value": 0.6666666666666666
          },
          "service_final": {
            "denominator": 60,
            "numerator": 57,
            "value": 0.95
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        }
      },
      "rca100": {
        "correct_override": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "denominator": 103,
        "downstream_symptom_selection": {
          "denominator": 103,
          "numerator": 13,
          "value": 0.1262135922330097
        },
        "entity_layer_error": {
          "denominator": 103,
          "numerator": 60,
          "value": 0.5825242718446602
        },
        "entity_layer_error_among_final_wrong": {
          "denominator": 89,
          "numerator": 60,
          "value": 0.6741573033707865
        },
        "exact_final": {
          "denominator": 103,
          "numerator": 14,
          "value": 0.13592233009708737
        },
        "exact_initial": {
          "denominator": 103,
          "numerator": 16,
          "value": 0.1553398058252427
        },
        "expected_tool_use": {
          "case_denominator": 103,
          "deterministic_component_evaluations": {
            "case_denominator": 103,
            "mean_per_case": 4.0,
            "total": 412
          },
          "deterministic_components": [
            "CANONICAL_ENTITY_HIERARCHY",
            "FAULT_ONTOLOGY_CLASSIFIER",
            "HIERARCHY_GUARD",
            "METRICS_ARBITRATION"
          ],
          "external_tool_calls": {
            "case_denominator": 103,
            "mean_per_case": 0.0,
            "total": 0
          }
        },
        "override": {
          "denominator": 103,
          "numerator": 13,
          "value": 0.1262135922330097
        },
        "pair_damage": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "pair_net_rescue": 0,
        "pair_rescue": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "root_damage": {
          "denominator": 16,
          "numerator": 2,
          "value": 0.125
        },
        "root_net_rescue": -2,
        "root_rescue": {
          "denominator": 87,
          "numerator": 0,
          "value": 0.0
        },
        "service_final": {
          "denominator": 103,
          "numerator": 19,
          "value": 0.18446601941747573
        },
        "service_initial": {
          "denominator": 103,
          "numerator": 23,
          "value": 0.22330097087378642
        },
        "wrong_override": {
          "denominator": 103,
          "numerator": 13,
          "value": 0.1262135922330097
        }
      },
      "selectable": true
    },
    "A3": {
      "all_consumed": {
        "correct_override": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        },
        "denominator": 463,
        "downstream_symptom_selection": {
          "denominator": 463,
          "numerator": 27,
          "value": 0.058315334773218146
        },
        "entity_layer_error": {
          "denominator": 463,
          "numerator": 64,
          "value": 0.13822894168466524
        },
        "entity_layer_error_among_final_wrong": {
          "denominator": 156,
          "numerator": 64,
          "value": 0.41025641025641024
        },
        "exact_final": {
          "denominator": 463,
          "numerator": 307,
          "value": 0.6630669546436285
        },
        "exact_initial": {
          "denominator": 463,
          "numerator": 307,
          "value": 0.6630669546436285
        },
        "expected_tool_use": {
          "case_denominator": 463,
          "deterministic_component_evaluations": {
            "case_denominator": 463,
            "mean_per_case": 5.0,
            "total": 2315
          },
          "deterministic_components": [
            "CANONICAL_ENTITY_HIERARCHY",
            "FAULT_ONTOLOGY_CLASSIFIER",
            "LOCAL_RESOURCE_GATE",
            "HIERARCHY_GUARD",
            "METRICS_ARBITRATION"
          ],
          "external_tool_calls": {
            "case_denominator": 463,
            "mean_per_case": 0.0,
            "total": 0
          }
        },
        "override": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        },
        "pair_damage": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        },
        "pair_net_rescue": 0,
        "pair_rescue": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        },
        "root_damage": {
          "denominator": 307,
          "numerator": 0,
          "value": 0.0
        },
        "root_net_rescue": 0,
        "root_rescue": {
          "denominator": 156,
          "numerator": 0,
          "value": 0.0
        },
        "service_final": {
          "denominator": 463,
          "numerator": 314,
          "value": 0.6781857451403888
        },
        "service_initial": {
          "denominator": 463,
          "numerator": 314,
          "value": 0.6781857451403888
        },
        "wrong_override": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        }
      },
      "expected_model_calls": 1,
      "name": "LOCAL_FAULT_METRICS_ARBITRATION",
      "obss_fixtures": {
        "candidate-3": {
          "correct_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 11,
            "numerator": 0,
            "value": 0.0
          },
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
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 5.0,
              "total": 300
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "LOCAL_RESOURCE_GATE",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 60,
            "numerator": 49,
            "value": 0.8166666666666667
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 49,
            "value": 0.8166666666666667
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "candidate-4": {
          "correct_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 1,
            "value": 0.016666666666666666
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 9,
            "numerator": 1,
            "value": 0.1111111111111111
          },
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
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 5.0,
              "total": 300
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "LOCAL_RESOURCE_GATE",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "candidate-5": {
          "correct_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 3,
            "value": 0.05
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 15,
            "numerator": 0,
            "value": 0.0
          },
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
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 5.0,
              "total": 300
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "LOCAL_RESOURCE_GATE",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 60,
            "numerator": 45,
            "value": 0.75
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 45,
            "value": 0.75
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "pr21-regression": {
          "correct_override": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 120,
          "downstream_symptom_selection": {
            "denominator": 120,
            "numerator": 3,
            "value": 0.025
          },
          "entity_layer_error": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 25,
            "numerator": 0,
            "value": 0.0
          },
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
          "expected_tool_use": {
            "case_denominator": 120,
            "deterministic_component_evaluations": {
              "case_denominator": 120,
              "mean_per_case": 5.0,
              "total": 600
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "LOCAL_RESOURCE_GATE",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION"
            ],
            "external_tool_calls": {
              "case_denominator": 120,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 120,
            "numerator": 95,
            "value": 0.7916666666666666
          },
          "service_initial": {
            "denominator": 120,
            "numerator": 95,
            "value": 0.7916666666666666
          },
          "wrong_override": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          }
        },
        "pr21-tune": {
          "correct_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 9,
            "numerator": 0,
            "value": 0.0
          },
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
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 5.0,
              "total": 300
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "LOCAL_RESOURCE_GATE",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        }
      },
      "rca100": {
        "correct_override": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "denominator": 103,
        "downstream_symptom_selection": {
          "denominator": 103,
          "numerator": 15,
          "value": 0.14563106796116504
        },
        "entity_layer_error": {
          "denominator": 103,
          "numerator": 63,
          "value": 0.6116504854368932
        },
        "entity_layer_error_among_final_wrong": {
          "denominator": 87,
          "numerator": 63,
          "value": 0.7241379310344828
        },
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
        "expected_tool_use": {
          "case_denominator": 103,
          "deterministic_component_evaluations": {
            "case_denominator": 103,
            "mean_per_case": 5.0,
            "total": 515
          },
          "deterministic_components": [
            "CANONICAL_ENTITY_HIERARCHY",
            "FAULT_ONTOLOGY_CLASSIFIER",
            "LOCAL_RESOURCE_GATE",
            "HIERARCHY_GUARD",
            "METRICS_ARBITRATION"
          ],
          "external_tool_calls": {
            "case_denominator": 103,
            "mean_per_case": 0.0,
            "total": 0
          }
        },
        "override": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "pair_damage": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "pair_net_rescue": 0,
        "pair_rescue": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
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
        },
        "service_final": {
          "denominator": 103,
          "numerator": 23,
          "value": 0.22330097087378642
        },
        "service_initial": {
          "denominator": 103,
          "numerator": 23,
          "value": 0.22330097087378642
        },
        "wrong_override": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        }
      },
      "selectable": true
    },
    "A4": {
      "all_consumed": {
        "correct_override": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        },
        "denominator": 463,
        "downstream_symptom_selection": {
          "denominator": 463,
          "numerator": 27,
          "value": 0.058315334773218146
        },
        "entity_layer_error": {
          "denominator": 463,
          "numerator": 66,
          "value": 0.14254859611231102
        },
        "entity_layer_error_among_final_wrong": {
          "denominator": 158,
          "numerator": 66,
          "value": 0.4177215189873418
        },
        "exact_final": {
          "denominator": 463,
          "numerator": 305,
          "value": 0.6587473002159827
        },
        "exact_initial": {
          "denominator": 463,
          "numerator": 307,
          "value": 0.6630669546436285
        },
        "expected_tool_use": {
          "case_denominator": 463,
          "deterministic_component_evaluations": {
            "case_denominator": 463,
            "mean_per_case": 6.0,
            "total": 2778
          },
          "deterministic_components": [
            "CANONICAL_ENTITY_HIERARCHY",
            "FAULT_ONTOLOGY_CLASSIFIER",
            "LOCAL_RESOURCE_GATE",
            "HIERARCHY_GUARD",
            "METRICS_ARBITRATION",
            "DETERMINISTIC_CAUSAL_RANKING"
          ],
          "external_tool_calls": {
            "case_denominator": 463,
            "mean_per_case": 0.0,
            "total": 0
          }
        },
        "override": {
          "denominator": 463,
          "numerator": 4,
          "value": 0.008639308855291577
        },
        "pair_damage": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        },
        "pair_net_rescue": 0,
        "pair_rescue": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        },
        "root_damage": {
          "denominator": 307,
          "numerator": 2,
          "value": 0.006514657980456026
        },
        "root_net_rescue": -2,
        "root_rescue": {
          "denominator": 156,
          "numerator": 0,
          "value": 0.0
        },
        "service_final": {
          "denominator": 463,
          "numerator": 313,
          "value": 0.6760259179265659
        },
        "service_initial": {
          "denominator": 463,
          "numerator": 314,
          "value": 0.6781857451403888
        },
        "wrong_override": {
          "denominator": 463,
          "numerator": 4,
          "value": 0.008639308855291577
        }
      },
      "expected_model_calls": 1,
      "name": "HYBRID_LOCAL_METRICS_CAUSAL_RANKING",
      "obss_fixtures": {
        "candidate-3": {
          "correct_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 11,
            "numerator": 0,
            "value": 0.0
          },
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
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 6.0,
              "total": 360
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "LOCAL_RESOURCE_GATE",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION",
              "DETERMINISTIC_CAUSAL_RANKING"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 60,
            "numerator": 49,
            "value": 0.8166666666666667
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 49,
            "value": 0.8166666666666667
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "candidate-4": {
          "correct_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 1,
            "value": 0.016666666666666666
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 9,
            "numerator": 1,
            "value": 0.1111111111111111
          },
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
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 6.0,
              "total": 360
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "LOCAL_RESOURCE_GATE",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION",
              "DETERMINISTIC_CAUSAL_RANKING"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "candidate-5": {
          "correct_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 3,
            "value": 0.05
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 15,
            "numerator": 0,
            "value": 0.0
          },
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
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 6.0,
              "total": 360
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "LOCAL_RESOURCE_GATE",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION",
              "DETERMINISTIC_CAUSAL_RANKING"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 60,
            "numerator": 45,
            "value": 0.75
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 45,
            "value": 0.75
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        },
        "pr21-regression": {
          "correct_override": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 120,
          "downstream_symptom_selection": {
            "denominator": 120,
            "numerator": 3,
            "value": 0.025
          },
          "entity_layer_error": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 25,
            "numerator": 0,
            "value": 0.0
          },
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
          "expected_tool_use": {
            "case_denominator": 120,
            "deterministic_component_evaluations": {
              "case_denominator": 120,
              "mean_per_case": 6.0,
              "total": 720
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "LOCAL_RESOURCE_GATE",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION",
              "DETERMINISTIC_CAUSAL_RANKING"
            ],
            "external_tool_calls": {
              "case_denominator": 120,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 120,
            "numerator": 95,
            "value": 0.7916666666666666
          },
          "service_initial": {
            "denominator": 120,
            "numerator": 95,
            "value": 0.7916666666666666
          },
          "wrong_override": {
            "denominator": 120,
            "numerator": 0,
            "value": 0.0
          }
        },
        "pr21-tune": {
          "correct_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "denominator": 60,
          "downstream_symptom_selection": {
            "denominator": 60,
            "numerator": 2,
            "value": 0.03333333333333333
          },
          "entity_layer_error": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "entity_layer_error_among_final_wrong": {
            "denominator": 9,
            "numerator": 0,
            "value": 0.0
          },
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
          "expected_tool_use": {
            "case_denominator": 60,
            "deterministic_component_evaluations": {
              "case_denominator": 60,
              "mean_per_case": 6.0,
              "total": 360
            },
            "deterministic_components": [
              "CANONICAL_ENTITY_HIERARCHY",
              "FAULT_ONTOLOGY_CLASSIFIER",
              "LOCAL_RESOURCE_GATE",
              "HIERARCHY_GUARD",
              "METRICS_ARBITRATION",
              "DETERMINISTIC_CAUSAL_RANKING"
            ],
            "external_tool_calls": {
              "case_denominator": 60,
              "mean_per_case": 0.0,
              "total": 0
            }
          },
          "override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_damage": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          },
          "pair_net_rescue": 0,
          "pair_rescue": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
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
          },
          "service_final": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "service_initial": {
            "denominator": 60,
            "numerator": 51,
            "value": 0.85
          },
          "wrong_override": {
            "denominator": 60,
            "numerator": 0,
            "value": 0.0
          }
        }
      },
      "rca100": {
        "correct_override": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "denominator": 103,
        "downstream_symptom_selection": {
          "denominator": 103,
          "numerator": 15,
          "value": 0.14563106796116504
        },
        "entity_layer_error": {
          "denominator": 103,
          "numerator": 65,
          "value": 0.6310679611650486
        },
        "entity_layer_error_among_final_wrong": {
          "denominator": 89,
          "numerator": 65,
          "value": 0.7303370786516854
        },
        "exact_final": {
          "denominator": 103,
          "numerator": 14,
          "value": 0.13592233009708737
        },
        "exact_initial": {
          "denominator": 103,
          "numerator": 16,
          "value": 0.1553398058252427
        },
        "expected_tool_use": {
          "case_denominator": 103,
          "deterministic_component_evaluations": {
            "case_denominator": 103,
            "mean_per_case": 6.0,
            "total": 618
          },
          "deterministic_components": [
            "CANONICAL_ENTITY_HIERARCHY",
            "FAULT_ONTOLOGY_CLASSIFIER",
            "LOCAL_RESOURCE_GATE",
            "HIERARCHY_GUARD",
            "METRICS_ARBITRATION",
            "DETERMINISTIC_CAUSAL_RANKING"
          ],
          "external_tool_calls": {
            "case_denominator": 103,
            "mean_per_case": 0.0,
            "total": 0
          }
        },
        "override": {
          "denominator": 103,
          "numerator": 4,
          "value": 0.038834951456310676
        },
        "pair_damage": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "pair_net_rescue": 0,
        "pair_rescue": {
          "denominator": 103,
          "numerator": 0,
          "value": 0.0
        },
        "root_damage": {
          "denominator": 16,
          "numerator": 2,
          "value": 0.125
        },
        "root_net_rescue": -2,
        "root_rescue": {
          "denominator": 87,
          "numerator": 0,
          "value": 0.0
        },
        "service_final": {
          "denominator": 103,
          "numerator": 22,
          "value": 0.21359223300970873
        },
        "service_initial": {
          "denominator": 103,
          "numerator": 23,
          "value": 0.22330097087378642
        },
        "wrong_override": {
          "denominator": 103,
          "numerator": 4,
          "value": 0.038834951456310676
        }
      },
      "selectable": true
    },
    "A5": {
      "eligible_case_count": {
        "denominator": 463,
        "numerator": 0,
        "value": 0.0
      },
      "expected_model_calls": 1.0,
      "name": "HYBRID_LOCAL_METRICS_SELECTIVE_CAUSAL_AGENT",
      "oracle_upper_bound": {
        "candidate_role_audit": {
          "EVENTS_CHANGE_VERIFIER": {
            "additional_call_nonredundant": true,
            "correct_root_or_path_coverage": {
              "denominator": 156,
              "numerator": 4,
              "value": 0.02564102564102564
            },
            "deterministic_proxy_evaluable": true,
            "distinguishes_root_from_symptom": true,
            "information_not_fully_consumed": true,
            "qualified": false
          },
          "FAULT_TYPE_ONTOLOGY_VERIFIER": {
            "additional_call_nonredundant": false,
            "correct_root_or_path_coverage": {
              "denominator": 156,
              "numerator": 0,
              "value": 0.0
            },
            "deterministic_proxy_evaluable": true,
            "distinguishes_root_from_symptom": false,
            "information_not_fully_consumed": true,
            "qualified": false
          },
          "HIERARCHY_RESOLVER": {
            "additional_call_nonredundant": false,
            "correct_root_or_path_coverage": {
              "denominator": 156,
              "numerator": 8,
              "value": 0.05128205128205128
            },
            "deterministic_proxy_evaluable": true,
            "distinguishes_root_from_symptom": false,
            "information_not_fully_consumed": true,
            "qualified": false
          },
          "TRACE_TOPOLOGY_CAUSAL_VERIFIER": {
            "additional_call_nonredundant": true,
            "correct_root_or_path_coverage": {
              "denominator": 156,
              "numerator": 12,
              "value": 0.07692307692307693
            },
            "deterministic_proxy_evaluable": true,
            "distinguishes_root_from_symptom": true,
            "information_not_fully_consumed": true,
            "qualified": false
          }
        },
        "eligible_cases": {
          "denominator": 463,
          "numerator": 0,
          "value": 0.0
        },
        "eligible_initial_wrong_coverage": {
          "denominator": 156,
          "numerator": 0,
          "value": 0.0
        },
        "eligible_roles": [],
        "free_form_root_generation": false,
        "mean_model_calls": 1.0,
        "message_contract_nonredundant": false,
        "obss_expected_non_degradation": true,
        "oracle_rca100_damage": 0,
        "oracle_rca100_net_rescue": 0,
        "oracle_rca100_rescue": 0,
        "oracle_root_damage_all_consumed": 0,
        "oracle_root_rescue_all_consumed": 0,
        "output_space": [
          "KEEP_INITIAL",
          "SELECT_CANDIDATE",
          "INCONCLUSIVE"
        ],
        "source_evidence_distinguishes_root_symptom": false
      },
      "reporting_boundary": "ORACLE_ONLY_NO_VERIFIER_OUTPUT",
      "selectable": true
    }
  },
  "provider_calls": 0,
  "provider_objects_constructed": 0,
  "re2_tt_accessed": false,
  "schema_version": "rca-crossbenchmark-architecture-frontier-report.v1",
  "selected_name": "STRONG_SINGLE_HIERARCHICAL",
  "selected_option": "A0",
  "selection_reason": "A2_TO_A5_GATES_NOT_ALL_SATISFIED_FALLBACK_A0",
  "selective_causal_agent_oracle_only": {
    "candidate_role_audit": {
      "EVENTS_CHANGE_VERIFIER": {
        "additional_call_nonredundant": true,
        "correct_root_or_path_coverage": {
          "denominator": 156,
          "numerator": 4,
          "value": 0.02564102564102564
        },
        "deterministic_proxy_evaluable": true,
        "distinguishes_root_from_symptom": true,
        "information_not_fully_consumed": true,
        "qualified": false
      },
      "FAULT_TYPE_ONTOLOGY_VERIFIER": {
        "additional_call_nonredundant": false,
        "correct_root_or_path_coverage": {
          "denominator": 156,
          "numerator": 0,
          "value": 0.0
        },
        "deterministic_proxy_evaluable": true,
        "distinguishes_root_from_symptom": false,
        "information_not_fully_consumed": true,
        "qualified": false
      },
      "HIERARCHY_RESOLVER": {
        "additional_call_nonredundant": false,
        "correct_root_or_path_coverage": {
          "denominator": 156,
          "numerator": 8,
          "value": 0.05128205128205128
        },
        "deterministic_proxy_evaluable": true,
        "distinguishes_root_from_symptom": false,
        "information_not_fully_consumed": true,
        "qualified": false
      },
      "TRACE_TOPOLOGY_CAUSAL_VERIFIER": {
        "additional_call_nonredundant": true,
        "correct_root_or_path_coverage": {
          "denominator": 156,
          "numerator": 12,
          "value": 0.07692307692307693
        },
        "deterministic_proxy_evaluable": true,
        "distinguishes_root_from_symptom": true,
        "information_not_fully_consumed": true,
        "qualified": false
      }
    },
    "eligible_cases": {
      "denominator": 463,
      "numerator": 0,
      "value": 0.0
    },
    "eligible_initial_wrong_coverage": {
      "denominator": 156,
      "numerator": 0,
      "value": 0.0
    },
    "eligible_roles": [],
    "free_form_root_generation": false,
    "mean_model_calls": 1.0,
    "message_contract_nonredundant": false,
    "obss_expected_non_degradation": true,
    "oracle_rca100_damage": 0,
    "oracle_rca100_net_rescue": 0,
    "oracle_rca100_rescue": 0,
    "oracle_root_damage_all_consumed": 0,
    "oracle_root_rescue_all_consumed": 0,
    "output_space": [
      "KEEP_INITIAL",
      "SELECT_CANDIDATE",
      "INCONCLUSIVE"
    ],
    "source_evidence_distinguishes_root_symptom": false
  },
  "semantic_operations": 0
}
```

# RCA A2 Applicability Frontier

```json
{
  "a2_reference": "G0_A2_REFERENCE",
  "authoritative_runtime": "A0_STRONG_SINGLE_HIERARCHICAL",
  "classification": [
    "CONSUMED_CROSS_BENCHMARK_DEVELOPMENT",
    "LIVE_DEVELOPMENT_EVALUATION",
    "NOT_EXTERNAL_VALIDATION",
    "NOT_PRIMARY_INFERENCE"
  ],
  "design_denominators": {
    "RCA100": 103,
    "candidate-3": 60,
    "candidate-4": 60,
    "candidate-5": 60
  },
  "design_read_boundary": "EXACT_283_RECORD_PREFIX_NO_TUNE_OR_REGRESSION_OUTCOME_PARSE",
  "evaluation_version": "hierarchical-a2-shadow-v1",
  "gate_supported": false,
  "gates": {
    "G0_A2_REFERENCE": {
      "accepted": false,
      "entity_layer_fold_pass_fraction": 1.0,
      "fault_family_fold_pass_fraction": 1.0,
      "g0_obss_net_retained_fraction": 1.0,
      "grouped_robustness": [
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F001",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F002",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 280,
          "held_out_group": "F004",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F005",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 277,
          "held_out_group": "F006",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 271,
          "held_out_group": "F007",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F009",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 278,
          "held_out_group": "F010",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F011",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F012",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 272,
          "held_out_group": "F014",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 272,
          "held_out_group": "F016",
          "root_damage": 1,
          "root_net_rescue": 25,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F018",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F020",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F022",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 279,
          "held_out_group": "F023",
          "root_damage": 1,
          "root_net_rescue": 25,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F025",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 271,
          "held_out_group": "F026",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 279,
          "held_out_group": "F029",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F031",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 275,
          "held_out_group": "F034",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 277,
          "held_out_group": "F036",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F039",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F050",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F051",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F052",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F056",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F057",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "cpu",
          "root_damage": 2,
          "root_net_rescue": 21,
          "root_rescue": 23
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "delay",
          "root_damage": 2,
          "root_net_rescue": 17,
          "root_rescue": 19
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "disk",
          "root_damage": 2,
          "root_net_rescue": 23,
          "root_rescue": 25
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "loss",
          "root_damage": 2,
          "root_net_rescue": 13,
          "root_rescue": 15
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "mem",
          "root_damage": 2,
          "root_net_rescue": 21,
          "root_rescue": 23
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "socket",
          "root_damage": 2,
          "root_net_rescue": 23,
          "root_rescue": 25
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 264,
          "held_out_group": "OPERATION",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 251,
          "held_out_group": "POD",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 58,
          "held_out_group": "SERVICE",
          "root_damage": 0,
          "root_net_rescue": 0,
          "root_rescue": 0
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 276,
          "held_out_group": "UNKNOWN",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 180,
          "held_out_group": "RCA100",
          "root_damage": 0,
          "root_net_rescue": 26,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 193,
          "held_out_group": "RE2-OB",
          "root_damage": 2,
          "root_net_rescue": 22,
          "root_rescue": 24
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 193,
          "held_out_group": "RE2-SS",
          "root_damage": 2,
          "root_net_rescue": 0,
          "root_rescue": 2
        }
      ],
      "model_calls": 1,
      "obss_aggregate": {
        "correct_override": 26,
        "denominator": 180,
        "final_exact_correct": 171,
        "final_service_correct": 171,
        "initial_exact_correct": 145,
        "initial_service_correct": 145,
        "override_count": 26,
        "pair_damage": 0,
        "pair_net_rescue": 4,
        "pair_rescue": 4,
        "root_damage": 0,
        "root_damage_rate": 0.0,
        "root_net_rescue": 26,
        "root_rescue": 26,
        "wrong_override": 0
      },
      "obss_fixtures": {
        "candidate-3": {
          "correct_override": 8,
          "denominator": 60,
          "final_exact_correct": 57,
          "final_service_correct": 57,
          "initial_exact_correct": 49,
          "initial_service_correct": 49,
          "override_count": 8,
          "pair_damage": 0,
          "pair_net_rescue": 1,
          "pair_rescue": 1,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 8,
          "root_rescue": 8,
          "wrong_override": 0
        },
        "candidate-4": {
          "correct_override": 6,
          "denominator": 60,
          "final_exact_correct": 57,
          "final_service_correct": 57,
          "initial_exact_correct": 51,
          "initial_service_correct": 51,
          "override_count": 6,
          "pair_damage": 0,
          "pair_net_rescue": 2,
          "pair_rescue": 2,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 6,
          "root_rescue": 6,
          "wrong_override": 0
        },
        "candidate-5": {
          "correct_override": 12,
          "denominator": 60,
          "final_exact_correct": 57,
          "final_service_correct": 57,
          "initial_exact_correct": 45,
          "initial_service_correct": 45,
          "override_count": 12,
          "pair_damage": 0,
          "pair_net_rescue": 1,
          "pair_rescue": 1,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 12,
          "root_rescue": 12,
          "wrong_override": 0
        }
      },
      "rca100": {
        "correct_override": 0,
        "denominator": 103,
        "final_exact_correct": 14,
        "final_service_correct": 19,
        "initial_exact_correct": 16,
        "initial_service_correct": 23,
        "override_count": 13,
        "pair_damage": 0,
        "pair_net_rescue": 0,
        "pair_rescue": 0,
        "root_damage": 2,
        "root_damage_rate": 0.125,
        "root_net_rescue": -2,
        "root_rescue": 0,
        "wrong_override": 13
      },
      "rejection_reasons": [
        "RCA100_ROOT_NET_RESCUE_BELOW_ZERO",
        "RCA100_FINAL_EXACT_BELOW_INITIAL",
        "RCA100_ROOT_DAMAGE_RATE_ABOVE_0_10",
        "RCA100_RESCUE_NOT_GREATER_THAN_DAMAGE"
      ]
    },
    "G1_EXACT_LAYER_A2": {
      "accepted": false,
      "entity_layer_fold_pass_fraction": 1.0,
      "fault_family_fold_pass_fraction": 1.0,
      "g0_obss_net_retained_fraction": 1.0,
      "grouped_robustness": [
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F001",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F002",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 280,
          "held_out_group": "F004",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F005",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 277,
          "held_out_group": "F006",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 271,
          "held_out_group": "F007",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F009",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 278,
          "held_out_group": "F010",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F011",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F012",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 272,
          "held_out_group": "F014",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 272,
          "held_out_group": "F016",
          "root_damage": 1,
          "root_net_rescue": 25,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F018",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F020",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F022",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 279,
          "held_out_group": "F023",
          "root_damage": 1,
          "root_net_rescue": 25,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F025",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 271,
          "held_out_group": "F026",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 279,
          "held_out_group": "F029",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F031",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 275,
          "held_out_group": "F034",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 277,
          "held_out_group": "F036",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F039",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F050",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F051",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F052",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F056",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F057",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "cpu",
          "root_damage": 2,
          "root_net_rescue": 21,
          "root_rescue": 23
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "delay",
          "root_damage": 2,
          "root_net_rescue": 17,
          "root_rescue": 19
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "disk",
          "root_damage": 2,
          "root_net_rescue": 23,
          "root_rescue": 25
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "loss",
          "root_damage": 2,
          "root_net_rescue": 13,
          "root_rescue": 15
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "mem",
          "root_damage": 2,
          "root_net_rescue": 21,
          "root_rescue": 23
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "socket",
          "root_damage": 2,
          "root_net_rescue": 23,
          "root_rescue": 25
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 264,
          "held_out_group": "OPERATION",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 251,
          "held_out_group": "POD",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 58,
          "held_out_group": "SERVICE",
          "root_damage": 0,
          "root_net_rescue": 0,
          "root_rescue": 0
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 276,
          "held_out_group": "UNKNOWN",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 180,
          "held_out_group": "RCA100",
          "root_damage": 0,
          "root_net_rescue": 26,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 193,
          "held_out_group": "RE2-OB",
          "root_damage": 2,
          "root_net_rescue": 22,
          "root_rescue": 24
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 193,
          "held_out_group": "RE2-SS",
          "root_damage": 2,
          "root_net_rescue": 0,
          "root_rescue": 2
        }
      ],
      "model_calls": 1,
      "obss_aggregate": {
        "correct_override": 26,
        "denominator": 180,
        "final_exact_correct": 171,
        "final_service_correct": 171,
        "initial_exact_correct": 145,
        "initial_service_correct": 145,
        "override_count": 26,
        "pair_damage": 0,
        "pair_net_rescue": 4,
        "pair_rescue": 4,
        "root_damage": 0,
        "root_damage_rate": 0.0,
        "root_net_rescue": 26,
        "root_rescue": 26,
        "wrong_override": 0
      },
      "obss_fixtures": {
        "candidate-3": {
          "correct_override": 8,
          "denominator": 60,
          "final_exact_correct": 57,
          "final_service_correct": 57,
          "initial_exact_correct": 49,
          "initial_service_correct": 49,
          "override_count": 8,
          "pair_damage": 0,
          "pair_net_rescue": 1,
          "pair_rescue": 1,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 8,
          "root_rescue": 8,
          "wrong_override": 0
        },
        "candidate-4": {
          "correct_override": 6,
          "denominator": 60,
          "final_exact_correct": 57,
          "final_service_correct": 57,
          "initial_exact_correct": 51,
          "initial_service_correct": 51,
          "override_count": 6,
          "pair_damage": 0,
          "pair_net_rescue": 2,
          "pair_rescue": 2,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 6,
          "root_rescue": 6,
          "wrong_override": 0
        },
        "candidate-5": {
          "correct_override": 12,
          "denominator": 60,
          "final_exact_correct": 57,
          "final_service_correct": 57,
          "initial_exact_correct": 45,
          "initial_service_correct": 45,
          "override_count": 12,
          "pair_damage": 0,
          "pair_net_rescue": 1,
          "pair_rescue": 1,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 12,
          "root_rescue": 12,
          "wrong_override": 0
        }
      },
      "rca100": {
        "correct_override": 0,
        "denominator": 103,
        "final_exact_correct": 14,
        "final_service_correct": 21,
        "initial_exact_correct": 16,
        "initial_service_correct": 23,
        "override_count": 4,
        "pair_damage": 0,
        "pair_net_rescue": 0,
        "pair_rescue": 0,
        "root_damage": 2,
        "root_damage_rate": 0.125,
        "root_net_rescue": -2,
        "root_rescue": 0,
        "wrong_override": 4
      },
      "rejection_reasons": [
        "RCA100_ROOT_NET_RESCUE_BELOW_ZERO",
        "RCA100_FINAL_EXACT_BELOW_INITIAL",
        "RCA100_ROOT_DAMAGE_RATE_ABOVE_0_10",
        "RCA100_RESCUE_NOT_GREATER_THAN_DAMAGE"
      ]
    },
    "G2_ROOT_ELIGIBLE_LAYER_A2": {
      "accepted": false,
      "entity_layer_fold_pass_fraction": 1.0,
      "fault_family_fold_pass_fraction": 1.0,
      "g0_obss_net_retained_fraction": 1.0,
      "grouped_robustness": [
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F001",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F002",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 280,
          "held_out_group": "F004",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F005",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 277,
          "held_out_group": "F006",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 271,
          "held_out_group": "F007",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F009",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 278,
          "held_out_group": "F010",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F011",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F012",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 272,
          "held_out_group": "F014",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 272,
          "held_out_group": "F016",
          "root_damage": 1,
          "root_net_rescue": 25,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F018",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F020",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F022",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 279,
          "held_out_group": "F023",
          "root_damage": 1,
          "root_net_rescue": 25,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F025",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 271,
          "held_out_group": "F026",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 279,
          "held_out_group": "F029",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F031",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 275,
          "held_out_group": "F034",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 277,
          "held_out_group": "F036",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F039",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F050",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F051",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F052",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F056",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F057",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "cpu",
          "root_damage": 2,
          "root_net_rescue": 21,
          "root_rescue": 23
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "delay",
          "root_damage": 2,
          "root_net_rescue": 17,
          "root_rescue": 19
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "disk",
          "root_damage": 2,
          "root_net_rescue": 23,
          "root_rescue": 25
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "loss",
          "root_damage": 2,
          "root_net_rescue": 13,
          "root_rescue": 15
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "mem",
          "root_damage": 2,
          "root_net_rescue": 21,
          "root_rescue": 23
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "socket",
          "root_damage": 2,
          "root_net_rescue": 23,
          "root_rescue": 25
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 264,
          "held_out_group": "OPERATION",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 251,
          "held_out_group": "POD",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 58,
          "held_out_group": "SERVICE",
          "root_damage": 0,
          "root_net_rescue": 0,
          "root_rescue": 0
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 276,
          "held_out_group": "UNKNOWN",
          "root_damage": 2,
          "root_net_rescue": 24,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 180,
          "held_out_group": "RCA100",
          "root_damage": 0,
          "root_net_rescue": 26,
          "root_rescue": 26
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 193,
          "held_out_group": "RE2-OB",
          "root_damage": 2,
          "root_net_rescue": 22,
          "root_rescue": 24
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 193,
          "held_out_group": "RE2-SS",
          "root_damage": 2,
          "root_net_rescue": 0,
          "root_rescue": 2
        }
      ],
      "model_calls": 1,
      "obss_aggregate": {
        "correct_override": 26,
        "denominator": 180,
        "final_exact_correct": 171,
        "final_service_correct": 171,
        "initial_exact_correct": 145,
        "initial_service_correct": 145,
        "override_count": 26,
        "pair_damage": 0,
        "pair_net_rescue": 4,
        "pair_rescue": 4,
        "root_damage": 0,
        "root_damage_rate": 0.0,
        "root_net_rescue": 26,
        "root_rescue": 26,
        "wrong_override": 0
      },
      "obss_fixtures": {
        "candidate-3": {
          "correct_override": 8,
          "denominator": 60,
          "final_exact_correct": 57,
          "final_service_correct": 57,
          "initial_exact_correct": 49,
          "initial_service_correct": 49,
          "override_count": 8,
          "pair_damage": 0,
          "pair_net_rescue": 1,
          "pair_rescue": 1,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 8,
          "root_rescue": 8,
          "wrong_override": 0
        },
        "candidate-4": {
          "correct_override": 6,
          "denominator": 60,
          "final_exact_correct": 57,
          "final_service_correct": 57,
          "initial_exact_correct": 51,
          "initial_service_correct": 51,
          "override_count": 6,
          "pair_damage": 0,
          "pair_net_rescue": 2,
          "pair_rescue": 2,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 6,
          "root_rescue": 6,
          "wrong_override": 0
        },
        "candidate-5": {
          "correct_override": 12,
          "denominator": 60,
          "final_exact_correct": 57,
          "final_service_correct": 57,
          "initial_exact_correct": 45,
          "initial_service_correct": 45,
          "override_count": 12,
          "pair_damage": 0,
          "pair_net_rescue": 1,
          "pair_rescue": 1,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 12,
          "root_rescue": 12,
          "wrong_override": 0
        }
      },
      "rca100": {
        "correct_override": 0,
        "denominator": 103,
        "final_exact_correct": 14,
        "final_service_correct": 21,
        "initial_exact_correct": 16,
        "initial_service_correct": 23,
        "override_count": 4,
        "pair_damage": 0,
        "pair_net_rescue": 0,
        "pair_rescue": 0,
        "root_damage": 2,
        "root_damage_rate": 0.125,
        "root_net_rescue": -2,
        "root_rescue": 0,
        "wrong_override": 4
      },
      "rejection_reasons": [
        "RCA100_ROOT_NET_RESCUE_BELOW_ZERO",
        "RCA100_FINAL_EXACT_BELOW_INITIAL",
        "RCA100_ROOT_DAMAGE_RATE_ABOVE_0_10",
        "RCA100_RESCUE_NOT_GREATER_THAN_DAMAGE"
      ]
    },
    "G3_CROSS_SOURCE_SUPPORTED_A2": {
      "accepted": false,
      "entity_layer_fold_pass_fraction": 1.0,
      "fault_family_fold_pass_fraction": 1.0,
      "g0_obss_net_retained_fraction": 0.07692307692307693,
      "grouped_robustness": [
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F001",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F002",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 280,
          "held_out_group": "F004",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F005",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 277,
          "held_out_group": "F006",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 271,
          "held_out_group": "F007",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F009",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 278,
          "held_out_group": "F010",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F011",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F012",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 272,
          "held_out_group": "F014",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 272,
          "held_out_group": "F016",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F018",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F020",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F022",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 279,
          "held_out_group": "F023",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F025",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 271,
          "held_out_group": "F026",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 279,
          "held_out_group": "F029",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F031",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 275,
          "held_out_group": "F034",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 277,
          "held_out_group": "F036",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F039",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F050",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F051",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F052",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F056",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F057",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "cpu",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "delay",
          "root_damage": 0,
          "root_net_rescue": 0,
          "root_rescue": 0
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "disk",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "loss",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "mem",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "socket",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 264,
          "held_out_group": "OPERATION",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 251,
          "held_out_group": "POD",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 58,
          "held_out_group": "SERVICE",
          "root_damage": 0,
          "root_net_rescue": 0,
          "root_rescue": 0
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 276,
          "held_out_group": "UNKNOWN",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 180,
          "held_out_group": "RCA100",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 193,
          "held_out_group": "RE2-OB",
          "root_damage": 0,
          "root_net_rescue": 0,
          "root_rescue": 0
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 193,
          "held_out_group": "RE2-SS",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        }
      ],
      "model_calls": 1,
      "obss_aggregate": {
        "correct_override": 2,
        "denominator": 180,
        "final_exact_correct": 147,
        "final_service_correct": 147,
        "initial_exact_correct": 145,
        "initial_service_correct": 145,
        "override_count": 2,
        "pair_damage": 0,
        "pair_net_rescue": 2,
        "pair_rescue": 2,
        "root_damage": 0,
        "root_damage_rate": 0.0,
        "root_net_rescue": 2,
        "root_rescue": 2,
        "wrong_override": 0
      },
      "obss_fixtures": {
        "candidate-3": {
          "correct_override": 0,
          "denominator": 60,
          "final_exact_correct": 49,
          "final_service_correct": 49,
          "initial_exact_correct": 49,
          "initial_service_correct": 49,
          "override_count": 0,
          "pair_damage": 0,
          "pair_net_rescue": 0,
          "pair_rescue": 0,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 0,
          "root_rescue": 0,
          "wrong_override": 0
        },
        "candidate-4": {
          "correct_override": 1,
          "denominator": 60,
          "final_exact_correct": 52,
          "final_service_correct": 52,
          "initial_exact_correct": 51,
          "initial_service_correct": 51,
          "override_count": 1,
          "pair_damage": 0,
          "pair_net_rescue": 1,
          "pair_rescue": 1,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 1,
          "root_rescue": 1,
          "wrong_override": 0
        },
        "candidate-5": {
          "correct_override": 1,
          "denominator": 60,
          "final_exact_correct": 46,
          "final_service_correct": 46,
          "initial_exact_correct": 45,
          "initial_service_correct": 45,
          "override_count": 1,
          "pair_damage": 0,
          "pair_net_rescue": 1,
          "pair_rescue": 1,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 1,
          "root_rescue": 1,
          "wrong_override": 0
        }
      },
      "rca100": {
        "correct_override": 0,
        "denominator": 103,
        "final_exact_correct": 16,
        "final_service_correct": 23,
        "initial_exact_correct": 16,
        "initial_service_correct": 23,
        "override_count": 0,
        "pair_damage": 0,
        "pair_net_rescue": 0,
        "pair_rescue": 0,
        "root_damage": 0,
        "root_damage_rate": 0.0,
        "root_net_rescue": 0,
        "root_rescue": 0,
        "wrong_override": 0
      },
      "rejection_reasons": [
        "OBSS_G0_NET_RETAINED_BELOW_0_50"
      ]
    },
    "G4_EXACT_LAYER_CROSS_SOURCE_A2": {
      "accepted": false,
      "entity_layer_fold_pass_fraction": 1.0,
      "fault_family_fold_pass_fraction": 1.0,
      "g0_obss_net_retained_fraction": 0.07692307692307693,
      "grouped_robustness": [
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F001",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F002",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 280,
          "held_out_group": "F004",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F005",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 277,
          "held_out_group": "F006",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 271,
          "held_out_group": "F007",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F009",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 278,
          "held_out_group": "F010",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F011",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F012",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 272,
          "held_out_group": "F014",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 272,
          "held_out_group": "F016",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F018",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F020",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 281,
          "held_out_group": "F022",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 279,
          "held_out_group": "F023",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F025",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 271,
          "held_out_group": "F026",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 279,
          "held_out_group": "F029",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F031",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 275,
          "held_out_group": "F034",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 277,
          "held_out_group": "F036",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F039",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F050",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F051",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F052",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F056",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 282,
          "held_out_group": "F057",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "cpu",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "delay",
          "root_damage": 0,
          "root_net_rescue": 0,
          "root_rescue": 0
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "disk",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "loss",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "mem",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_FAULT_FAMILY_OUT",
          "denominator": 253,
          "held_out_group": "socket",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 264,
          "held_out_group": "OPERATION",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 251,
          "held_out_group": "POD",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 58,
          "held_out_group": "SERVICE",
          "root_damage": 0,
          "root_net_rescue": 0,
          "root_rescue": 0
        },
        {
          "axis": "LEAVE_ONE_ENTITY_LAYER_OUT",
          "denominator": 276,
          "held_out_group": "UNKNOWN",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 180,
          "held_out_group": "RCA100",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 193,
          "held_out_group": "RE2-OB",
          "root_damage": 0,
          "root_net_rescue": 0,
          "root_rescue": 0
        },
        {
          "axis": "LEAVE_ONE_SYSTEM_OUT",
          "denominator": 193,
          "held_out_group": "RE2-SS",
          "root_damage": 0,
          "root_net_rescue": 2,
          "root_rescue": 2
        }
      ],
      "model_calls": 1,
      "obss_aggregate": {
        "correct_override": 2,
        "denominator": 180,
        "final_exact_correct": 147,
        "final_service_correct": 147,
        "initial_exact_correct": 145,
        "initial_service_correct": 145,
        "override_count": 2,
        "pair_damage": 0,
        "pair_net_rescue": 2,
        "pair_rescue": 2,
        "root_damage": 0,
        "root_damage_rate": 0.0,
        "root_net_rescue": 2,
        "root_rescue": 2,
        "wrong_override": 0
      },
      "obss_fixtures": {
        "candidate-3": {
          "correct_override": 0,
          "denominator": 60,
          "final_exact_correct": 49,
          "final_service_correct": 49,
          "initial_exact_correct": 49,
          "initial_service_correct": 49,
          "override_count": 0,
          "pair_damage": 0,
          "pair_net_rescue": 0,
          "pair_rescue": 0,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 0,
          "root_rescue": 0,
          "wrong_override": 0
        },
        "candidate-4": {
          "correct_override": 1,
          "denominator": 60,
          "final_exact_correct": 52,
          "final_service_correct": 52,
          "initial_exact_correct": 51,
          "initial_service_correct": 51,
          "override_count": 1,
          "pair_damage": 0,
          "pair_net_rescue": 1,
          "pair_rescue": 1,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 1,
          "root_rescue": 1,
          "wrong_override": 0
        },
        "candidate-5": {
          "correct_override": 1,
          "denominator": 60,
          "final_exact_correct": 46,
          "final_service_correct": 46,
          "initial_exact_correct": 45,
          "initial_service_correct": 45,
          "override_count": 1,
          "pair_damage": 0,
          "pair_net_rescue": 1,
          "pair_rescue": 1,
          "root_damage": 0,
          "root_damage_rate": 0.0,
          "root_net_rescue": 1,
          "root_rescue": 1,
          "wrong_override": 0
        }
      },
      "rca100": {
        "correct_override": 0,
        "denominator": 103,
        "final_exact_correct": 16,
        "final_service_correct": 23,
        "initial_exact_correct": 16,
        "initial_service_correct": 23,
        "override_count": 0,
        "pair_damage": 0,
        "pair_net_rescue": 0,
        "pair_rescue": 0,
        "root_damage": 0,
        "root_damage_rate": 0.0,
        "root_net_rescue": 0,
        "root_rescue": 0,
        "wrong_override": 0
      },
      "rejection_reasons": [
        "OBSS_G0_NET_RETAINED_BELOW_0_50"
      ]
    }
  },
  "live_shadow_executed": false,
  "new_external_data_accessed": false,
  "promotion_executed": false,
  "provider_calls": 0,
  "provider_objects_constructed": 0,
  "re2_tt_accessed": false,
  "regression_executed": false,
  "schema_version": "hierarchical-a2-shadow-v1.applicability-frontier.v1",
  "selected_gate": null,
  "verdict": "A2_APPLICABILITY_GATE_NOT_SUPPORTED_KEEP_A0"
}
```

# DTA v2.3.3 Domain-Bound Witness-Guard Evaluation

Measured terminal: `DTA_V233_DOMAIN_AND_GUARD_MIXED_RESULT`

- Execution count: `1`
- Cases / runs: `28` / `84`
- Provider model: `gpt-5.4-mini-2026-03-17`

| Arm | Novelty | Root | Domain | Top-2 | Conflict | False novel | Evidence refs | Calls | Tokens |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `V232_CONFLICT_AWARE_BASELINE` | 0.938 | 0.938 | 0.125 | 0.125 | 0.000 | 0.250 | 1.000 | 30 | 173070 |
| `V233_DOMAIN_BOUND` | 1.000 | 1.000 | 0.625 | 0.875 | 0.000 | 0.333 | 1.000 | 16 | 38568 |
| `V233_DOMAIN_BOUND_WITNESS_GUARD` | 1.000 | 1.000 | 0.625 | 0.875 | 1.000 | 0.000 | 1.000 | 17 | 40761 |

## Component interpretation

- Domain package: broad-domain accuracy `0.125` → `0.625`; Provider calls `30` → `16`.
- Guard increment: irreconcilable accuracy `0.000` → `1.000`; false-novel rate `0.333` → `0.000`; mean reads `1.429` → `1.500`.

These are fixed-set component comparisons, not claims of statistical significance.

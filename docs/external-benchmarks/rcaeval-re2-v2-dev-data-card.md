# RCAEval RE2 v2 Development Data Card

Status: `DEVELOPMENT_VISIBLE / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`

This data card covers only RE2-OB and RE2-SS. It records schema and mapping
evidence for development; it is not an external benchmark claim.

## Source bindings

- Dataset lock SHA-256: `e0cc4ef5c3414e457f3b04695059e352522adedbb13f6d1f7b9a531c2739a957`
- Indicator formula registry SHA-256: `51a8373e72e924151d9e8749ffc6b2959eadee59cc0b11510f9d8f6d6ed2455a`
- Cases audited: 180
- Telemetry-value use in split selection: No
- Provider calls: 0

## Metric schema and normalization

| System | Cases | Schema variants | Unique metric names | Canonical | Auxiliary | Unknown | Ambiguous | Raw truth-indicator coverage |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| RE2-OB | 90 | 34 | 86 | 67 | 19 | 0 | 0 | 90/90 (1.0000) |
| RE2-SS | 90 | 35 | 86 | 73 | 13 | 0 | 0 | 90/90 (1.0000) |

The registry uses exact case-sensitive service prefixes and suffixes.
Unknown suffixes remain `UNKNOWN`; ambiguous mappings remain `AMBIGUOUS`.
No Ground Truth is used to create runtime candidates.

## Live source verification

| System | Cases | Services | Faults | Metrics schemas | Logs schemas | Traces schemas | Extracted manifest match |
|---|---:|---:|---:|---:|---:|---:|---|
| RE2-OB | 90 | 5 | 6 | 34 | 2 | 1 | Yes |
| RE2-SS | 90 | 5 | 6 | 35 | 1 | 0 | Yes |

## Metric value quality

| System | Rows | Metric cells | Missing timestamps | Missing values | Nonfinite values |
|---|---:|---:|---:|---:|---:|
| RE2-OB | 129256 | 9482628 | 514 | 59684 | 2642 |
| RE2-SS | 129690 | 9935695 | 0 | 47013 | 39458 |

The versioned value policy preserves row order, drops rows with a missing
timestamp, fails closed on a nonfinite timestamp, and replaces each missing
or nonfinite metric value with its previous finite value or zero if none
exists. This matches the frozen v1 deterministic metric reader.

## Development boundary

- Overall raw truth-indicator coverage: 180/180 (1.0000)
- Formula selection uses only the frozen 60-case DESIGN split.
- DEV_VALIDATION metric values are not used for formula selection.
- Full case-level formula outcomes remain outside Git.
- Any later validation result remains development-only.

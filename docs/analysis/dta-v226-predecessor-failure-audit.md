# DTA v2.2.6 Predecessor Failure Audit

This is an offline source reproduction over the exact committed PR #67 public captures.
It does not rerun, edit, rescore, or reinterpret the frozen PR #67 study.
Docker and Provider calls: `0 / 0`.

## Current first failure

| Case | First failing stage | Strict sets | Metric payloads differ | Resources gaps on both targets |
|---|---|---:|---:|---:|
| `fault-map-a` | `RESOURCE_COMPARISON_SET_BUILD` | 0 | true | true |
| `fault-map-b` | `RESOURCE_COMPARISON_SET_BUILD` | 0 | true | true |
| `baseline-map-a` | `RESOURCE_COMPARISON_SET_BUILD` | 0 | true | true |
| `baseline-map-b` | `RESOURCE_COMPARISON_SET_BUILD` | 0 | true | true |

Source hypothesis disposition: `confirmed`.

All four snapshot runs and the two map-A live equivalents first fail at `RESOURCE_COMPARISON_SET_BUILD / RESOURCE_COMPARISON_SET_EMPTY`. The old strict `len(ambiguity_sets) == 1` gate sees zero sets because exact Metrics payloads make the target visibility signatures unequal, even though both targets have minimum-clause Resources-observable gaps and a target-complete bundle candidate exists.

## Flat first failure

| Case | Calls | Accepted turns | Reads | First failing stage | Narrow subtype |
|---|---:|---:|---:|---|---|
| `fault-map-a` | 2 | 1 | 1 | `PROVIDER_ACTION_SELECTION` | `UNRECOVERABLE_FROM_PRESERVED_BYTES` |
| `fault-map-b` | 4 | 3 | 3 | `PROVIDER_ACTION_SELECTION` | `UNRECOVERABLE_FROM_PRESERVED_BYTES` |
| `baseline-map-a` | 4 | 3 | 3 | `PROVIDER_ACTION_SELECTION` | `UNRECOVERABLE_FROM_PRESERVED_BYTES` |
| `baseline-map-b` | 4 | 3 | 3 | `PROVIDER_ACTION_SELECTION` | `UNRECOVERABLE_FROM_PRESERVED_BYTES` |

Every failed Flat call occurred before a Provider turn was accepted: calls equal accepted turns plus one, while every accepted nonterminal turn has a matching dispatched read. Therefore read-dispatch failure, Diagnosis evidence-ref failure, and terminal normalization failure are each exactly zero as first failures.

The preserved public result, private paired ledger, and execution output do not contain raw Provider output or safe parser validation codes. The remaining four failures cannot truthfully be split between read-request parse/bind and full-Diagnosis parse. Both categories are retained separately in JSON with `confirmed_count=0` and `possible_unresolved_count=4`; an additional exact count records four unresolved pre-acceptance Provider outputs.

Recoverability disposition: `partially confirmed`.

Audit SHA-256: `6564c222bce35324e41c289ddb13e115317b0b82dab26392c1e09623fa934b37`

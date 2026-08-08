# Single-first Adaptive v1 result

Final state: `SINGLE_FIRST_ADAPTIVE_V1_DESIGN_NOT_PASSED_READY_FOR_ALGORITHM_REVIEW`

## Result

Three candidate versions reached the required 12-case DESIGN smoke. Each smoke was fully terminalized with 12 `INVALID_SCHEMA` results, 12 Provider attempts, zero transport retries, and zero detected private-path hits. The conservative token upper bounds were 89,868, 89,506, and 88,871 respectively.

No candidate passed the Provider canary. The 60-case DESIGN evaluation was therefore not run, no candidate was selected or frozen, and DEV_VALIDATION remained unopened and unexecuted. The historical Strong Single DESIGN baseline remains 51/60 Root Service and 29/60 Pair; it is context only, not a newly executed comparison.

## Candidate sequence

| Candidate | Change entering smoke | Terminal result |
| --- | --- | --- |
| candidate-1 | Initial Single-first runtime and strict output validation | 0/12 completed; 12/12 invalid schema |
| candidate-2 | Candidate evidence references admitted by the initial visible-reference contract | 0/12 completed; 12/12 invalid schema |
| candidate-3 | Optional-output defaults, bounded deduplication, explicit field prompt, safe diagnostics | 0/12 completed; 12/12 invalid schema |

Candidate 3's bounded diagnostic was identical on all 12 cases: field path `$`, error class `ValueError`, constraint `validation_error`, error count 1. It confirms a local semantic validation rejection after a valid Provider response, but deliberately does not retain raw output or values.

## Safety and accounting

- Total across three smokes: 36 scheduled, 36 terminalized, 36 Provider attempts, zero retries.
- Schema/result retry: zero.
- TT accessed: no.
- DEV_VALIDATION schedule values accessed: no.
- DEV_VALIDATION case directories opened: no.
- Candidate freeze created: no.
- Merge, release, deployment, remediation, browser control, Docker, and GPU work: none.

## Required next action

Run algorithm review on the remaining initial semantic boundary and introduce field-specific safe diagnostic codes in a new, explicitly authorized evaluation version. Do not reinterpret this result as a DESIGN accuracy failure: accuracy evaluation never began because the Provider smoke did not pass.

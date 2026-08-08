# Single-first Adaptive v1 result

Final state: `BLOCKED`

Reason: `SHARED_SMOKE_DOWNSTREAM_SCHEMA_FAILURE_OUTSIDE_R2_INITIAL_INTERFACE_SCOPE`

## Disposition

The bounded Initial Diagnosis interface repair succeeded at its own boundary: all 12 new candidate-1 smoke runs completed Initial Diagnosis, with zero `INITIAL_*` failures. The new provider envelope contains one external evidence projection, does not send `canonical_evidence`, and derives visible services and evidence references from the exact input sent to the Provider.

The required shared smoke nevertheless did not pass. Seven of 12 runs completed end to end. Four stopped at Logs Specialist output validation and one stopped at Fusion output validation, all with the safe generic code `PROVIDER_OUTPUT_INVALID_SCHEMA`. The run used 30 Provider attempts, zero transport retries, zero semantic retries, and had zero detected private-path hits.

The authorized r2 path was limited to a precise, fixable shared Initial Diagnosis interface failure. No such failure occurred, so r2 was not run. The Gate was not relaxed, DESIGN was not started, and DEV_VALIDATION remained unopened.

## Preserved pre-fix evidence

The earlier 36 terminals remain unchanged and are classified as `PRE_FIX_INITIAL_INTERFACE_FAILURE`:

- three pre-fix candidate labels, each with 12/12 `INVALID_SCHEMA`;
- 36 scheduled and terminalized runs, 36 Provider attempts, and zero transport retries;
- failure stage `INITIAL_DIAGNOSIS / OUTPUT_VALIDATION`;
- not DESIGN-eligible and excluded from candidate selection;
- no old run ID was reused.

These failures are evidence of the former shared Initial Diagnosis interface, not three DESIGN candidate results.

## New shared smoke

| Boundary | Result |
| --- | --- |
| Candidate / domain | candidate-1 / `single-first-adaptive-v1-interface-fix-r1` |
| Scheduled / terminalized | 12 / 12 |
| Initial Diagnosis completed | 12 / 12 |
| End-to-end completed | 7 / 12 |
| `INITIAL_*` failures | 0 |
| Logs Specialist schema failures | 4 |
| Fusion schema failures | 1 |
| Provider attempts | 30 |
| Transport / semantic retries | 0 / 0 |
| Private-path hits | 0 |
| Conservative token upper bound | 53,069 |
| Gate | failed |

The safe records retain only operation type, status, and allowlisted failure codes. No raw invalid service, evidence reference, or Provider output is persisted in the public result.

## Evaluation boundary

- 60-case DESIGN: not run; iterations used: 0.
- Candidate selection and freeze: not performed.
- DEV_VALIDATION schedule values accessed: no.
- DEV_VALIDATION case directories opened: no.
- RE2-TT or any external holdout accessed: no.
- Validation reruns or result-driven tuning: none.

The historical Strong Single DESIGN baseline remains 51/60 Root Service and 29/60 Pair as context only. It was not rerun and is not presented as a new comparison.

## Required next action

Human algorithm review should decide whether a separately authorized follow-up may repair the newly exposed Logs Specialist and Fusion Provider-output schema boundaries. Do not call this a DESIGN accuracy result, do not reuse r1 IDs, and do not open DEV_VALIDATION before a candidate passes the unchanged shared-smoke gate.

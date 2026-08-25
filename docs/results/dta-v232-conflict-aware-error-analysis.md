# DTA v2.3.2 Conflict-Aware Successor Error Analysis

## Frozen result

The new fixed 24-case × 2-arm study executed exactly once and minted:

`DTA_V232_CONFLICT_AWARE_DISCOVERY_MIXED_RESULT`

This is an independent successor. It did not continue or rerun either consumed v2.3.1 attempt. Runtime exceptions, unmapped anomaly kinds, transport retries, and action-authority violations were all zero.

## Why the result is mixed

The treatment cleared every frozen mixed-result threshold:

- novelty recall improved from 4/14 (`0.286`) to 13/14 (`0.929`), a `+0.643` change;
- conflict-prone recall improved from 0/8 to 7/8 (`0.875`);
- evidence-ref validity was `1.000`;
- false-novel rate was 2/10 (`0.200`);
- action-authority violations were zero.

It did not satisfy the positive-effect contract for two independent reasons:

- broad-domain accuracy was 2/14 (`0.143`), below `0.55`;
- two irreconcilable controls were converted to novelty, above the allowed maximum of one.

The terminal therefore reflects a strong acquisition/novelty-recall signal without a sufficiently accurate mechanism-domain projection or safe irreconcilable-control boundary.

## Error groups

### Broad-domain projection

Root localization was 12/14 (`0.857`), but broad-domain accuracy was only 2/14. The Provider often retained the correct opaque root among its leading projection while choosing `UNKNOWN`, `RUNTIME`, or `DEPENDENCY` instead of the evaluator domain. The two correct broad-domain cases were the hidden service-unavailable cases `vx-209` and `vx-210`.

This separates two capabilities that must not be conflated: the successor usually localized the affected service, but it did not reliably classify the underlying fault domain.

### Irreconcilable controls

The strict arm preserved all three irreconcilable controls as non-novel (`3/3`). The treatment scored `0/3`:

- `vx-222` reached the two-repair protocol limit and terminated `PROVIDER_FAILED`;
- `vx-223` and `vx-224` emitted competing-hypothesis novelty reports.

Those two reports account for the full 2/10 false-novel denominator and for `true_conflict_converted_cases = 2`.

### Provider protocol failures

Two treatment arms terminated `PROVIDER_FAILED / PROTOCOL_FAILED` after the fixed two-repair bound:

- novelty case `vx-206`, which is the single treatment novelty-recall miss;
- irreconcilable control `vx-222`.

These terminals remain in the one-shot artifact. They were not repaired, retried beyond the frozen protocol, excluded, or rerun. Total protocol repairs were six, distributed across `vx-204` (1), `vx-206` (2), `vx-207` (1), and `vx-222` (2); exact-request transport retries were zero.

## Preserved strengths and boundaries

- Strict hard-conflict rate on novelty fell from 10/14 (`0.714`) to 0/14.
- Treatment produced nine competing-hypothesis reports; their evidence validity, alternative completeness, and unresolved-question completeness were each `1.000`.
- Registered-known accuracy remained 4/4 in both arms.
- No-Incident accuracy remained 3/3 in both arms.
- Mean discovery reads were `0.708` strict and `0.667` treatment.
- Provider calls were 4 strict and 19 treatment; total tokens were 126,357 and recorded Provider latency was 195,809.387 ms.
- Candidate Filter and action paths remained closed; Agent writes, Runbook executions, Docker calls, new live faults, and action-authority violations were all zero.

The study supports the frozen mixed result only. It does not establish production autonomy, safe remediation, general live-fault discovery, or a positive causal-effect terminal.

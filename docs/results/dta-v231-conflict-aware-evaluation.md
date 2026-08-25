# DTA v2.3.1 Conflict-Aware Discovery — Fixed Evaluation

Measured terminal: `DTA_V231_CONFLICT_AWARE_DISCOVERY_NOT_OBSERVED`

- Execution count: `1`
- Cases / runs: `24` / `48`
- Baseline / treatment novelty recall: `0.429` / `0.643`
- Recall improvement: `0.214`
- Conflict-prone baseline / treatment recall: `0.000` / `0.375`
- Non-conflict baseline / treatment recall: `1.000` / `1.000`
- Hard-conflict rate on novelty (baseline / treatment): `0.286` / `0.000`
- Treatment competing-report rate: `0.214`
- Treatment root localization: `0.571`
- Treatment broad-domain accuracy: `0.214`

## Conflict behavior

- Final conflict counts (none / coherent / resolvable / irreconcilable): `20` / `4` / `0` / `0`
- Discriminating-read execution rate: `0.286`
- Discriminating-read anomaly yield: `0.750`
- Post-read conflict-resolution rate: `1.000`
- Persistent-competition report rate: `0.750`

## Report quality and controls

- Treatment evidence-ref validity: `1.000`
- Residual-anomaly citation validity: `1.000`
- Competing-hypothesis evidence validity: `1.000`
- Leading-hypothesis root validity: `0.889`
- Alternative-hypothesis completeness: `1.000`
- Unresolved-question completeness: `1.000`
- Treatment false-novel rate: `0.100`
- Registered-known accuracy (baseline / treatment): `0.750` / `0.750`
- No-Incident accuracy (baseline / treatment): `1.000` / `1.000`
- Insufficient/conflict accuracy (baseline / treatment): `0.000` / `0.000`
- Known / No-Incident accuracy-drop cases: `0` / `0`
- True conflicts converted to novelty: `0`

## Cost and safety

- Mean discovery reads (baseline / treatment): `0.458` / `0.542`
- Provider calls (baseline / treatment): `8` / `6`
- Provider tokens (input / output / total): `47088` / `15717` / `62805`
- Provider latency: `105620.900 ms`
- Protocol repairs / transport retries: `3` / `0`
- Action-authority violations: `0`

The study used committed replay-derived bytes only. It did not call Docker, create a live fault, execute a Runbook, or grant Agent write authority.

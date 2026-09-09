# Product v0.4 minimal Payment live acceptance

Measured Product terminal: **RECOVERED**. Live execution is complete; independent review, final full tests, exact-head CI and merge are tracked separately in PR #102. This result alone is not a merge-completion claim.

The active contract is [the saved Goal](../../goals/EcomSRE_Product_v0.4_Minimal_Payment_Live_Acceptance_Goal.md), SHA-256 `290e2f2c7948522f3f642f16696e73fc77c5535374be992e5f2ec2ff5db5337b`.
Starting main: `cc941b51cbff9287b876be49652cd0ad83030474` (PRs #91–#94 merged).
Live source: `539824ee5c3b2497c890a52a7aaa1fff455b6b8e`; subsequent export, report and CI changes do not retroactively change the executed source.
Pinned upstream: `1755859a9de82c2e5e225be68abc401a5ebf2b4f`, Linux arm64.

## Observed result

The real Payment gRPC Charge path used the pinned Payment service and real flagd. The ten-service dependency set and each service's purpose, image identities and Compose commitment are in [live-result.json](live-result.json). A read-only host observer collected actual Payment responses, owned Docker resource metrics and signed ownership evidence. Prometheus supplied the supported metrics clause; OpenSearch, Jaeger, Checkout and unrelated Demo services were unnecessary for this clause.

| Stage | Actual evidence |
| --- | --- |
| Healthy | 194 direct Payment requests, zero errors after startup; active Product Baseline, 5/5 qualifying windows |
| Healthy diagnosis | `INSUFFICIENT_EVIDENCE`; logs, runtime and trace evidence unavailable to the Product diagnosis |
| Fault | `paymentFailure=100%`, changed configuration digest, final 30/30 probes failed as expected |
| Diagnosis | `CORE_KNOWN / payment / CONFIGURATION / CONFIGURATION_ERROR`, `configuration:change-and-error-metric` |
| Control | One deterministic Candidate, persisted Goal-authorized Approval, fresh state, Authorization, WriteIntent and dispatch |
| Restore | One gateway consumption, one real fixed Baseline restore, persisted `APPLIED` StepReceipt |
| Recovery window 1 | 08:04:16.074743–08:04:26.074743 UTC: 39 requests, 0 errors |
| Recovery window 2 | 08:04:33.722197–08:04:43.722197 UTC: 39 requests, 0 errors |
| Final | Current Product Recovery Verifier: `RECOVERED`; Provider/LLM calls: 0 |
| Cleanup | 10 containers, 3 networks and 2 volumes removed; remaining 0/0/0; non-owned resources unchanged; Baseline flag restored |

All times above are on 2026-09-09. Healthy requests and the active Baseline provide bounded healthy-state evidence under Goal §6. They do **not** convert `INSUFFICIENT_EVIDENCE` into `NO_INCIDENT`, or establish a complete healthy diagnosis across unavailable telemetry sources. Recovery observations explicitly use `DIRECT_PAYMENT_TRAFFIC` under [DEC-063](../../DECISIONS.md); existing policy thresholds and diagnosis rules are unchanged.

## Evidence and engineering history

[live-result.json](live-result.json) contains actual persisted Product objects and CAS recovery observations. The minimal verifier validates bindings and replays the existing recovery evaluator against those observations; it does not rerun Docker or independently reproduce the live experiment. Diagnosis support references were resolved from the private Product Evidence Store; public evidence publishes commitments without raw telemetry.

[engineering-attempts.json](engineering-attempts.json) preserves all seven attempts. Earlier startup, observation and integration failures remain failures. In particular, two earlier attempts performed a restore and persisted a receipt but remained `VERIFYING` without accepted recovery windows; later cleanup and the successful seventh attempt do not rewrite their Product states. Every attempt has a clean owned-resource cleanup result. Source-specific build images and private evidence remain retained; the cleanup claim covers containers, networks and volumes.

[HUMAN_BRIEF.md](HUMAN_BRIEF.md) states the Chinese review boundary. [evidence-manifest.json](evidence-manifest.json) binds the public result files. Private raw telemetry, control tokens, private paths and unredacted Docker inventory stay outside Git.

## Verification and scope

Run `PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.ci.verify_product_v040_minimal_payment` to verify the public manifest and replay recovery. Full pytest, Ruff, mypy, the existing Product v0.4 verifier and this verifier remain required for the final reviewed head. CI includes the minimal verifier.

- Read scope: current Product, pinned upstream source, historical #95–#101 implementation and results as read-only references, exact local Docker inventory and private attempt evidence.
- Write scope: the saved Goal; dedicated minimal-payment scripts, configuration, tests, verifier and result files; `docs/DECISIONS.md`; the recovery observation provenance literal in `execution_contracts.py`; its existing Executor tests; and `.github/workflows/agent-mainline.yml` to enforce verification. Core diagnosis rules and thresholds remain unchanged.
- Frozen scope: existing historical evidence, registry and historical-binding commitments, pinned upstream commit.
- Final repository scope: every tracked change against the starting main, including generated public results. Private telemetry and control credentials stay outside Git.

## Claim boundary

This proves one pinned local minimal Payment configuration-fault recovery through the current Product control and verification chain. It does not prove production self-healing, all-fault recovery, full-Demo stability, cross-environment generalization, exactly-once external side effects, or production readiness without human governance. Approval records map the user's prior Goal authorization; Codex did not independently authorize the action.

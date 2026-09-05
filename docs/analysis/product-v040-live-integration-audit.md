# Product v0.4 PR-E integration preparation

Status: PREPARATION_FAILED_BEFORE_FORMAL_FREEZE, corrected source awaiting fresh validation. No measured Payment result exists.
PR-A through PR-D are merged; PR-D merge is
`cc941b51cbff9287b876be49652cd0ad83030474`.

The exact profile is `config/product-v040/live-profile.v1.json`. It fixes one
Payment fault campaign, one accepted attempt, one durable write intent, at most
one forward dispatch, and two new 60-second recovery windows. Healthy baseline
collection uses five successful windows; the business oracle checks a returned
order and the frozen cart item independently of HTTP status. Provider calls are
zero. The only registry entry remains Payment configuration rollback.

`scripts/product/run_payment_v040.py prepare` builds a tracked-source-only ARM64
Product image, admits the frozen 28-image Demo proof, starts owned local services,
collects a healthy Active Baseline, measures actual network denial, and runs a
NO_INCIDENT control. It creates no approval, attempt or fault. The separate
`campaign` phase requires exact-head CI, full local validation, independent
pre-execution review, fresh ownership and baseline checks before publishing a
create-once manifest and consuming the single fault intent. No frozen campaign
can be rerun. Cleanup never issues a baseline control write.

The observer uses a separate host process with fixed operations and per-window
create-once sentinels. A signed request must bind the reserved ordinal, APPLIED
receipt, policy, current VERIFYING state and lease before probes begin. A failed
or cancelled window is consumed. API and Worker have neither control credentials
nor control routes; the executor has network mode none. A dedicated observation
proxy exposes fixed read routes. All lifecycle/export CLIs hold one nonblocking
OS file lock, and a live observer prevents cleanup.

Public export requires all four Product database writer roles to be stopped.
Residual owned resources force BLOCKED with actual counts. Historical non-owned
changes and unknown states remain visible. Protocol failures and incomplete
windows cannot become complete negative measurements. The public verifier binds
real Git commit/tree/blob content, the full measured source set, build input
hashes, profile, registry, typed receipt/windows and public artifact digests.
It labels absent live evidence PRE_EXECUTION_ONLY.

## Fixed negative safety suite

These are offline contract cases, except that the healthy control is additionally
required in the actual preparation. None claims live Kafka recovery.

| Required case | Regression surface | Expected result |
|---|---|---|
| NO_INCIDENT | test_candidates.py: test_non_core_zero_candidates | no candidate |
| OPEN_WORLD | test_candidates.py: test_non_core_zero_candidates | no candidate |
| KafkaBacklog EXTENSION_KNOWN | test_candidates.py: test_kafka_backlog_extension_contract_never_projects_payment_runbook | no candidate |
| Insufficient evidence | test_candidates.py: test_non_core_zero_candidates | denied |
| Ambiguous root | test_candidates.py: test_ineligible_semantic_diagnosis | denied |
| Expired/revoked approval | test_approval_api.py: test_active_gate_rejects_expired_revoked_future_and_mismatched | denied |
| Changed state | test_authorization.py: test_state_denials_persist_zero_authority; test_state_drift_immediately_before_intent_denies | zero intent |
| Duplicate request | test_authorization.py: test_fresh_state_authorizes_once_and_replay_survives_provider_loss; test_executor.py | one durable authority and no second dispatch |

## Validation boundaries

Preparation source review: PASS / Must Fix 0 / Claim Accuracy PASS. This is a
read-only independent source review; actual pre-execution evidence remains a
separate gate. Focused Product tests passed (292 before the final added cases).
Final exporter regression: 9 passed. Git-binding/host/result regressions: 26
passed. Full Ruff passed; scoped mypy passed for 264 files and mainline mypy for
698 files. Docker Compose config resolution passed with five services, internal
API/Worker networking, networkless executor and no named volumes; no container
was started. Full repository testing and exact-head CI remain pending.

Initial test fixture errors and reviewer findings were corrected before live:
coherent negative terminal construction, omitted build registry, pinned Docker
context, checkout business oracle, observer cancellation, output classification,
Git provenance, cross-object binding and historical cleanup truth preservation.
No runtime success, recovery, or production capability is claimed here.

The first no-fault preparation failed on an unquoted tmpfs flow list and was
cleanly removed with zero formal allowance consumed. The failure is retained
in the Payment error analysis. Corrected source validates resolved mounts before
startup and sets private-process file creation permissions, with actual-mode
checks at preparation and freeze. Independent repair review: PASS / Must Fix 0
/ Claim Accuracy PASS; 24 targeted tests independently passed.

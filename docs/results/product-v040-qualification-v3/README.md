# Runtime Qualification Successor v3

Goal: [frozen execution contract](../../goals/EcomSRE_Product_v0.4_Runtime_Qualification_Successor_v3_Goal.md),
SHA-256 `bb5373899fc8523007b6e9cec0b865403450e4bf4f15b81426ae20f6e1f2deeb`.
Base: PR #98 `7d1c975e3450901a6b09fcbca8c0cd82f24cfc14`.
Original Goal: preserved `goal_impossible_checkpoint`, never success.

**Terminal: `BLOCKED_PRE_EXECUTION / SANDBOX_UNHEALTHY`; cleanup
`MANUAL_INTERVENTION_REQUIRED / COMMAND_DRIFT`; future formal campaign
`WITHHOLD`.** One v3 no-fault allowance is consumed. The formal Payment allowance
remains unconsumed. This publishes a preserved failed qualification, not runtime
readiness or successful remediation.

Qualification `c6a70e54d58b4df5a1325886304b4844` ran exactly once on
`1fe68b22a1cbc22818c99b5c418569aca175bcf3`, after 6996 offline tests passed
(21 skipped), repository Ruff, mypy (702 mainline and 10 v3 files), independent
Must Fix 0 / Claim Accuracy PASS, and [exact-head CI](https://github.com/Raidriar7170/EcomSRE-Agent/actions/runs/34207995089).
The later publication commit changes result documentation only.

The [raw result](qualification-result.json), [first divergence](qualification-first-divergence.json)
and [296-file evidence index](qualification-evidence-index.json) are byte-identical
copies of immutable private artifacts. Thirty-two of 63 stages were accepted;
copy-up, six fresh effective OOM observations, the access/sentinel checks and
explicit probe stop passed. Full healthy control, Active Baseline, NO_INCIDENT,
isolation and later Product checks were not reached.

The saved cleanup inventory retains **28 running sandbox containers, one stopped
probe, three networks and six volumes**. No cleanup mutation intent was reached.
See [exact retained bindings](retained-resources.json) and the [qualification
cleanup receipt](qualification-cleanup-receipt.json). These are terminal
observations, not a claim that later live state has been re-inspected. No cleanup
bypass or retry is authorized.

Source plus saved inventory supports an inference that the legacy health guard
counted the stopped probe as a 29th owned container against 28 expected services.
A contemporaneous health-return trace was not sealed. Cleanup independently
rejects plan `None` command/entrypoint values against image-inherited runtime
defaults. This does not prove an unhealthy Kafka service or malicious drift;
[terminal adjudication](terminal-adjudication.json) preserves those limits.

[Final independent review](final-independent-review.json): evidence disposition
Must Fix 0 / Claim Accuracy PASS; **future formal campaign WITHHOLD**. All eleven
formal-action counters are zero, with the explicit basis that Product startup
and its database creation were not reached. The legacy raw
`one_shot_allowance_consumed: false` means the formal allowance; the separate
`no_fault_qualification_consumption` records v3 consumption = 1.

Phase 1 exact retained-resource cleanup is **CLEAN**, independently accepted.
The three raw automatic receipts remain unchanged: (1) zero operations before
an image-list ordering mismatch; (2) one exact stop before a mount-list ordering
mismatch; (3) seven exact removals followed by an image reference-count mismatch.
All image and mount entries were preserved; image `Containers: 1 -> 0` matches
the exact probe removal. The separate append-only `CleanupReceipt.json` records
verified resource absence and explicit postflight adjudication. No earlier
terminal, PR #98 result, raw inspection or fuse was rewritten. No further
cleanup mutation of the PR #98 retained set is needed. Before every removal, two complete inventories,
exact identities, pinned image, labels, mounts and overlapping attachments were
validated. Raw private inventories are indexed for independent verification.

Phase 2 introduces `DOCKER_STAGE_AWARE_FINGERPRINT_V3` in an independent namespace.
Affected driver/journal methods are versioned copies so historical v1/v2 source
and policy bytes remain unchanged. All v3 journal admission, seed-reader checks,
writer census and cleanup comparisons use the same explicit comparator.
Raw inspected dictionaries are never normalized in place.

Only the owned stopped-before-start probe may undergo the predeclared
`HostConfig.OomKillDisable: false -> null` transition at `AFTER_PROBE_START`.
Birth CID, Created, image/descriptor, labels, all HostConfig keys, mounts,
security labels and command identity remain exact. Additional changes, missing
fields, `true`, wrong-stage changes and restarts fail closed. Other containers
remain exact comparisons from their own authorized births.

The start transition requires fresh two-pass read-only cgroup-v2 configuration
and process evidence, tied to the same daemon, CID, Created, StartedAt, host PID
and restart count. Measurements include memory.max, memory.oom.group, absence
of the legacy v1 memory.oom_control interface, and PID1 oom_score_adj plus
UID/GID/groups/capabilities/NoNewPrivs. `memory.oom.group` controls group killing;
it is not itself an OOM-disable bit. This is observed effective configuration,
not an induced-OOM or memory-pressure recovery claim. Running checkpoints take
fresh measurements. Stopped checkpoints can refer only to the last verified
pre-stop measurement and sealed explicit stop intent, never a claimed fresh
measurement of a dead process.

Builtin `none` network endpoint membership is a separate explicit lifecycle
rule: the exact owned probe endpoint may appear while running and disappear
on stop. CID/name/endpoint ID and empty IP/MAC must match raw container and
network views. The endpoint binds once at `AFTER_PROBE_START`; consistent
replacement in both later views is rejected. Network identity and every other field remain exact. The raw
network inventory changes; only the separately checked semantic view is equal.

Phase 3 admission and the one permitted attempt are complete; the blocked
result is frozen. No formal Payment fault, campaign manifest, remediation
candidate/approval/AttemptAuthorization/WriteIntent, remediation execution,
Provider call, recovery window or merge into main was performed. The frozen
Goal cannot mint another no-fault attempt. Publication ends this bounded Goal;
Product v0.4 remains blocked and the retained resources need separately scoped
manual intervention. PR #95–#98 are unchanged.

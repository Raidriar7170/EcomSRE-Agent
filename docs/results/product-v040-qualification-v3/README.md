# Runtime Qualification Successor v3

Goal: [frozen execution contract](../../goals/EcomSRE_Product_v0.4_Runtime_Qualification_Successor_v3_Goal.md),
SHA-256 `bb5373899fc8523007b6e9cec0b865403450e4bf4f15b81426ae20f6e1f2deeb`.
Base: PR #98 `7d1c975e3450901a6b09fcbca8c0cd82f24cfc14`.
Original Goal: preserved `goal_impossible_checkpoint`, never success.

Phase 1 exact retained-resource cleanup is **CLEAN**, independently accepted.
The three raw automatic receipts remain unchanged: (1) zero operations before
an image-list ordering mismatch; (2) one exact stop before a mount-list ordering
mismatch; (3) seven exact removals followed by an image reference-count mismatch.
All image and mount entries were preserved; image `Containers: 1 -> 0` matches
the exact probe removal. The separate append-only `CleanupReceipt.json` records
verified resource absence and explicit postflight adjudication. No earlier
terminal, PR #98 result, raw inspection or fuse was rewritten. No further
cleanup mutation is needed. Before every removal, two complete inventories,
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
network views. Network identity and every other field remain exact. The raw
network inventory changes; only the separately checked semantic view is equal.

Phase 3 remains gated until clean full offline tests, exact-head `Agent mainline`
CI and independent `Must Fix 0 / Claim Accuracy PASS /
FRESH_NOFAULT_QUALIFICATION_ALLOW`. One new Goal-bound common-repository fuse
permits one new qualification identity and evidence root; it cannot be replayed.
No formal Payment fault, campaign manifest, remediation candidate/approval/
AttemptAuthorization/WriteIntent, remediation execution, Provider call,
recovery window or merge into main is authorized. Future-formal ALLOW/WITHHOLD
is a recommendation only. Stop after publishing the one no-fault result and
independent final decision.

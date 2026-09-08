# EcomSRE Product v0.4 Runtime Qualification Successor v3 Goal

Goal ID: `ecomsre-product-v040-runtime-qualification-successor-v3`

Authority: the user's explicit v3 instruction in the current task. This frozen
Goal is its execution contract. Starting point: PR #98 exact head
`7d1c975e3450901a6b09fcbca8c0cd82f24cfc14`. No later observation changes authority.

## Preserved predecessor

Close the original EcomSRE Product v0.4 Bounded Remediation Goal as a preserved
`goal_impossible_checkpoint` under its frozen authority, never as success.
Its no-fault allowance was consumed; PR #98 ended
`BLOCKED_PRE_EXECUTION / WRITER_CONTAINER_DRIFT`; cleanup ended
`MANUAL_INTERVENTION_REQUIRED`. The same authorization hash cannot mint a retry.
The separate formal Payment campaign allowance remains unconsumed.

PR #95, #96, #97 and #98 and their raw evidence/results are immutable history.
Do not rewrite or reinterpret them. New records describe subsequent actions
without replacing predecessor evidence, manifests, fuses or terminal markers.

## Ordered phases

### 1. Exact retained-resource cleanup

Cleanup authority is limited to PR #98 qualification
`ebbcf1e4e3e740a296f294fb9ab6047c` and exactly these seven retained resources:

- Container ID `c1e1f654931ebe062ae3253727adf884c28ea580781bef5976f322ac06bed66f`,
  name `ecomsre-v040-ebbcf1e4e3e740a296f294fb9ab6047c-volume-probe`.
- Volume `ecomsre-v040-ebbcf1e4e3e740a296f294fb9ab6047c-astronomy-db-data`.
- Volume `ecomsre-v040-ebbcf1e4e3e740a296f294fb9ab6047c-jaeger-data`.
- Volume `ecomsre-v040-ebbcf1e4e3e740a296f294fb9ab6047c-prometheus-data`.
- Volume `ecomsre-v040-ebbcf1e4e3e740a296f294fb9ab6047c-qualification-kafka-config`.
- Volume `ecomsre-v040-ebbcf1e4e3e740a296f294fb9ab6047c-qualification-kafka-data`.
- Volume `ecomsre-v040-ebbcf1e4e3e740a296f294fb9ab6047c-qualification-kafka-secrets`.

Authenticate local daemon/context and verify exact IDs, names, image identities,
labels, mounts and every attachment before every removal. Bind expected identity
to hash-verified PR #98 retained evidence, not arbitrary current observations.
An exact stop of the retained probe may precede its non-force removal. Verify
again after stop and before removal. Never remove an attached volume.
No prune, wildcard cleanup, `compose down -v`, unknown-resource mutation, host
configuration change or privilege escalation. Any identity mismatch stops this
phase. Preserve immutable raw before/after inventories and an append-only
CleanupReceipt, including any partial failure and non-owned comparison.
Phase 2 requires verified complete cleanup of this exact retained set.

### 2. Offline stage-aware fingerprint repair and independent review

Preserve raw Docker inspect evidence. Add a versioned, stage-aware semantic
fingerprint instead of raw full-HostConfig equality across lifecycle transitions.
Any permitted `HostConfig.OomKillDisable` false/null normalization must be
predeclared and limited to this field, require the same container identity and
all other security-critical fields, and have an effective runtime OOM-policy
measurement. Unknown or additional changes remain fail-closed. No global
normalization, dropping unknown fields, observed-value promotion or retry loop.

Retain exact image-path provenance and v2 access protections: owner identity,
group exclusion, complete process/attachment census, stable image and mount
bindings, freshly owned volumes, bounded sentinel, append-only stage inventory
and first divergence. Tests must reject unauthorized drift and demonstrate the
permitted lifecycle transition only with matching effective OOM evidence.

Before Phase 3 require all of: clean full offline tests, exact-head CI, and an
independent read-only review recording Must Fix 0 / Claim Accuracy PASS and
explicit fresh-no-fault admission. Freeze source, policy, image/Compose/stage
bindings and implementation/review digests. Missing evidence blocks runtime.
No Product, Demo or Kafka qualification startup during this offline phase.
Any necessary read-only measurement protocol must be frozen before use; never
inject an OOM, fault or load to manufacture effective-policy evidence.

### 3. Exactly one fresh no-fault runtime qualification

Only after Phase 2 gates pass, consume one new common-repository fuse bound to
this Goal and exact reviewed source. Use one new qualification identity, one new
append-only evidence root and new owned resources. No PR #98 resource reuse and
no reuse of earlier authorization hashes. The fuse is create-once across all
worktrees; a failed attempt is consumed, not repaired and rerun.

Run only no-fault qualification: full prebound environment/stage plan, raw and
semantic snapshots, effective OOM-policy verification at required lifecycle
gates, exact Kafka copy-up provenance, runtime identity/groups and writer/mount
census, bounded create/read/ownership/delete/absence sentinel in fresh volumes,
then bounded warmup, frozen 30/30 healthy control, connector/capability checks,
Active Baseline, NO_INCIDENT, remediation-disabled and network-denial checks.
Never advance after a gate failure. Cleanup remains exact owned-resource work
with freshly verified identity; stop on mismatch and retain failure evidence.

Publish the immutable result, first divergence, counters, inventory and cleanup
receipts, source/policy/evidence digests, tests and exact-head CI, plus final
independent `ALLOW` or `WITHHOLD` for a future formal campaign. This recommendation
never authorizes a formal campaign in this Goal. Stop after publication.

## Permanent non-authorizations

This Goal does not authorize a formal Payment fault; formal campaign manifest;
RemediationCandidate; approval or AttemptAuthorization; WriteIntent; remediation
execution; Provider calls; recovery windows; or merge of stacked Product v0.4
PRs into main. All corresponding formal-action counters must remain zero and the
formal campaign allowance remains unconsumed. Do not run the formal Payment
campaign even if the no-fault qualification passes.

## Scope and evidence

Read scope: repository contracts, frozen upstream and cached image provenance,
PR #95–#98 evidence and the exact local inventories required by the phases.
Write scope: this new Goal, new v3 scripts/config/tests/results and any narrowly
required CI integration; new private v3 evidence; the exact authorized cleanup
set in Phase 1 and separately gated fresh owned resources in Phase 3. Publication
and exact-head CI use a new successor branch; no historical PR branch is updated.
Frozen scope: all predecessor Goal/PR evidence, v1/v2 code and policies, upstream
commit `1755859a9de82c2e5e225be68abc401a5ebf2b4f`, image commitments and old fuses.
Final repository scope: complete tracked delta from the exact PR #98 head;
untracked caches/dependencies are excluded, declared raw evidence is included.
New Goal bytes are frozen before cleanup and referenced by SHA-256 thereafter.

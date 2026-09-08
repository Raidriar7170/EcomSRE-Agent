# Goal: EcomSRE-Agent Product v0.4 — PR #99 Retained Docker Resources Cleanup Only

> **Repository**: `Raidriar7170/EcomSRE-Agent`  
> **Goal ID**: `ecomsre-product-v040-v3-retained-cleanup-only-v1`  
> **Starting point**: PR #99 exact publication head `ea466d7ca14ac4a0a0c1f7680507747dbe67b06e`  
> **Starting branch**: `codex/product-v040-runtime-qualification-v3`  
> **Required successor branch**: `codex/product-v040-v3-retained-cleanup-only`  
> **Required PR base**: `codex/product-v040-runtime-qualification-v3`  
> **Execution scope**: exact cleanup of the retained PR #99 qualification resources only  
> **Qualification ID**: `c6a70e54d58b4df5a1325886304b4844`  
> **Formal Payment campaign authority**: `NONE`  
> **Product remediation authority**: `NONE`  
> **Provider / LLM calls inside Product**: `0`  
> **Positive terminal**: `ECOMSRE_PRODUCT_V040_V3_RETAINED_CLEANUP_COMPLETE`  
> **Updated**: 2026-09-08

---

## 中文摘要

本 Goal 只处理 PR #99 无故障资格验证失败后遗留的 Docker 资源：

```text
29 个容器
  - 28 个 OTel Sandbox 容器
  - 1 个已停止 Kafka volume probe
3 个 owned networks
6 个 owned named volumes
```

它不修复 Live Harness，不重新运行 no-fault qualification，不启动 Product，不注入 Payment 故障，不创建 Candidate / Approval / Authorization，不执行恢复，不创建 Recovery Window，也不合并 PR #95–#99。

清理权限只能来自 PR #99 已封存的 retained-resource allowlist。任何名称相同但 ID 不同的替代资源、未知资源、标签/镜像/挂载不一致、非 owned attachment 或 daemon/context 漂移都必须立即停止。禁止 `prune`、通配符、`docker compose down -v` 和 `docker rm -f`。

---

# 0. Activation and Frozen-Contract Semantics

This file is a draft Goal Contract until the user explicitly activates it.

An unambiguous activation is:

```text
Execute EcomSRE Product v0.4 PR #99 Retained Cleanup Only Goal v1 from the
exact PR #99 publication head, and perform only the exact Docker cleanup
operations authorized by that Goal.
```

On activation, Codex must:

1. run `git fetch origin --prune`;
2. verify that PR #99 head is exactly
   `ea466d7ca14ac4a0a0c1f7680507747dbe67b06e`;
3. create a new clean branch/worktree from that exact commit;
4. compute and persist the SHA-256 of this Goal file;
5. bind the exact repository tree, current local Docker context, daemon identity,
   activation timestamp, and retained-resource source artifacts;
6. freeze every authority, non-goal, command allowlist, cleanup cardinality,
   terminal, and Definition of Done below;
7. stop at one of:

```text
goal_complete_checkpoint
cleanup_blocked_checkpoint
safety_checkpoint
human_boundary_checkpoint
```

A phase completion is not a terminal. Codex should continue autonomously inside
this exact scope until one of the four terminals is reached.

The activation is prior authorization for the exact cleanup mutations named in
this document. It is not authorization for production, remote infrastructure,
new resource creation, fault injection, remediation, general Docker cleanup,
or any resource absent from the frozen allowlist.

---

# 1. Mission

Remove only the exact Docker resources retained by PR #99 qualification
`c6a70e54d58b4df5a1325886304b4844`, while proving that:

```text
- every removed resource belongs to the frozen retained set;
- no replacement or unknown resource is adopted;
- no non-owned resource is stopped, removed, reconfigured, or restored;
- the old PR #99 blocked result and cleanup failure remain immutable history;
- the final retained-resource count is zero;
- the operation is independently reviewable from immutable receipts.
```

The mission is cleanup, not recovery of the Product v0.4 Goal.

---

# 2. Accepted Starting Facts

Treat the following as historical facts. Verify their files and hashes, but do
not rewrite their meaning.

## 2.1 PR #99 result

PR #99 is a preserved failed no-fault qualification:

```text
runtime terminal: BLOCKED_PRE_EXECUTION / SANDBOX_UNHEALTHY
cleanup terminal: MANUAL_INTERVENTION_REQUIRED / COMMAND_DRIFT
completed stages: 32 / 63
future formal campaign: WITHHOLD
formal action counters: all 0
formal Payment allowance: unconsumed
```

The original cleanup rejected command identity before creating any cleanup
mutation intent. That was correct under the frozen v3 contract and must remain
unchanged.

## 2.2 Retained resources

The authoritative public retained-resource source is:

```text
path:
  docs/results/product-v040-qualification-v3/retained-resources.json
commit:
  ea466d7ca14ac4a0a0c1f7680507747dbe67b06e
Git blob SHA:
  8ad3ee775cb13b933312c9909e1d20cfc5b7667d
qualification_id:
  c6a70e54d58b4df5a1325886304b4844
raw capture:
  host/cleanup/000-inventory.json
raw capture file SHA-256:
  263b43e61d6e6e736065701cf7b23e367da19cbd126811f831d990bb2fe4a168
```

The authoritative resource cardinality in that artifact is:

```text
containers: 29
running sandbox containers: 28
stopped probe containers: 1
networks: 3
volumes: 6
```

This artifact is an immutable terminal observation, not proof that every
resource is still present at activation time.

## 2.3 Historical evidence preservation

All of the following are frozen:

```text
PR #95, #96, #97, #98 and #99 branches and result artifacts
PR #99 qualification-result.json
PR #99 qualification-first-divergence.json
PR #99 qualification-cleanup-receipt.json
PR #99 retained-resources.json
PR #99 private qualification evidence and indexes
all predecessor Goal files, fuses, policies and terminal records
```

A new cleanup result may describe later state. It may not replace or edit any
historical byte.

---

# 3. Authoritative Cleanup Allowlist

## 3.1 General rule

The exact allowlist is the complete resource set encoded in the hash-verified
`retained-resources.json` above.

Do not reconstruct authority from:

```text
- name prefixes;
- Compose project labels alone;
- qualification labels alone;
- current `docker ps` output;
- current resource names;
- a new Compose expansion;
- an operator's memory;
- an observed replacement resource.
```

Every mutation target must first resolve to one exact retained-record identity.

## 3.2 Human-readable container summary

The retained set contains one stopped probe:

```text
ecomsre-v040-c6a70e54d58b4df5a1325886304b4844-volume-probe
```

It also contains exactly these 28 Sandbox service roles:

```text
accounting
ad
astronomy-db
cart
checkout
currency
email
flagd
flagd-ui
frontend
frontend-proxy
fraud-detection
grafana
image-provider
jaeger
kafka
load-generator
opamp-server
opensearch
otel-collector
payment
product-catalog
prometheus
quote
recommendation
shipping
telemetry-docs
valkey-cart
```

This role list is for review only. Full container IDs, image bindings, labels,
mounts and raw inspection commitments must come from the authoritative artifact.

## 3.3 Exact networks

Only the following networks may be removed, and only after their current IDs,
names, labels and endpoint sets are validated against the retained artifact:

```text
8268f64f2d2fc1b64dbff0ec081c7aa0cec354169e39c4b4756ebd68489b179f
  ecomsre-live-sandbox-v1-default

b5a0761c5ad0fa742c2c86b7eea2fdb47bd1af5b687faa5417e8576d5e4b2ca3
  ecomsre-product-v040_remediation-observation

bb27e2b218b80dbf7d71fff6adf210656f70242e85216ee6a0012684d141b04a
  ecomsre-product-v040-default
```

## 3.4 Exact volumes

Only these six volume names may be removed:

```text
ecomsre-v040-c6a70e54d58b4df5a1325886304b4844-astronomy-db-data
ecomsre-v040-c6a70e54d58b4df5a1325886304b4844-jaeger-data
ecomsre-v040-c6a70e54d58b4df5a1325886304b4844-prometheus-data
ecomsre-v040-c6a70e54d58b4df5a1325886304b4844-qualification-kafka-config
ecomsre-v040-c6a70e54d58b4df5a1325886304b4844-qualification-kafka-data
ecomsre-v040-c6a70e54d58b4df5a1325886304b4844-qualification-kafka-secrets
```

## 3.5 Exact ownership anchors

Expected retained-resource ownership includes:

```text
qualification ID:
  c6a70e54d58b4df5a1325886304b4844

Sandbox ID where applicable:
  e477da43-27e7-4c55-8491-1d45cda03000

Sandbox Compose project where applicable:
  ecomsre-live-sandbox-v1

Product Compose project where applicable:
  ecomsre-product-v040
```

These anchors are necessary but not sufficient. They must be combined with the
exact retained IDs, names, images, mounts and network bindings.

---

# 4. Scope

## 4.1 Read scope

Codex may read:

```text
- the exact PR #99 repository tree;
- PR #95–#99 public evidence and Goal files;
- the exact private PR #99 qualification evidence required to validate hashes;
- the current local Docker context and daemon information;
- current container, network, volume and image inspection data;
- the frozen Compose inputs and pinned image configuration needed to derive
  effective Entrypoint/Cmd semantics;
- current attachment and endpoint information for the exact retained set;
- current non-owned resource inventory for before/after comparison.
```

## 4.2 Repository write scope

Create only additive cleanup-specific files under paths such as:

```text
docs/goals/EcomSRE_Product_v0.4_V3_Retained_Cleanup_Only_Goal.md
scripts/product/cleanup_v040_v3_retained/**
tests/product_v040/cleanup_v3_retained/**
docs/results/product-v040-v3-retained-cleanup/**
docs/external-reviews/product-v040-v3-retained-cleanup-*.md
```

A narrowly required cleanup verifier may be added under:

```text
scripts/ci/verify_product_v040_v3_retained_cleanup.py
```

Do not modify `src/ecomsre/**`, Product migrations, remediation contracts,
qualification v1/v2/v3 implementations, historical result files, or upstream.

## 4.3 Docker mutation scope

The only allowed Docker mutations are:

```text
- stop an exact currently running retained container;
- remove an exact stopped retained container;
- remove an exact retained network after proving it has no endpoints;
- remove an exact retained volume after proving it has no attachments.
```

No other Docker mutation is authorized.

## 4.4 Private filesystem scope

New cleanup evidence may be written to one new append-only private cleanup root.

Do not delete or edit:

```text
- any prior private qualification root;
- the PR #99 retained raw capture;
- any prior DB, evidence index, result or receipt;
- repository worktrees or branches;
- host configuration or Docker Desktop settings.
```

---

# 5. Permanent Non-Goals

This Goal does not authorize:

```text
- starting or restarting any container;
- creating any container, network or volume;
- building, pulling, tagging or deleting images;
- running Docker Compose up/down;
- `docker compose down -v`;
- `docker system prune`, `docker container prune`, `docker network prune`,
  `docker volume prune` or `docker image prune`;
- wildcard, prefix-based or label-only deletion;
- `docker rm -f` or `docker kill`;
- editing Docker Desktop or daemon settings;
- restoring, replacing, disconnecting or mutating any non-owned resource;
- deleting host bind directories or retained evidence;
- Product API/Worker startup;
- Kafka/Product no-fault qualification;
- traffic warmup or 30/30 healthy control;
- Baseline construction;
- NO_INCIDENT diagnosis;
- Payment fault injection;
- formal campaign manifest creation;
- RemediationCandidate, Approval, AttemptAuthorization or WriteIntent creation;
- executor invocation, Payment flag write, StepReceipt or Recovery Window;
- Provider calls;
- reopening or rerunning any consumed qualification;
- changing the v3 fingerprint policy;
- fixing `SANDBOX_UNHEALTHY` for a new run;
- merging PR #95–#99 or this cleanup PR into `main`;
- closing historical Draft PRs unless separately requested.
```

Useful work outside these boundaries must be deferred.

---

# 6. Safety Invariants

## 6.1 Exact retained identity

A resource is eligible for cleanup only when all applicable identity-critical
fields agree with the frozen retained record:

```text
resource kind
exact full ID or exact volume name
exact name
qualification ID
Sandbox/Product ownership labels
Compose project and service role where applicable
image ID / pinned platform digest
mount type, source identity, destination and RW mode
attached owned network IDs
network driver/internal/ingress identity
volume driver/options
```

## 6.2 Current observation is not authority

Current Docker inspection may confirm or reject a retained identity. It may not
mint a new cleanup target.

The following must stop cleanup:

```text
- retained ID absent but the retained name points to a different ID;
- retained volume name resolves to unexpected labels or options;
- exact retained network name resolves to a different ID;
- any image, mount, ownership label or network binding mismatch;
- an unknown resource carrying the same qualification ID;
- an extra endpoint or attachment not attributable to the exact retained set;
- incomplete or raced list/inspect capture;
- Docker context or daemon identity drift during cleanup.
```

## 6.3 Allowed lifecycle variation

The cleanup identity may tolerate only ordinary lifecycle-state variation that
does not change resource ownership or configuration, including:

```text
Running / Paused / Restarting / Exited
PID
StartedAt / FinishedAt
ExitCode
Health output
RestartCount
```

Such variation must be recorded. It must not be used to authorize a replacement
resource.

Do not start an exited retained container.

## 6.4 No force and no takeover

A stop failure does not authorize `kill` or forced removal.

An identity mismatch does not authorize adoption, relabeling, disconnection,
repair, or manual recreation.

## 6.5 Append-only receipts

Before every mutation, persist an immutable intent containing:

```text
resource identity
validated retained-record digest
current inspect digest
allowed exact command
expected postcondition
daemon/context binding
UTC and monotonic timestamps
```

After every mutation, persist a separate immutable receipt. Never overwrite an
intent or prior receipt.

## 6.6 No hidden cleanup effects

Every Docker mutation command must target exactly one resource.

Batch deletion, shell glob expansion and label-filter mutation are forbidden.

---

# 7. Cleanup-Specific Command Identity Adjudication

The old v3 cleanup failed because its plan represented image-inherited commands
as `None`, while actual containers had effective image Entrypoint/Cmd values.
This Goal may resolve that discrepancy only for cleanup ownership validation.
It must not rewrite the old plan or terminal.

## 7.1 Required three-layer command model

For every retained container, derive and persist:

```text
compose_declared_entrypoint
compose_declared_command
image_default_entrypoint
image_default_command
resolved_effective_entrypoint
resolved_effective_command
current_actual_entrypoint
current_actual_command
```

The effective values must be derived deterministically from the exact frozen
Compose input and exact pinned image configuration:

```text
if Compose explicitly overrides entrypoint:
    effective entrypoint = Compose entrypoint
else:
    effective entrypoint = Image.Config.Entrypoint

if Compose explicitly overrides command:
    effective command = Compose command
else:
    effective command = Image.Config.Cmd
```

Do not promote current observed command values into the expected plan.

## 7.2 Command admission

A retained container remains cleanup-eligible only if its actual command and
entrypoint match the independently derived effective values.

Any other command difference must stop before mutation with:

```text
CLEANUP_EFFECTIVE_COMMAND_MISMATCH
```

## 7.3 Probe fingerprint boundary

For the stopped volume probe, retain the already preserved v3 interpretation of
the single predeclared `HostConfig.OomKillDisable: false -> null` lifecycle
normalization. Do not generalize it to other fields or containers.

The cleanup process may rely on the frozen v3 effective OOM evidence; it must not
restart the probe to remeasure it.

---

# 8. Allowed Docker Command Surface

## 8.1 Read-only commands

The cleanup implementation may invoke only bounded forms of:

```text
docker context show
docker context inspect <exact-current-context>
docker info --format <fixed-format>
docker version --format <fixed-format>
docker ps -aq --no-trunc
docker container inspect <exact full IDs>
docker network ls -q
docker network inspect <exact full IDs>
docker volume ls -q
docker volume inspect <exact names>
docker image inspect <exact retained image references or digests>
docker ps -aq --no-trunc --filter volume=<exact-volume-name>
```

Fixed read-only commands may be repeated to detect races. No arbitrary command
string may be supplied by a model or HTTP input.

## 8.2 Mutating commands

Only these exact command shapes are allowed:

```text
docker container stop --time 30 <exact-full-retained-container-ID>
docker container rm <exact-full-retained-container-ID>
docker network rm <exact-full-retained-network-ID>
docker volume rm <exact-full-retained-volume-name>
```

Constraints:

```text
- one target per command;
- full container/network ID, never a prefix;
- exact volume name, never a prefix;
- no `--force`;
- no shell interpolation;
- no mutation by label filter;
- no compose command;
- no retry after an ambiguous or unknown outcome.
```

---

# 9. Repository and PR Workflow

## 9.1 Branching

Create one clean worktree and branch from exact PR #99 head:

```text
base commit:
  ea466d7ca14ac4a0a0c1f7680507747dbe67b06e

branch:
  codex/product-v040-v3-retained-cleanup-only
```

Open one stacked Draft PR whose base is:

```text
codex/product-v040-runtime-qualification-v3
```

Keep it:

```text
Draft / REVIEW_REQUIRED
```

Do not merge it.

## 9.2 Frozen pre-mutation implementation head

Before any Docker mutation:

1. implement only the cleanup-specific loader, identity adjudication, journal,
   command allowlist and verifier;
2. add deterministic fake-transport tests;
3. commit the Goal and implementation;
4. run all required offline validation;
5. obtain an independent read-only pre-mutation review;
6. record and freeze the exact implementation head and tree;
7. execute cleanup only from that exact reviewed implementation.

No cleanup-relevant source file may change after the first mutation intent.

Publication-only result files may be added afterward.

---

# 10. Offline Tests Required Before Mutation

Tests must use fake Docker transports and must prove at least:

```text
1. Exact retained record + matching current resource is admitted.
2. Same name with different container/network ID is rejected.
3. Same qualification label on an unknown resource is rejected.
4. Missing retained resource with no name replacement is recorded as
   ALREADY_ABSENT, not adopted or recreated.
5. Image drift is rejected.
6. Mount source, destination, type or RW drift is rejected.
7. Network identity drift is rejected.
8. Volume label/driver/options drift is rejected.
9. Extra volume attachment is rejected.
10. Extra network endpoint is rejected.
11. Incomplete/raced captures are rejected.
12. Daemon/context drift is rejected.
13. Compose-null + pinned-image default resolves to the correct effective
    command.
14. Observed command values cannot become authority.
15. An unrelated command difference is rejected.
16. The probe's exact predeclared OomKillDisable normalization is narrow.
17. A running retained container receives at most one stop intent.
18. A stopped retained container receives no start intent.
19. Container removal occurs only after stopped-state proof.
20. Network removal occurs only with zero endpoints.
21. Volume removal occurs only with zero attachments.
22. No mutation command can target more than one resource.
23. No force, prune, wildcard or compose mutation command can be constructed.
24. A partial failure creates immutable evidence and stops later mutations.
25. Final success requires all exact retained resources absent.
26. Non-owned comparison permits only removal of the exact retained probe
    endpoint from builtin `none`, if that endpoint is present before cleanup.
```

Required validation before mutation:

```text
focused cleanup tests: PASS
Ruff on changed Python files: PASS
mypy on changed Python files: PASS
git diff --check: PASS
Goal and retained-artifact hash verifier: PASS
historical evidence integrity: PASS
independent review: PASS / Must Fix 0 / Claim Accuracy PASS
```

A full repository suite is optional because `src/ecomsre/**` is frozen, but any
failure in the focused cleanup suite blocks execution.

---

# 11. Pre-Mutation Runtime Preflight

Create one new private append-only cleanup root. Do not reuse a prior journal.

## 11.1 Bind local authority

Record:

```text
Docker context name and immutable inspection
Docker endpoint type
Docker daemon identity
Server version and platform
host architecture
activation Goal SHA-256
cleanup implementation head/tree
retained-resource artifact blob SHA
retained raw-capture SHA-256
qualification ID
```

Only the local Docker Desktop context previously used for the retained resources
is eligible. Remote contexts, SSH contexts and production daemons are forbidden.

## 11.2 Load the frozen allowlist

Verify the Git blob SHA and parse the complete retained set.

Require exactly:

```text
29 container records
3 network records
6 volume records
qualification_id = c6a70e54d58b4df5a1325886304b4844
```

Duplicate IDs, duplicate names or malformed records block cleanup.

## 11.3 Capture current inventory twice

Perform a complete list/inspect capture twice around no mutation.

Require:

```text
same daemon/context
same IDs in both enumerations
same identity-critical fields
complete inspect results
no unknown retained-label resource
```

Lifecycle changes may be recorded under Section 6.3; identity-critical changes
are blockers.

## 11.4 Classify every retained resource

Each frozen retained resource must become exactly one of:

```text
PRESENT_MATCHING
ALREADY_ABSENT
REPLACED_OR_DRIFTED
```

Rules:

```text
PRESENT_MATCHING:
  exact identity exists and all cleanup identity gates pass.

ALREADY_ABSENT:
  exact identity does not exist and no resource currently uses the retained
  name or claims to replace that identity.

REPLACED_OR_DRIFTED:
  retained ID/name association changed, or any identity-critical field differs.
```

`REPLACED_OR_DRIFTED` immediately stops before mutation.

`ALREADY_ABSENT` is not claimed as a removal by this Goal. Record the absence and
unknown external attribution, then continue with other exact resources.

## 11.5 Freeze current non-owned baseline

Capture the current non-owned inventory immediately before mutations.

The execution comparison is against this new pre-cleanup baseline, not against
the historical PR #99 non-owned snapshot. Historical differences may be
reported but cannot be silently attributed to this Goal.

---

# 12. Exact Cleanup Sequence

The sequence is mandatory.

## Phase 1 — Remove the stopped retained probe

1. verify the exact retained probe identity again;
2. require that it is stopped;
3. inspect builtin `none` network membership;
4. if the exact probe endpoint is present, bind its exact EndpointID and
   container ID as the only expected non-owned-network membership change;
5. persist one remove intent;
6. execute:

```text
docker container rm <exact-full-probe-ID>
```

7. verify the exact probe ID and name are absent;
8. verify that only its exact endpoint disappeared from builtin `none`;
9. persist the remove receipt.

If the probe is unexpectedly running, do not start or force-remove it. It may be
stopped once using the exact allowed stop command, then reinspected before
removal.

## Phase 2 — Stop the 28 retained Sandbox containers

Derive a deterministic reverse dependency order from the frozen Compose graph.
Use service name as the stable tie-breaker.

For each `PRESENT_MATCHING` running Sandbox container:

1. revalidate daemon/context;
2. re-inspect the exact full ID;
3. revalidate cleanup identity and effective command;
4. persist the stop intent;
5. execute exactly one bounded stop command;
6. verify the same full ID now exists and is stopped;
7. persist the stop receipt.

For a retained container already stopped, record `STOP_NOT_REQUIRED`.

On the first stop failure, unknown outcome, ID change or identity mismatch:

```text
stop all further mutations
preserve exact partial-cleanup state
emit cleanup_blocked_checkpoint
```

Do not kill or force-remove it.

## Phase 3 — Remove all 28 Sandbox containers

After every present retained Sandbox container is proven stopped, remove them in
the same deterministic order.

For each container:

1. revalidate exact ID and identity;
2. require stopped state;
3. persist one remove intent;
4. remove the exact full ID without force;
5. verify exact ID and retained name are absent;
6. persist one remove receipt.

Do not continue to networks until all 29 retained containers are either:

```text
REMOVED_BY_THIS_GOAL
or
ALREADY_ABSENT_BEFORE_MUTATION
```

## Phase 4 — Remove the three retained networks

For each exact retained network:

1. revalidate exact ID, name, driver, labels and configuration;
2. require `Containers == {}` / zero endpoints;
3. ensure no current container references its network ID;
4. persist one remove intent;
5. remove by exact full network ID;
6. verify exact ID and retained name are absent;
7. persist one remove receipt.

Recommended deterministic order:

```text
1. ecomsre-product-v040_remediation-observation
2. ecomsre-product-v040-default
3. ecomsre-live-sandbox-v1-default
```

Any endpoint blocks that network removal. Do not use `docker network disconnect`.

## Phase 5 — Remove the six retained volumes

For each exact retained volume:

1. revalidate exact name, labels, driver and options;
2. inspect every current container attachment;
3. require zero attachments, including stopped and non-owned containers;
4. persist one remove intent;
5. remove by exact full volume name;
6. verify the volume is absent;
7. persist one remove receipt.

Use the exact ordered list from Section 3.4.

Do not backfill, copy, mount, start a helper container, or inspect volume content
by creating a new container. Existing retained evidence is the preservation
boundary.

## Phase 6 — Final inventory and closure

Capture current Docker inventory twice.

Require:

```text
all 29 retained container identities absent
all 3 retained network identities/names absent
all 6 retained volume names absent
no replacement resource at any retained name
no newly created resource by this Goal
no image deletion
no unauthorized non-owned difference
stable daemon/context across final captures
```

Compare non-owned resources against the new pre-cleanup baseline.

The only allowed raw difference on a non-owned network is removal of the exact
retained probe endpoint from builtin `none`, if it existed before mutation.
Network identity and configuration must remain unchanged.

---

# 13. Cleanup Cardinality

Maximum authorized effects:

```text
new Docker resources created: 0
containers started/restarted: 0
container stop intents: at most 29
container removals: at most 29
network removals: at most 3
volume removals: at most 6
image mutations: 0
non-owned stop/remove/configuration mutations: 0
Product Provider calls: 0
formal faults: 0
formal candidates: 0
approvals: 0
attempt authorizations: 0
write intents: 0
remediation executor invocations: 0
remediation writes: 0
step receipts: 0
recovery windows: 0
formal campaign executions: 0
```

A resource already absent does not consume a mutation count.

Each resource may receive at most one successful removal intent under this Goal.

---

# 14. Failure and Ambiguity Semantics

## 14.1 Command returns non-zero

Capture bounded stdout/stderr, current daemon/context, and exact post-command
resource state.

Do not automatically retry a mutating command.

If the postcondition is unambiguous despite a client error, record the direct
observation and stop for independent adjudication. Do not continue based on an
assumption.

## 14.2 Daemon restarts or becomes unavailable

Stop immediately. Preserve the last completed receipt and capture current state
when the daemon becomes readable, using read-only commands only.

Do not resume mutation under this Goal.

## 14.3 Resource disappears before its mutation

If exact ID/name is absent and no replacement exists, record:

```text
ALREADY_ABSENT_DURING_CLEANUP
attribution: UNKNOWN
removed_by_this_goal: false
```

Stop for review if disappearance occurred after an intent but before a receipt,
because the mutation outcome is ambiguous.

## 14.4 Replacement or unknown resource

Stop without mutation. Do not clean a same-name replacement.

## 14.5 Partial cleanup

A partial cleanup is a valid preserved result, not success.

Publish remaining exact resources and required next authority. Do not rewrite or
rerun this Goal.

---

# 15. Evidence Requirements

## 15.1 Private append-only evidence

Create one new private root containing at least:

```text
activation.json
goal-contract.json
implementation-binding.json
retained-allowlist.json
retained-allowlist-verification.json
effective-command-adjudication.json
pre-cleanup-inventory-1.json
pre-cleanup-inventory-2.json
pre-cleanup-nonowned-baseline.json
mutation-intents/*.json
mutation-receipts/*.json
post-cleanup-inventory-1.json
post-cleanup-inventory-2.json
final-cleanup-result.json
private-evidence-index.json
```

Files are create-once. Use mode `0600`; directories use `0700`.

## 15.2 Public tracked evidence

Publish safe summaries under:

```text
docs/results/product-v040-v3-retained-cleanup/
```

Required files:

```text
README.md
cleanup-result.json
CleanupReceipt.json
resource-disposition.json
nonowned-comparison.json
effective-command-adjudication.json
evidence-index.json
verification.json
independent-review.md
HUMAN_BRIEF.md
```

Do not publish raw credentials, host-private paths beyond already public source
facts, Docker socket details, secrets or unbounded logs.

## 15.3 Per-resource receipt fields

Each mutation receipt must include:

```text
resource_kind
retained_identity
retained_record_digest
current_pre_mutation_digest
intent_sha256
exact command shape with sensitive paths redacted where required
started_at / ended_at
monotonic start/end
client exit status
bounded output digest
postcondition
post_mutation_observation_digest
daemon/context binding
outcome
previous_receipt_sha256
receipt_sha256
```

---

# 16. Independent Review

## 16.1 Pre-mutation review

A separate read-only reviewer must verify:

```text
- exact PR #99 base and Goal SHA;
- authoritative retained artifact hash and counts;
- command allowlist cannot target arbitrary resources;
- effective command is derived from frozen Compose + image config;
- current observations cannot mint authority;
- already-absent and replacement semantics are fail closed;
- non-owned comparison scope is correct;
- no force/prune/compose mutation exists;
- focused tests pass;
- Must Fix = 0;
- Claim Accuracy = PASS;
- exact retained cleanup = ALLOW.
```

No mutation may begin without this explicit `ALLOW`.

## 16.2 Final review

A separate read-only reviewer must verify:

```text
- every mutation target belonged to the allowlist;
- command and mutation cardinality remained within bounds;
- per-resource intent and receipt chains are complete;
- all retained resources are absent for a success claim;
- preexisting absences are not claimed as Goal removals;
- non-owned before/after comparison is accurate;
- historical PR #99 evidence remains unchanged;
- all Product/formal-action counters remain zero;
- cleanup result and limitations are accurately stated.
```

Required disposition:

```text
PASS / Must Fix 0 / Claim Accuracy PASS
```

for the positive completion terminal.

---

# 17. Terminals

## 17.1 `goal_complete_checkpoint`

Emit only when every Definition of Done item passes.

```yaml
terminal: goal_complete_checkpoint
product_terminal: ECOMSRE_PRODUCT_V040_V3_RETAINED_CLEANUP_COMPLETE
goal_id: ecomsre-product-v040-v3-retained-cleanup-only-v1
goal_sha256:
starting_head: ea466d7ca14ac4a0a0c1f7680507747dbe67b06e
qualification_id: c6a70e54d58b4df5a1325886304b4844
resource_disposition:
  containers:
    expected: 29
    removed_by_goal:
    already_absent:
    remaining: 0
  networks:
    expected: 3
    removed_by_goal:
    already_absent:
    remaining: 0
  volumes:
    expected: 6
    removed_by_goal:
    already_absent:
    remaining: 0
non_owned_mutations: 0
new_docker_resources: 0
formal_action_counters: all_zero
review:
  verdict: PASS
  must_fix: 0
  claim_accuracy: PASS
pr_disposition: Draft / REVIEW_REQUIRED
```

## 17.2 `cleanup_blocked_checkpoint`

Emit when the exact cleanup cannot continue safely under this Goal.

Valid examples:

```text
RESOURCE_REPLACED
RESOURCE_IDENTITY_DRIFT
UNKNOWN_QUALIFICATION_RESOURCE
CLEANUP_EFFECTIVE_COMMAND_MISMATCH
UNEXPECTED_ATTACHMENT
UNEXPECTED_NETWORK_ENDPOINT
DAEMON_IDENTITY_DRIFT
RACED_OR_INCOMPLETE_CAPTURE
STOP_FAILED
REMOVE_FAILED
MUTATION_OUTCOME_UNKNOWN
PARTIAL_CLEANUP_REQUIRES_NEW_AUTHORITY
```

Required report:

```yaml
terminal: cleanup_blocked_checkpoint
reason_code:
last_completed_receipt:
mutations_completed:
remaining_exact_resources:
non_owned_state:
daemon_state:
historical_evidence_preserved:
required_successor_authority:
```

Do not rerun or broaden cleanup after this terminal.

## 17.3 `safety_checkpoint`

Emit immediately if continuing may affect a resource not in the exact allowlist,
requires force/prune, or cannot distinguish a retained resource from a
replacement.

## 17.4 `human_boundary_checkpoint`

Emit when progress requires changing:

```text
- starting PR/head;
- retained allowlist;
- exact mutation command surface;
- allowed cardinality;
- treatment of a replacement or unknown resource;
- non-owned mutation policy;
- force/kill/prune behavior;
- Definition of Done.
```

---

# 18. Definition of Done

Every item is mandatory for a positive terminal.

## Contract and history

- [ ] PR #99 starting head is exactly bound.
- [ ] Goal file SHA-256 and activation record are persisted.
- [ ] `retained-resources.json` Git blob SHA is verified.
- [ ] retained raw-capture SHA-256 is bound.
- [ ] qualification ID and exact cardinalities are verified.
- [ ] PR #95–#99 evidence and results are byte-unchanged.

## Implementation safety

- [ ] cleanup code is additive and outside `src/ecomsre/**`.
- [ ] exact target allowlist is loaded only from the frozen retained artifact.
- [ ] command construction cannot use arbitrary names, prefixes, labels or
      current observations as authority.
- [ ] effective command adjudication uses frozen Compose + image config.
- [ ] force, kill, prune, compose-down and wildcard cleanup are impossible.
- [ ] all focused tests pass.
- [ ] Ruff, mypy and diff check pass.
- [ ] independent pre-mutation review records ALLOW with Must Fix 0.

## Cleanup execution

- [ ] all mutation intents and receipts are append-only.
- [ ] every mutation targets exactly one frozen retained resource.
- [ ] no container is started or restarted.
- [ ] every running retained container is stopped without force.
- [ ] every present retained container is removed without force.
- [ ] every retained network is removed only after zero endpoints.
- [ ] every retained volume is removed only after zero attachments.
- [ ] no image is mutated.
- [ ] no new Docker resource is created.

## Final state

- [ ] all 29 retained container identities/names are absent.
- [ ] all 3 retained network identities/names are absent.
- [ ] all 6 retained volume names are absent.
- [ ] no same-name replacement is present.
- [ ] non-owned identities and configuration are unchanged from the new
      pre-cleanup baseline, except the exact allowed probe-endpoint removal.
- [ ] all Product/formal-action counters remain zero.
- [ ] final private and public evidence indexes verify.
- [ ] independent final review records PASS / Must Fix 0 / Claim Accuracy PASS.
- [ ] cleanup PR remains Draft / REVIEW_REQUIRED and unmerged.

---

# 19. Final Human Report

The final response must include:

```text
Summary
Starting Head and Goal SHA
Authoritative Retained Set
Pre-Mutation Review
Effective Command Adjudication
Resources Removed by This Goal
Resources Already Absent Before Mutation
Remaining Resources
Non-Owned Before/After Comparison
Mutation Cardinality
Tests and Verification
Independent Review
Historical Evidence Preservation
Remaining Limitations
PR / Branch Disposition
```

Do not claim:

```text
- Product v0.4 remediation success;
- no-fault qualification success;
- Payment recovery;
- production readiness;
- that every absent resource was removed by this Goal;
- that old PR #99 cleanup succeeded;
- that this cleanup authorizes any future live campaign.
```

---

# 20. Copyable Activation Prompt

```text
Read and execute:

docs/goals/EcomSRE_Product_v0.4_V3_Retained_Cleanup_Only_Goal.md

Treat it as the frozen authoritative Goal Contract.

Start only from PR #99 exact publication head:
ea466d7ca14ac4a0a0c1f7680507747dbe67b06e

I explicitly authorize only the exact owned-local Docker cleanup mutations in
that Goal for qualification:
c6a70e54d58b4df5a1325886304b4844

The exact allowlist must come from the hash-verified PR #99
retained-resources.json artifact. This authorization permits bounded stop and
non-force removal of those exact retained containers, and removal of those exact
retained networks and volumes only after their gates pass.

It does not authorize creation/start/restart of resources, Docker Compose,
force/kill/prune, mutation of non-owned resources, Product startup, no-fault
qualification, Payment fault injection, remediation, Provider calls, formal
campaign work, historical evidence edits, or merge into main.

Preserve PR #95–#99 as immutable history. Stop on any replacement, identity
mismatch, unknown attachment/endpoint, raced capture, daemon/context drift,
mutation ambiguity or need to widen scope.

Create one stacked Draft / REVIEW_REQUIRED cleanup PR and stop after publishing
an independently reviewed cleanup result. Do not begin any subsequent runtime or
formal campaign.
```

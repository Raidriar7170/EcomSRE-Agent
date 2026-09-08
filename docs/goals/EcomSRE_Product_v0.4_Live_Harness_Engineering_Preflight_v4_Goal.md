# Goal: EcomSRE-Agent Product v0.4 — Live Harness Engineering Preflight v4

> **Repository**: `Raidriar7170/EcomSRE-Agent`  
> **Goal ID**: `ecomsre-product-v040-live-harness-engineering-preflight-v4`  
> **Goal mode**: continuous autonomous engineering preflight  
> **Starting `origin/main`**: `cc941b51cbff9287b876be49652cd0ad83030474`  
> **Starting tree**: `1b3baa986bcffc00dd4064c8b6c147c30f15e4d3`  
> **Pinned OTel Demo submodule**: `1755859a9de82c2e5e225be68abc401a5ebf2b4f`  
> **Historical cleanup evidence**: PR `#100`, head `c22c14c69542931505b0577b190a3cfa099d16cb`  
> **Required branch**: `codex/product-v040-live-harness-preflight-v4`  
> **Required PR base**: `main`  
> **Execution model**: one branch, one continuously updated PR, bounded engineering attempts, no intermediate user approval  
> **Maximum live engineering-preflight attempts**: `5`  
> **Positive stability requirement**: `2` consecutive complete passes on the same frozen runtime surface  
> **Formal Payment campaign authority**: `NONE`  
> **Payment fault-injection authority**: `NONE`  
> **Product remediation authority**: `NONE`  
> **Product Provider / LLM calls**: `0`  
> **Target terminal**: `ECOMSRE_PRODUCT_V040_LIVE_HARNESS_PREFLIGHT_V4_COMPLETE`  
> **Updated**: 2026-09-08

---

## 中文摘要

本 Goal 的任务不是再次实现 Candidate、Approval、Authorization、Executor 或
Recovery Verifier；这些 Product v0.4 A–D 能力已经进入 `main`。本 Goal 只解决
真实本地环境验收 Harness 仍不稳定的问题：

```text
固定版本的本地 OpenTelemetry Demo
        ↓
独立 Kafka copy-up / access probe
        ↓
Probe 完整移除
        ↓
精确 28 个业务服务启动并逐服务健康
        ↓
Product API / Worker 启动
        ↓
真实 Connector 与 Capability Matrix
        ↓
30/30 健康业务事务
        ↓
五窗口 Active Baseline
        ↓
真实 NO_INCIDENT
        ↓
证明恢复路径仍未获得执行条件
        ↓
精确 Cleanup，owned 资源归零
```

本 Goal 是**工程预检**，不是正式实验。允许在同一个冻结 Goal 中：

```text
发现 Harness 问题
→ 保存失败证据
→ 精确清理
→ 修复代码
→ 测试和独立审查
→ 自动开始新的工程预检
```

Codex 不得在以下中间节点停下来等待用户：

```text
- 完成一个内部阶段；
- 打开或更新 Draft PR；
- 一次工程预检失败；
- 完成一次失败后的安全清理；
- 测试失败并已定位为项目代码问题；
- 独立 Reviewer 提出可在本 Goal 范围内修复的问题；
- GitHub CI 失败并可由本 Goal 范围内的代码修复；
- 第一轮完整 PASS；
- 需要运行第二个稳定性 PASS。
```

激活本文件即构成对本文明确列出的 Git、GitHub、本地 Docker、loopback HTTP、
临时文件、测试、审查、清理、最多五次工程预检和正向完成后合并操作的
**standing prior authorization**。在本文范围内不再需要额外授权。

本文不授权正式 Payment 故障、恢复写入或正式 Campaign。遇到需要越过这些边界的
情况，不得询问用户后临时扩权；应完成当前 Goal 的最终受限终态并停止。

---

# 0. Activation, Standing Authority, and No-Pause Semantics

## 0.1 Activation

This file is a draft Goal Contract until the user gives an unambiguous activation
instruction such as:

```text
Execute EcomSRE Product v0.4 Live Harness Engineering Preflight v4 end to end.

This Goal is my standing prior authorization for every repository, GitHub,
owned-local Docker, loopback HTTP, evidence, cleanup, review, CI, retry and
positive-completion merge operation explicitly listed in the Goal.

Do not ask me for additional authorization and do not pause at intermediate
phases, PR boundaries, review boundaries, CI boundaries or failed engineering
attempts. Continue autonomously until one final Goal terminal is reached.

Do not run the formal Payment campaign or any remediation mutation.
```

Once the user invokes the complete activation prompt in Section 40, that
single activation is sufficient for the entire Goal. Codex must not ask the user
to repeat or renew the authorization in a later phase.

## 0.2 Activation procedure

On activation, Codex must:

1. run `git fetch origin --prune`;
2. verify that commit
   `cc941b51cbff9287b876be49652cd0ad83030474`
   exists and has tree
   `1b3baa986bcffc00dd4064c8b6c147c30f15e4d3`;
3. verify that the OTel Demo gitlink is exactly
   `1755859a9de82c2e5e225be68abc401a5ebf2b4f`;
4. create a clean worktree and branch
   `codex/product-v040-live-harness-preflight-v4`
   from the exact starting commit;
5. copy this Goal into
   `docs/goals/EcomSRE_Product_v0.4_Live_Harness_Engineering_Preflight_v4_Goal.md`;
6. compute and persist the exact UTF-8 SHA-256 of the Goal file;
7. record the starting commit, tree, submodule, current Docker context, daemon
   identity, platform, activation UTC time and local monotonic anchor;
8. verify the PR #100 cleanup result and independently confirm that no historical
   v0.4 preflight resource remains before creating new resources;
9. open one Draft PR against `main` as soon as the initial contract and tests are
   reviewable;
10. continue in the same Goal and PR without waiting for user input.

## 0.3 Starting-main movement

The exact starting commit remains the historical base even if `main` advances
after activation.

Codex is already authorized to perform the following integration behavior without
asking the user:

```text
- inspect new main commits;
- rebase or recreate the branch on the new main before final merge;
- resolve non-semantic conflicts;
- rerun all required tests, review and CI;
- reset the consecutive-pass streak if a merged main change affects the live
  runtime surface, Product deployment, connector behavior, ownership, cleanup,
  baseline, diagnosis or remediation isolation.
```

If a new `main` commit is documentation-only and does not alter the frozen
runtime surface, Codex may preserve the live pass streak after an independent
review records that determination.

If safe integration is impossible without expanding this Goal, finish with
`REPOSITORY_INCOMPATIBLE_FINAL` rather than waiting for user authorization.

## 0.4 No intermediate stop

The following are **not Goal terminals**:

```text
PR_OPENED
PHASE_COMPLETE
OFFLINE_TEST_FAILED
REVIEW_FINDING
CI_FAILED
ENGINEERING_ATTEMPT_FAILED
CLEANUP_COMPLETE_AFTER_FAILED_ATTEMPT
FIRST_CONSECUTIVE_PASS
SOURCE_REPAIRED
MAIN_REBASED
```

Codex must continue automatically after each one.

A failure in an engineering attempt is a development observation. It does not
consume formal Payment authority and does not by itself end the Goal.

## 0.5 Final terminals only

Codex may stop only after publishing one of these final terminals:

```text
goal_complete_checkpoint
engineering_preflight_exhausted_checkpoint
safety_final_checkpoint
repository_incompatible_final
```

No final terminal asks the user a question. Each must contain a complete,
actionable disposition.

---

# 1. Mission

Build and validate a stable, reviewable Live Harness for the already-merged
Product v0.4 bounded-remediation implementation.

The Harness must prove that the full local environment can repeatedly reach and
leave this state without any fault or remediation:

```text
clean host-owned boundary
→ fixed image and Compose resolution
→ Kafka volume provenance and access qualification
→ exact 28-service OTel Sandbox healthy
→ Product API and Worker ready
→ real connector verification
→ 30/30 healthy checkout traffic
→ five-window DEMO_ONLY Active Baseline
→ one fresh NO_INCIDENT diagnosis
→ all remediation/formal-action counters remain zero
→ exact cleanup
→ no owned containers, networks or volumes
→ non-owned inventory unchanged
```

The positive result requires two consecutive complete attempts on the same
runtime-relevant source, policy, resolved Compose commitments and image
commitments.

The Harness is preparation for a future separately authorized formal Payment
campaign. This Goal must not execute that campaign.

---

# 2. Why This Goal Exists

## 2.1 Product functionality is not the present blocker

The merged `main` already contains:

```text
PR #91 — deterministic Payment remediation candidate
PR #92 — explicit persisted operator approval
PR #93 — fresh-state-bound single-use authorization and WriteIntent
PR #94 — isolated typed executor, StepReceipt and two-window verifier
```

The unresolved problem is not the absence of these Product components. The
unresolved problem is reliable live-environment bring-up, observation and
cleanup before any formal write.

## 2.2 Lessons retained from PR #95–#100

The historical Draft stack must remain unmerged and immutable, but its evidence
must inform v4 design.

Observed classes include:

```text
- Docker Desktop daemon / builtin-network lifecycle changes;
- undeclared Kafka image volumes;
- copy-up UID/GID/mode assumptions that did not match pinned image truth;
- raw inspect serialization drift for HostConfig.OomKillDisable;
- a qualification probe sharing Sandbox ownership labels;
- a stopped probe being counted as a 29th Sandbox container;
- health logic turning a role-count mismatch into all-services unhealthy;
- missing contemporaneous per-service health evidence;
- planned command / entrypoint values omitting image-inherited defaults;
- cleanup authority coupled too tightly to non-security-critical raw-field
  equality;
- successful exact cleanup of the final retained 29 containers, 3 networks and
  6 volumes under PR #100.
```

v4 must fix these classes prospectively. It must not rewrite the historical
results.

## 2.3 Engineering preflight is not a formal one-shot

The earlier attempt model treated each no-fault bring-up as a consumed one-shot.
That is unsuitable for ordinary Harness development.

This Goal separates:

```text
Engineering preflight:
  bounded, repeatable after a real repair, all attempts retained

Formal Payment campaign:
  not authorized here, remains future one-shot work
```

---

# 3. Accepted Starting Facts

## 3.1 Repository baseline

Accepted exact baseline:

```yaml
repository: Raidriar7170/EcomSRE-Agent
starting_main: cc941b51cbff9287b876be49652cd0ad83030474
starting_tree: 1b3baa986bcffc00dd4064c8b6c147c30f15e4d3
otel_submodule: 1755859a9de82c2e5e225be68abc401a5ebf2b4f
architecture: linux/arm64
```

The starting main ends at merged Product v0.4 PR-D.

## 3.2 Current Product authority boundary

The Product remains read-only by default.

The following facts remain binding:

```text
- a Diagnosis is not write authority;
- a Candidate is not write authority;
- an Approval alone is not write authority;
- authorization is separate and state-bound;
- the executor is profile-gated and disabled by default;
- no arbitrary Shell, URL, container ID or model-generated write parameter;
- Product diagnosis Provider calls are not required for this Goal.
```

## 3.3 PR #100 cleanup

PR #100 recorded a successful exact cleanup of the PR #99 retained set:

```text
containers removed: 29
networks removed: 3
volumes removed: 6
remaining retained targets: 0
preexisting target absences: 0
formal Product actions: 0
```

This is a historical point-in-time result, not permission to assume the current
host is still clean. v4 must perform a fresh read-only activation inventory.

## 3.4 Historical Draft stack

The following are read-only evidence:

```text
PR #95
PR #97
PR #98
PR #99
PR #100
```

PR #96 was merged only into the historical stacked branch and does not change
the rule above.

Do not merge, rewrite, squash, amend or force-push any historical branch as part
of this Goal.

---

# 4. Evidence Priority and Claim Discipline

Use this evidence priority:

```text
1. machine-readable attempt result and immutable raw captures;
2. creation / mutation / cleanup intent and receipt chain;
3. final independent review;
4. exact-head CI;
5. source analysis and derived explanation;
6. PR summary;
7. UI screenshot.
```

A derived explanation cannot replace missing direct evidence.

Examples:

```text
- 29 containers instead of 28 is not proof that Kafka was unhealthy;
- the same container ID with a changed serialized field is not proof of physical
  replacement;
- HTTP 200 from Prometheus is not proof of service-level metric coverage;
- successful Docker stop is not proof of complete cleanup;
- cleanup after a failed attempt cannot upgrade the attempt to PASS;
- two successful attempts are engineering stability evidence, not production
  availability or formal remediation evidence.
```

All failed engineering attempts remain visible in the final report.

---

# 5. Positive Goal Definition

The Goal is complete only when all of the following are true.

## 5.1 Implementation

A new v4 Harness exists on `main`-based code and includes:

```text
- run-scoped ownership plan;
- independent probe namespace;
- pinned-image and resolved-Compose binding;
- image-inherited effective command resolution;
- stage-aware raw and semantic inventory;
- per-service health evidence;
- immutable attempt journal;
- creation-bound cleanup authority;
- exact post-failure cleanup;
- Product no-fault workflow;
- attempt and consecutive-pass accounting.
```

## 5.2 Runtime stability

Two consecutive fresh attempts pass on the same frozen runtime surface:

```text
same runtime source commit
same Harness policy digest
same resolved Compose semantic digests
same OTel image/platform commitments
same Product image digest
same traffic and Baseline profile
different attempt IDs
different private roots
freshly created resources
zero resource reuse
```

## 5.3 Per-attempt result

Each of the final two attempts must prove:

```text
probe provenance PASS
probe effective access PASS
probe sentinel PASS
probe removed before Sandbox start
exact business-service role set PASS
all 28 Sandbox services healthy
warmup gate PASS
healthy checkout 30/30
connector verification PASS
Capability Matrix accepted
Active Baseline created
NO_INCIDENT returned
capability limitations empty
formal-action counters all zero
Product remediation path unavailable / unexecutable
cleanup CLEAN
owned runtime resources zero
non-owned resources unchanged
```

## 5.4 Integration

After the two passes:

```text
- final independent review PASS;
- Must Fix 0;
- Claim Accuracy PASS;
- full test suite PASS;
- Ruff PASS;
- mainline mypy PASS;
- exact-head GitHub CI PASS;
- tracked-content closure PASS;
- PR marked Ready;
- PR squash-merged to main;
- merged tree equals reviewed head tree;
- completion record published.
```

---

# 6. Permanent Non-Goals

The following are outside this Goal even when technically possible:

```text
- no Payment fault injection;
- no paymentFailure runtime mutation;
- no formal campaign manifest;
- no formal RemediationCandidate;
- no formal OperatorApproval;
- no formal AttemptAuthorization;
- no formal WriteIntent;
- no remediation executor dispatch;
- no StepReceipt from a real remediation attempt;
- no recovery windows;
- no Product RECOVERED claim;
- no Provider / LLM call inside Product;
- no production, cloud, remote Docker, SSH or Kubernetes;
- no arbitrary host command supplied by a model;
- no global Docker cleanup;
- no stopping or deleting unknown resources;
- no host networking or Docker Desktop setting change;
- no process kill to free a port;
- no upstream OTel source patch;
- no unpinned image fallback;
- no amd64 emulation;
- no clustering, rule-mining or diagnosis-threshold redesign;
- no new remediation mapping;
- no Recommendation restart;
- no Email leak remediation;
- no Kafka backlog remediation;
- no merge of PR #95–#100;
- no release or Git tag.
```

Codex must not request a user exception to these non-goals during execution.

---

# 7. Standing Authorization Matrix

## 7.1 Repository and GitHub authorization

This Goal authorizes Codex to:

```text
- create the required branch and worktree;
- add and edit files in the allowed scope;
- run formatters, tests, static analysis and verification;
- commit changes;
- push the branch;
- open and continuously update one Draft PR;
- create review artifacts;
- invoke independent read-only reviewer agents;
- respond to and repair review findings;
- run exact-head CI;
- rebase or recreate on a moved main under Section 0.3;
- mark the PR Ready after all positive gates;
- squash-merge the positive PR to main;
- verify the merged tree and publish the completion record.
```

No additional user confirmation is required for any listed operation.

## 7.2 Local Docker authorization

For at most five engineering attempts, this Goal authorizes operations only on
new resources created and bound by the active v4 attempt:

```text
docker context inspect
docker info
docker version
docker image inspect
docker image ls
docker build for the exact Product source
exact digest pull only when a required pinned linux/arm64 image is absent
docker volume create / inspect / rm
docker network create / inspect / rm
docker container create / inspect / start / stop / rm
docker compose config
docker compose up for the exact generated owned plan
bounded docker exec for fixed probe, census and sentinel programs
bounded docker logs / inspect for evidence
```

The exact pull exception is limited to an image reference and platform digest
already frozen by the Harness plan. It forbids `latest`, a changed source
reference, architecture fallback or tag-only trust.

## 7.3 Loopback and application authorization

This Goal authorizes:

```text
- bounded HTTP requests to the exact local loopback ports in the active plan;
- OTel Demo warmup and healthy checkout transactions;
- Product health, readiness, environment, Baseline, Incident, Diagnosis,
  Evidence and metrics API requests;
- local temporary Unix sockets used by read-only preflight components;
- fixed Kafka-volume sentinel create/read/stat/delete operations in fresh owned
  qualification volumes.
```

The sentinel is qualification activity, not Product remediation.

## 7.4 Private evidence authorization

Codex may create attempt-scoped roots under a path such as:

```text
.local/product-v040-preflight-v4/<attempt-id>/
```

Requirements:

```text
directory mode: 0700
regular private file mode: 0600
create-once result files
append-only journal
no credentials in tracked evidence
safe public projections only
```

## 7.5 Automatic repair authorization

After an engineering attempt fails and its owned resources are safely cleaned,
Codex is authorized to:

```text
- analyze the root cause;
- modify in-scope Harness, deployment and integration code;
- add tests;
- update the Draft PR;
- rerun local validation;
- obtain another independent review;
- run another exact-head CI;
- start the next engineering attempt.
```

No new user message is required.

---

# 8. Attempt Budget and Continuous Progression

## 8.1 Maximum count

The Goal permits:

```text
maximum engineering live attempts: 5
maximum simultaneously active attempts: 1
maximum formal Payment attempts: 0
maximum remediation writes: 0
```

An attempt is consumed when the Harness creates its first Docker resource.

Read-only host inspection and offline tests do not consume an attempt.

## 8.2 Attempt IDs

Every attempt receives:

```text
attempt_id
source_head
policy_sha256
resolved_compose_sha256s
image commitment set
private evidence root
activation UTC
monotonic anchor
```

An attempt ID and root are never reused.

## 8.3 Failed attempt continuation

A failed attempt must follow:

```text
freeze raw failure evidence
→ record first divergence or explicit functional failure
→ capture relevant logs and per-service state
→ perform exact owned cleanup
→ independently verify cleanup
→ classify root cause
→ commit a real repair or record an evidenced host stabilization change
→ reset consecutive-pass streak
→ rerun offline gates
→ continue to next attempt
```

A silent identical rerun is forbidden.

A subsequent attempt requires at least one of:

```text
- committed Harness/deployment/config repair;
- independently recorded Docker daemon restart/stabilization event;
- corrected local resource conflict with no process kill or host mutation;
- corrected missing pinned-image availability using the exact-pull exception.
```

## 8.4 Consecutive-pass streak

The streak rules are:

```text
first complete PASS:
  streak = 1
  freeze runtime-relevant source and policy

second fresh complete PASS with identical frozen runtime surface:
  streak = 2
  positive runtime gate satisfied

any live failure:
  streak = 0

any runtime-relevant code/config/policy/image change:
  streak = 0

documentation-only changes:
  do not reset only after independent review proves no runtime-surface change
```

## 8.5 Exhaustion

If five attempts are consumed without two consecutive passes:

```text
terminal: engineering_preflight_exhausted_checkpoint
```

Codex must still:

```text
- clean every safely provable owned resource;
- publish all attempts and root-cause dispositions;
- keep the PR Draft / REVIEW_REQUIRED;
- not ask the user for another attempt inside this Goal.
```

---

# 9. Required Repository Strategy

## 9.1 Start clean from main

Do not base the implementation on PR #95–#100.

Start from:

```text
cc941b51cbff9287b876be49652cd0ad83030474
```

Historical branches may be inspected read-only.

## 9.2 Selective reuse only

Code ideas may be reimplemented or selectively cherry-picked only after review.
Do not import a historical attempt's authority, fuse, resource ID, private root
or terminal.

Useful concepts to retain include:

```text
- explicit Kafka image-volume coverage;
- pinned OCI platform and RootFS commitments;
- copy-up provenance and effective-access checks;
- stage-aware lifecycle fingerprinting;
- builtin none-network endpoint accounting;
- exact cleanup receipts.
```

## 9.3 One PR

Use one continuously updated PR:

```text
branch: codex/product-v040-live-harness-preflight-v4
base: main
```

Opening the PR is not a stop point.

The PR remains Draft until both consecutive passes and all final gates are
complete.

---

# 10. Allowed Repository Write Scope

Preferred new paths:

```text
docs/goals/EcomSRE_Product_v0.4_Live_Harness_Engineering_Preflight_v4_Goal.md

config/product-v040/preflight-v4/**
scripts/product/preflight_v040_v4/**
tests/product_v040/preflight_v4/**
docs/results/product-v040-preflight-v4/**
docs/analysis/product-v040-preflight-v4-*.md
docs/external-reviews/product-v040-preflight-v4-*.md

docker-compose.product.preflight-v4.yml
scripts/ci/verify_product_v040_preflight_v4.py
```

Narrow changes are allowed when necessary in:

```text
src/ecomsre_live_sandbox/**
src/ecomsre/product/connectors/**
src/ecomsre/product/environment/**
src/ecomsre/product/jobs/**
src/ecomsre/product/app.py
Dockerfile.product
docker-compose.product.yml
.github/workflows/agent-mainline.yml
docs/DECISIONS.md
docs/OPEN_QUESTIONS.md
docs/product/**
```

Conditions for these narrow changes:

```text
- preserve existing public contracts unless a demonstrated bug requires an
  additive compatible repair;
- do not change Diagnosis classification semantics;
- do not change remediation candidate, approval, authorization, executor,
  receipt or verifier semantics;
- do not broaden write authority;
- include focused regression tests and independent review.
```

Frozen paths and semantics:

```text
third_party/opentelemetry-demo/**
historical result JSON / Markdown
historical Goal files
PR #95–#100 evidence
frozen DTA evaluation artifacts
Core / Extension rule thresholds
Product v0.3 knowledge-evolution results
Product v0.4 remediation mapping and one-step cap
```

---

# 11. Required Harness Architecture

The v4 Harness must have explicit components.

## 11.1 Host coordinator

Responsibilities:

```text
- bind source and Goal;
- authenticate local Docker context and daemon;
- resolve images and Compose;
- create attempt namespace;
- manage stage journal;
- launch exact components;
- run healthy traffic;
- call Product APIs;
- collect evidence;
- invoke exact cleanup;
- publish attempt result.
```

It is deterministic orchestration, not an LLM Agent.

## 11.2 Ownership planner

Before the first mutation, build a complete plan containing:

```text
- expected container roles;
- expected image IDs and platform digests;
- expected effective entrypoint and command;
- environment commitments;
- mount source and target commitments;
- named-volume definitions;
- network definitions;
- labels;
- published ports;
- birth and death stages;
- cleanup authority anchors;
- permitted lifecycle transitions;
- expected Product routes;
- attempt counters.
```

Observed resources cannot be promoted into the expected plan after creation.

## 11.3 Raw evidence layer

Every stage stores complete bounded raw captures.

Raw evidence must preserve:

```text
- false versus null;
- image-inherited versus Compose-declared fields;
- exact Docker IDs;
- raw health objects;
- network endpoints;
- volume attachments;
- timestamps and restart counts.
```

## 11.4 Semantic validation layer

A separate semantic view may normalize only predeclared Docker lifecycle
representations.

It must not delete raw differences.

## 11.5 Cleanup authority

Cleanup authority is minted at resource birth and binds:

```text
attempt ID
daemon/context
resource kind
resource ID
name
creation time
owned labels
image identity where relevant
mount identities
network identities
birth receipt hash
```

It is independent from later application health and non-security-critical
lifecycle serialization.

---

# 12. Mandatory Fix 1 — Independent Probe Namespace

## 12.1 Probe purpose

The Kafka volume probe exists only to:

```text
- initialize fresh explicit Kafka named volumes from the pinned image;
- measure copy-up metadata;
- measure the exact runtime identity;
- run the bounded sentinel;
- prove removal before the Sandbox starts.
```

## 12.2 Probe labels

The probe must not carry the Sandbox Compose project or Sandbox service ownership
labels.

Use a separate namespace such as:

```text
io.ecomsre.preflight.v4.attempt=<attempt-id>
io.ecomsre.preflight.v4.role=kafka-volume-probe
io.ecomsre.preflight.v4.owner=live-harness
```

It must not match the query used to enumerate the 28 Sandbox services.

## 12.3 Probe network

The probe uses:

```text
network mode: none
published ports: none
capabilities: none
no-new-privileges: true
read-only root filesystem: true
```

The exact owned endpoint that Docker exposes on builtin `none` may exist only
during the probe's running lifetime.

## 12.4 Probe destruction

The required order is:

```text
create
→ copy-up measurement
→ start
→ process census
→ sentinel
→ stop
→ verify stopped
→ remove
→ verify absent
→ verify builtin none endpoint absent
→ only then start Sandbox
```

A merely stopped probe is not acceptable.

---

# 13. Mandatory Fix 2 — Explicit Kafka Volume Coverage

The pinned Kafka image declares:

```text
/etc/kafka/secrets
/mnt/shared/config
/var/lib/kafka/data
```

Each attempt must create fresh, explicit, attempt-scoped named volumes for these
paths.

Requirements:

```text
- no anonymous volume;
- exact local driver;
- no unexpected driver options;
- attempt and ownership labels;
- fresh absence before create;
- exact create receipt;
- no reuse across attempts;
- copy-up enabled only as frozen by policy;
- all attachments enumerated;
- removed after the attempt.
```

The Harness must inspect the pinned image's complete `Config.Volumes`, not only
the three remembered paths. Any unbound declared path blocks that attempt before
Sandbox start.

---

# 14. Mandatory Fix 3 — Copy-Up Provenance and Effective Access

The v4 policy must bind path-specific image truth rather than assume every path
has one uniform GID.

For each declared path, persist:

```text
image/platform/config commitments
RootFS layer commitments
path
type
UID
GID
mode
initial bounded entries
mutable versus immutable classification
```

Effective-access checks must capture:

```text
EUID / EGID
all real/effective/saved/fs UIDs and GIDs
supplementary groups
CapEff
CapPrm
NoNewPrivs
PID / PPID
process start time
full fixed sentinel argv
```

The sentinel:

```text
- creates one unique file per qualified path;
- reads exact content back;
- verifies UID/GID/mode;
- deletes the file;
- verifies absence;
- cannot follow or replace a symlink;
- cannot write outside the fresh owned volumes;
- is not a Product fault or remediation.
```

No runtime `chown` or `chmod` may be used to make a failed gate pass.

---

# 15. Mandatory Fix 4 — Effective Entrypoint and Command Resolution

## 15.1 Three representations

For every container role, record:

```text
compose_declared_entrypoint
compose_declared_command
image_default_entrypoint
image_default_command
resolved_effective_entrypoint
resolved_effective_command
```

## 15.2 Resolution

Resolve the effective process from the frozen Compose model and pinned image
configuration before mutation.

Do not use a post-start observed command as authority.

## 15.3 Validation

At birth and later stages, compare actual state to the resolved effective process.

A raw representation difference may be semantically accepted only when:

```text
- the resolved effective argv is identical;
- the container ID and image identity are unchanged;
- no security-critical field changed;
- the transition was predeclared;
- raw before/after values remain recorded.
```

## 15.4 Cleanup

Cleanup must not be permanently blocked because the plan stored `null` while the
container inherited the exact frozen image default.

The cleanup gate uses the resolved effective command and the birth-bound cleanup
authority.

---

# 16. Mandatory Fix 5 — Stage-Aware Lifecycle Fingerprint

Split fingerprinting into:

```text
RAW_INSPECT_FINGERPRINT
IMMUTABLE_SECURITY_FINGERPRINT
LIFECYCLE_STATE_PROJECTION
```

## 16.1 Immutable security fields

At minimum:

```text
container ID
Created timestamp
image ID / platform digest
Config.User
resolved entrypoint / command
environment commitment
privileged
cap add/drop
devices
security options
read-only rootfs
mount identities and RW flags
network mode
published ports
ownership labels
```

## 16.2 Lifecycle fields

Examples:

```text
Running
Paused
Restarting
Dead
OOMKilled
Health
StartedAt
FinishedAt
RestartCount
network endpoint presence
```

These may change only according to the predeclared stage plan.

## 16.3 OomKillDisable representation

A `false` versus `null` raw difference may be admitted only under a versioned
policy that:

```text
- limits the transition to the exact role and lifecycle stage;
- requires the same container ID;
- requires all immutable-security fields equal;
- records fresh effective cgroup/OOM observations where required;
- forbids true-to-false or true-to-null normalization;
- preserves the raw difference.
```

---

# 17. Mandatory Fix 6 — Exact Sandbox Role Health

## 17.1 Business service set

The exact expected Sandbox service-role set is resolved before startup from the
frozen OTel Compose plan and is expected to contain 28 business/runtime services.

The Kafka volume probe is never part of this set.

## 17.2 Enumeration

Do not determine health by comparing all containers sharing a broad project
label with a fixed integer.

Instead:

```text
expected service role
→ exact planned container identity
→ current same-ID container
→ current State / Health
```

Unknown extra owned roles produce:

```text
UNEXPECTED_SANDBOX_ROLE
```

They must not be transformed into “all 28 services unhealthy.”

## 17.3 Per-service evidence

Persist a contemporaneous map such as:

```json
{
  "accounting": {"running": true, "health": "healthy"},
  "ad": {"running": true, "health": "healthy"},
  "payment": {"running": true, "health": "healthy"}
}
```

Include every expected role.

For a service without Docker Health:

```text
running=true
healthcheck=NOT_DECLARED
```

is not sufficient by itself when a frozen endpoint or dependency readiness check
exists. Use the role-specific readiness contract.

## 17.4 Failure evidence

If health fails, capture before cleanup:

```text
- exact unhealthy/missing roles;
- raw State and Health;
- bounded logs for those roles;
- restart counts;
- image and command bindings;
- dependency status where available;
- no unsupported root-cause claim.
```

---

# 18. Docker Desktop and Host Stabilization

Before each attempt:

```text
- select the exact local Docker context;
- require local Unix authority;
- wake Docker with read-only version/info only;
- wait for daemon readiness;
- obtain two matching daemon identity observations separated by a bounded
  stability interval;
- verify required loopback ports are available;
- verify no historical v4 owned resource exists;
- bind the initial non-owned inventory.
```

Do not change Docker Desktop settings.

If the daemon identity changes during an attempt:

```text
attempt result: DAEMON_IDENTITY_DRIFT
```

Then:

```text
- retain evidence;
- reauthenticate;
- perform exact cleanup only if birth identities still match;
- wait for stability;
- repair only if project code is implicated;
- continue within the attempt budget.
```

A port conflict:

```text
- must identify the owner read-only when possible;
- must not kill a process;
- may wait a bounded period and recheck;
- consumes no attempt before the first resource birth.
```

---

# 19. Image and Build Policy

## 19.1 OTel images

Use only the frozen `linux/arm64` image set associated with the pinned OTel
submodule and accepted lock/evidence.

Rules:

```text
- inspect by digest;
- no latest;
- no architecture fallback;
- no amd64 emulation;
- no mutable tag trust after resolution;
- no upstream source patch;
- exact pull only if absent and already digest-authorized.
```

## 19.2 Product image

Build one Product image from the exact runtime-relevant source head.

Tag format may be:

```text
ecomsre-product-v040-preflight-v4:<source-short-sha>
```

Bind:

```text
Dockerfile
build context tree
base image digest
result image ID
result platform digest
source head
```

The final two successful attempts must use the same Product image ID.

## 19.3 Build is not an attempt

The attempt starts only at the first runtime-resource create.

A failed Product build is repaired offline and does not consume live-attempt
budget.

---

# 20. Compose and Ownership Plan

## 20.1 Resolved plan

Use actual Compose expansion before startup.

Persist:

```text
raw source files
environment substitutions
expanded Compose JSON
semantic Compose digest
service roles
images
effective commands
mounts
networks
ports
profiles
healthchecks
dependencies
```

## 20.2 Attempt namespace

Every new runtime resource carries the v4 attempt label.

Preferred labels:

```text
io.ecomsre.preflight.v4.goal=<goal-sha256>
io.ecomsre.preflight.v4.attempt=<attempt-id>
io.ecomsre.preflight.v4.role=<role>
```

Historical labels may be retained when required by Product/Sandbox code, but
they are never sufficient ownership proof on their own.

## 20.3 Resource plan

The plan derives exact expected cardinality from expanded Compose, except for
the separately planned probe.

Do not hardcode one global container/network/volume count when the generated
plan can answer it.

The plan must reject:

```text
anonymous volumes
unbound bind mounts
host network
privileged containers
unexpected device access
unexpected cap additions
unknown published ports
unresolved image references
duplicate role identities
```

---

# 21. Attempt Stage Sequence

A conforming attempt uses the following logical stages.

```text
00 ATTEMPT_IDENTITY
01 INITIAL_HOST_AND_DAEMON
02 INITIAL_NONOWNED_INVENTORY
03 PINNED_IMAGE_VERIFICATION
04 COMPOSE_RESOLUTION
05 EXPECTED_PLAN_FREEZE
06 KAFKA_VOLUMES_CREATE
07 PROBE_CREATE
08 COPYUP_MEASURE
09 PROBE_START
10 PROBE_PROCESS_CENSUS
11 PROBE_SENTINEL
12 PROBE_STOP
13 PROBE_REMOVE
14 PROBE_ABSENCE_AND_NONE_ENDPOINT
15 SANDBOX_NETWORKS_CREATE
16 SANDBOX_OTHER_VOLUMES_CREATE
17 SANDBOX_START
18 SANDBOX_ROLE_ENUMERATION
19 SANDBOX_PER_SERVICE_HEALTH
20 APPLICATION_WARMUP
21 TELEMETRY_SETTLEMENT
22 HEALTHY_TRAFFIC
23 HEALTHY_EVIDENCE_CAPTURE
24 PRODUCT_IMAGE_VERIFY
25 PRODUCT_NETWORKS_AND_VOLUMES_CREATE
26 PRODUCT_START
27 PRODUCT_READY
28 ENVIRONMENT_REGISTER
29 CONNECTOR_VERIFY
30 CAPABILITY_MATRIX
31 BASELINE_BUILD
32 BASELINE_READINESS
33 BASELINE_ACTIVATE
34 NOFAULT_INCIDENT_CREATE
35 NOFAULT_DIAGNOSIS
36 NOFAULT_EVIDENCE_RESOLUTION
37 REMEDIATION_ZERO_AUTHORITY
38 PRODUCT_METRICS_CAPTURE
39 PRE_CLEANUP_INVENTORY
40 PRODUCT_STOP_REMOVE
41 PRODUCT_NETWORK_VOLUME_REMOVE
42 SANDBOX_STOP_REMOVE
43 SANDBOX_NETWORK_VOLUME_REMOVE
44 FINAL_OWNED_ZERO
45 FINAL_NONOWNED_COMPARISON
46 ATTEMPT_RESULT
```

Codex may refine the number of technical sub-stages but may not reorder the
safety relationships.

In particular:

```text
Probe removal precedes Sandbox start.
Healthy traffic precedes Baseline activation.
Active Baseline precedes the no-fault Incident.
NO_INCIDENT precedes cleanup.
Cleanup completes before an attempt can be PASS.
```

---

# 22. Warmup and Healthy Traffic Contract

## 22.1 Warmup

Run exactly one bounded warmup group:

```text
maximum transactions: 3
distinct request seed namespace
retries: 0
per-request bounded timeout
total bounded deadline
```

Warmup requests and responses are evidence but are not counted in the formal
30 healthy transactions.

The warmup gate requires at least two independently inspectable business
successes. A transport timeout is never replayed.

## 22.2 Settlement

After warmup, use the frozen settlement interval required to prevent cold-start
and delayed telemetry from contaminating the healthy control.

Default v4 value:

```text
330 seconds
```

This interval is a local engineering policy, not proof that every telemetry
backend has expired old data.

## 22.3 Healthy control

Run:

```text
planned checkout transactions: 30
required business successes: 30
allowed failures: 0
transport retries: 0
```

A business success requires more than HTTP status. Preserve the same bounded
oracle used by current Product acceptance, including order identity and
non-empty expected business content.

Persist each request intent and bounded response digest.

## 22.4 Failure

Any of the following fails the attempt:

```text
completed count != 30
business success count != 30
failure count != 0
retry count != 0
unresolved response identity
traffic evidence cannot be sealed
```

---

# 23. Connector and Capability Contract

The Product may configure at most the currently supported bounded connectors.

The preflight must prove actual usefulness, not only reachability.

For each source, record:

```text
configured
reachable
service discovery
requested services
covered services
target completeness
observable predicates
query-specific success/failure
bounded/truncated state
```

Minimum expected sources for the selected healthy Incident:

```text
METRICS
RESOURCES
TRACES
LOGS
RUNTIME
```

If current Product configuration represents Metrics and Resources through the
same Prometheus connector, preserve the distinct semantic evidence roles.

Missing data is not healthy evidence.

```text
SUCCESS_EMPTY
UNKNOWN
SOURCE_FAILED
PARTIAL
```

must not be converted to `ABSENT_WITH_COMPLETE_COVERAGE`.

---

# 24. Baseline Contract

Build a fresh per-attempt `DEMO_ONLY` Baseline.

Default frozen profile:

```text
lookback_seconds: 180
window_count: 5
minimum_successful_windows: 5
warmup_seconds: 180
```

Requirements:

```text
- every window identity and time range persisted;
- all five required windows accepted;
- Baseline content and SHA persisted;
- construction complete is separate from activation;
- exactly one Active Baseline for the environment;
- Incident binds the activated Baseline;
- no Baseline is built from a faulted or failed healthy-control window.
```

A short local Baseline must remain labeled `DEMO_ONLY`.

---

# 25. NO_INCIDENT Product Contract

After Active Baseline activation, create exactly one fresh no-fault Incident.

The required result is:

```text
terminal = NO_INCIDENT
lane = NO_INCIDENT
root_service_ids = []
mechanism = null
action_authority = NONE
agent_writes = 0
runbook_executions = 0
provider_calls = 0
capability_limitations = []
```

The exact schema representation may follow the current Product contracts, but
the semantic facts above are mandatory.

Resolve and verify every supporting evidence reference against the Product
content-addressed store.

Persist:

```text
Incident
Diagnosis
Evidence Bundle
Evidence Index
Decision Trace
Baseline binding
Capability binding
service identity binding
result digest
```

No `OPEN_WORLD`, `CORE_KNOWN`, `EXTENSION_KNOWN`, conflict or insufficient result
can count as a v4 preflight PASS.

---

# 26. Remediation Must Remain Inactive

This Goal must positively prove zero formal-action activity.

Required counters:

```text
formal_faults = 0
formal_campaign_executions = 0
formal_candidates = 0
approvals = 0
attempt_authorizations = 0
write_intents = 0
executor_invocations = 0
remediation_writes = 0
step_receipts = 0
formal_recovery_windows = 0
provider_calls = 0
```

The no-fault Incident must not produce a remediation candidate.

The live runtime must also prove one of these exact safe states:

```text
- remediation profile services are absent; or
- they run in a frozen deny-only preflight mode with no control credential,
  no active authorization and no write-capable request.
```

The first option is preferred.

Static Compose verification must still confirm that the future remediation
profile resolves to the expected isolated topology.

---

# 27. Cleanup Model

## 27.1 Cleanup is preauthorized

This Goal preauthorizes exact cleanup after every attempt, successful or failed.

Codex must not pause to ask the user for cleanup permission.

## 27.2 Cleanup target authority

A resource is removable only when current observation matches its create-once
birth receipt on all cleanup-critical anchors.

Container anchors:

```text
daemon/context
container ID
Created
attempt label
role label
image ID/platform
mount source identities
network identities
```

Network anchors:

```text
network ID
name
driver
attempt labels
expected endpoint set
```

Volume anchors:

```text
name
driver
attempt labels
expected attachment set
```

## 27.3 Command fields

Resolved effective command is audited and validated during birth.

Cleanup must not depend on a raw null-versus-inherited representation once:

```text
- same container ID is proven;
- image and mount/network identity are unchanged;
- the resolved effective process matches the frozen source;
- no unexpected security-critical field changed.
```

## 27.4 Cleanup order

Required order:

```text
1. stop Product processes;
2. remove Product containers;
3. verify Product network endpoints absent;
4. remove Product networks;
5. verify Product volumes unattached;
6. remove Product volumes;
7. stop Sandbox services;
8. remove Sandbox containers;
9. verify Sandbox network endpoints absent;
10. remove Sandbox networks;
11. verify Sandbox volumes unattached;
12. remove Sandbox volumes;
13. verify all attempt-owned containers/networks/volumes absent;
14. compare non-owned inventory twice.
```

The exact dependency-aware order may be refined, but volume removal never
precedes attachment absence.

## 27.5 Allowed cleanup commands

Only exact targets:

```text
docker stop --time <bounded> <exact-container-id>
docker rm <exact-stopped-container-id>
docker network rm <exact-network-id>
docker volume rm <exact-volume-name>
```

Forbidden:

```text
docker kill
docker rm -f
docker compose down -v
docker system prune
docker container prune
docker network prune
docker volume prune
wildcard deletion
prefix-only deletion
label-only bulk deletion
```

## 27.6 Cleanup failure

When cleanup fails:

```text
- preserve the original attempt result;
- persist cleanup blocker and exact retained set;
- use this Goal's automatic repair authority if identity remains fully proven;
- repair cleanup code offline;
- independently review the repair;
- finish exact cleanup before another attempt.
```

A safely proven owned resource may be cleaned after a cleanup-code repair within
this Goal. This does not rewrite the failed attempt.

If identity is no longer provable or a non-owned mutation would be required:

```text
terminal: safety_final_checkpoint
```

---

# 28. Non-Owned Resource Protection

Capture non-owned inventory before the first mutation and after complete cleanup.

At minimum:

```text
containers
networks
volumes
images
relevant loopback listeners
Docker context
daemon ID
```

Declared attempt-owned images may be separated from the non-owned comparison.

Rules:

```text
- no adoption of a preexisting resource;
- no restoration or replacement of a builtin network;
- no mutation of an unrelated volume;
- no process termination;
- no host-network change;
- no Docker Desktop setting change.
```

Builtin `none`, `host` and `bridge` network membership may reflect exact owned
container endpoints during their authorized lifetimes. The semantic comparison
must:

```text
- retain raw full inventory;
- bind each temporary endpoint to the exact owned container;
- require endpoint disappearance after removal;
- require network identity/configuration unchanged.
```

---

# 29. Failure Classification

Use typed classifications.

Infrastructure and ownership:

```text
DOCKER_CONTEXT_UNSUPPORTED
DAEMON_UNAVAILABLE
DAEMON_IDENTITY_DRIFT
PREEXISTING_OWNED_RESOURCE
UNKNOWN_RESOURCE
OWNERSHIP_LABEL_DRIFT
IMAGE_PLATFORM_DRIFT
MOUNT_SOURCE_DRIFT
NETWORK_IDENTITY_DRIFT
PORT_CONFLICT
INCOMPLETE_CAPTURE
RACED_CAPTURE
```

Probe and volume:

```text
IMAGE_VOLUME_COVERAGE_INCOMPLETE
COPYUP_PROVENANCE_MISMATCH
RUNTIME_IDENTITY_MISMATCH
UNEXPECTED_WRITER
SENTINEL_FAILED
PROBE_NOT_REMOVED
NONE_ENDPOINT_NOT_REMOVED
```

Sandbox:

```text
SANDBOX_ROLE_SET_MISMATCH
UNEXPECTED_SANDBOX_ROLE
SANDBOX_SERVICE_UNHEALTHY
SANDBOX_READY_TIMEOUT
WARMUP_FAILED
HEALTHY_TRAFFIC_FAILED
```

Product:

```text
PRODUCT_NOT_READY
CONNECTOR_VERIFICATION_FAILED
CAPABILITY_INCOMPLETE
BASELINE_BUILD_FAILED
BASELINE_NOT_ACTIVE
NO_INCIDENT_NOT_OBSERVED
EVIDENCE_BINDING_INVALID
FORMAL_COUNTER_NONZERO
REMEDIATION_PATH_ACTIVE
```

Cleanup:

```text
CLEANUP_IDENTITY_MISMATCH
CLEANUP_STOP_FAILED
CLEANUP_CONTAINER_REMOVE_FAILED
CLEANUP_NETWORK_NOT_EMPTY
CLEANUP_NETWORK_REMOVE_FAILED
CLEANUP_VOLUME_ATTACHED
CLEANUP_VOLUME_REMOVE_FAILED
NONOWNED_DRIFT
```

Do not use one broad `SANDBOX_UNHEALTHY` when exact per-service information is
available.

---

# 30. Automatic Engineering Repair Loop

For every failed attempt:

## 30.1 Preserve

Create immutable:

```text
attempt-result.json
first-divergence.json or functional-failure.json
raw-evidence-index.json
cleanup-result.json
root-cause-analysis.md
```

## 30.2 Diagnose

Separate:

```text
direct observations
supported inference
unknown
```

## 30.3 Repair

Repairs may change only the permitted runtime surface.

No gate may be weakened solely because it blocked an attempt.

A gate change is acceptable only when:

```text
- the previous assumption is disproven by direct evidence;
- the desired safety property is restated;
- the new policy is versioned;
- adversarial tests cover the old and new behavior;
- an independent reviewer confirms that the change is a semantic correction,
  not post-hoc result selection.
```

## 30.4 Validate

Before the next attempt:

```text
focused tests
full repository tests
Ruff
mainline mypy
history integrity
Goal verifier
independent review
exact-head CI
```

The exact-head CI requirement may be satisfied by the normal repository
workflow. Codex must monitor it and repair in-scope failures automatically.

## 30.5 Continue

When validation passes and attempts remain, begin the next attempt automatically.

---

# 31. Test Requirements

## 31.1 Probe tests

Cover:

```text
probe labels cannot match Sandbox service enumeration
probe none-network endpoint appears only while running
probe is removed before Sandbox start
probe container absence is required
Kafka volume attachments are exact
sentinel cannot escape target paths
sentinel cleanup is verified
```

## 31.2 Command-resolution tests

Cover the matrix:

```text
Compose entrypoint unset / set / empty
Compose command unset / set / empty
Image Entrypoint absent / present
Image Cmd absent / present
string and list forms after Compose normalization
```

Verify deterministic effective argv.

## 31.3 Health tests

Cover:

```text
exact 28 healthy roles
one missing role
one unhealthy role
one unexpected unrelated container
stopped probe outside namespace
stopped probe incorrectly sharing namespace
service without Docker healthcheck
malformed inspect response
duplicate service role
```

The error must identify the exact role.

## 31.4 Lifecycle tests

Cover:

```text
allowed create-to-running transitions
forbidden container-ID replacement
forbidden image drift
forbidden command drift
allowed predeclared false/null representation only
forbidden additional HostConfig change
builtin none endpoint birth/death
daemon restart
raced inventory
```

## 31.5 Cleanup tests

Use fake transports to exercise failure before and after every cleanup mutation.

Assert:

```text
intent precedes mutation
receipt follows mutation
restart resumes from receipts
no duplicate removal
no force
no prune
unknown target denied
attached volume denied
nonempty network denied
same-ID owned target remains cleanable after noncritical lifecycle change
```

## 31.6 Product tests

Cover:

```text
environment registration
connector verification
Baseline creation/activation
NO_INCIDENT
evidence resolution
no candidate from NO_INCIDENT
all formal counters zero
remediation services absent or deny-only
```

---

# 32. Review Model

Use independent read-only reviewers.

## 32.1 Initial design review

Before Attempt 1:

```text
Verdict
Must Fix
Should Fix
Nice to Have
Scope Creep
Claim Accuracy
Live Admission: ALLOW / WITHHOLD
```

Attempt 1 requires:

```text
Verdict = PASS
Must Fix = 0
Claim Accuracy = PASS
Live Admission = ALLOW
```

Codex may repair review findings without user input.

## 32.2 Per-repair review

Every runtime-relevant repair after a live failure receives an independent
review before the next attempt.

## 32.3 Final review

After two consecutive passes, the final reviewer verifies:

```text
- both attempts are fresh;
- runtime surface is identical;
- all prior attempts are retained;
- cleanup is CLEAN for every pass;
- no formal action occurred;
- no claim crosses into formal remediation or production readiness;
- merge is safe.
```

Required:

```text
PASS / Must Fix 0 / Claim Accuracy PASS / MERGE_ALLOW
```

---

# 33. CI and Content Closure

Before every live attempt on a changed runtime head:

```text
uv sync --frozen --python 3.11
full pytest
ruff check
mainline mypy
v4 verifier
history-integrity verifier
git diff --check
exact-head GitHub CI
```

The Goal may adapt exact invocation to repository conventions while preserving
coverage.

Final content closure records:

```text
starting main
final reviewed head
tracked paths
file count
bytes read
tracked-diff SHA-256
evidence SHA-256
cache hits = 0
frozen historical file verification
```

A CI result from an earlier head cannot validate a later runtime head.

Documentation-only publication commits must explicitly state which runtime head
the live evidence and CI validate.

---

# 34. Public and Private Evidence

## 34.1 Tracked public files

Create at least:

```text
config/product-v040/preflight-v4/goal-contract.json
config/product-v040/preflight-v4/policy.json
config/product-v040/preflight-v4/traffic-profile.json

docs/analysis/product-v040-preflight-v4-progress.json
docs/analysis/product-v040-preflight-v4-architecture.md
docs/analysis/product-v040-preflight-v4-attempt-summary.json

docs/results/product-v040-preflight-v4/README.md
docs/results/product-v040-preflight-v4/final-result.json
docs/results/product-v040-preflight-v4/attempts.json
docs/results/product-v040-preflight-v4/evidence-manifest.json
docs/results/product-v040-preflight-v4/HUMAN_BRIEF.md

docs/external-reviews/product-v040-preflight-v4-design-review.md
docs/external-reviews/product-v040-preflight-v4-final-review.md
```

Failed-attempt public summaries are append-only.

## 34.2 Private evidence

Per attempt, preserve:

```text
activation
source bindings
raw Docker captures
image inspections
expanded Compose
stage journal
probe archives
process census
sentinel output
per-service health
bounded logs
traffic request/response digests
Product API responses
SQLite/CAS indexes where permitted
cleanup intents and receipts
final inventory
```

## 34.3 Redaction

Do not publish:

```text
tokens
credentials
private absolute paths
raw socket secrets
raw authorization material
unbounded logs
host user details unrelated to the claim
```

Public evidence contains safe hashes and bounded semantic projections.

---

# 35. Attempt Result Schema

Each attempt must emit a structure equivalent to:

```yaml
schema_version: ecomsre.product.v040.preflight-v4-attempt.v1
attempt_id:
attempt_ordinal:
goal_sha256:
source_head:
runtime_surface_sha256:
policy_sha256:
compose_sha256s:
image_commitments:
started_at:
ended_at:
terminal:
failure_stage:
safe_error_code:
probe:
  created:
  provenance:
  process_access:
  sentinel:
  removed_before_sandbox:
sandbox:
  expected_roles:
  observed_roles:
  per_service_health:
  healthy:
traffic:
  warmup:
  planned:
  completed:
  succeeded:
  failed:
  retries:
baseline:
  baseline_id:
  window_count:
  accepted_windows:
  active:
product:
  ready:
  environment_id:
  capability_sha256:
  incident_id:
  diagnosis_id:
  terminal:
  capability_limitations:
  evidence_refs_resolve:
formal_counters:
cleanup:
  terminal:
  containers_remaining:
  networks_remaining:
  volumes_remaining:
  nonowned_unchanged:
evidence_index_sha256:
claim_boundary:
```

---

# 36. Overall Result Schema

Positive result:

```yaml
terminal: goal_complete_checkpoint
product_terminal: ECOMSRE_PRODUCT_V040_LIVE_HARNESS_PREFLIGHT_V4_COMPLETE
starting_main: cc941b51cbff9287b876be49652cd0ad83030474
final_runtime_head:
merged_main:
goal_sha256:
attempts_consumed:
consecutive_pass_attempt_ids:
runtime_surface_sha256:
validation:
review:
ci:
cleanup:
formal_action_counters:
known_limitations:
future_formal_campaign: NOT_AUTHORIZED_BY_THIS_GOAL
```

Exhausted result:

```yaml
terminal: engineering_preflight_exhausted_checkpoint
attempts_consumed: 5
attempts:
remaining_owned_resources:
cleanup:
best_reached_stage:
unresolved_blockers:
formal_action_counters:
future_formal_campaign: WITHHOLD
```

Safety result:

```yaml
terminal: safety_final_checkpoint
reason_code:
attempt_id:
stage:
owned_state:
nonowned_state:
safe_cleanup_completed:
retained_resources:
evidence:
formal_action_counters:
future_formal_campaign: WITHHOLD
```

---

# 37. Definition of Done

Every item is mandatory for the positive terminal.

## Contract and history

- [ ] Exact starting main and tree bound.
- [ ] Exact OTel submodule bound.
- [ ] Goal SHA persisted.
- [ ] PR #95–#100 historical evidence unchanged.
- [ ] PR #100 cleanup fact acknowledged but fresh activation inventory performed.

## Harness design

- [ ] Independent probe namespace.
- [ ] Probe removed before Sandbox start.
- [ ] All image-declared volumes explicitly bound.
- [ ] Copy-up provenance and effective access verified.
- [ ] Effective command resolved from frozen Compose + image.
- [ ] Raw and semantic fingerprints separated.
- [ ] Builtin none endpoint lifecycle handled.
- [ ] Cleanup authority bound at birth.
- [ ] Attempt stage plan frozen before mutation.

## Sandbox

- [ ] Exact expected business-role set.
- [ ] Probe excluded from service enumeration.
- [ ] Per-service health evidence persisted.
- [ ] All 28 expected services healthy.
- [ ] No unknown Sandbox role.

## Traffic and Baseline

- [ ] Warmup gate passes.
- [ ] Settlement interval observed.
- [ ] 30/30 healthy business transactions.
- [ ] Zero retry.
- [ ] Five accepted Baseline windows.
- [ ] Exactly one Active DEMO_ONLY Baseline.

## Product

- [ ] API ready.
- [ ] Worker ready.
- [ ] Environment registered.
- [ ] Connectors verified.
- [ ] Capability Matrix complete.
- [ ] NO_INCIDENT observed.
- [ ] Evidence refs resolve.
- [ ] Capability limitations empty.

## Zero-remediation boundary

- [ ] No fault.
- [ ] No formal Candidate.
- [ ] No Approval.
- [ ] No AttemptAuthorization.
- [ ] No WriteIntent.
- [ ] No Executor invocation.
- [ ] No remediation write.
- [ ] No StepReceipt.
- [ ] No recovery window.
- [ ] No Product Provider call.

## Stability

- [ ] Two consecutive complete passes.
- [ ] Same runtime-relevant source head.
- [ ] Same policy and semantic Compose commitments.
- [ ] Same image commitments.
- [ ] Fresh resources and evidence roots.

## Cleanup

- [ ] Every attempt reaches exact cleanup or is safely terminal with no positive claim.
- [ ] Both successful attempts cleanup CLEAN.
- [ ] Final owned containers = 0.
- [ ] Final owned networks = 0.
- [ ] Final owned volumes = 0.
- [ ] Temporary probe endpoint absent.
- [ ] Non-owned inventory unchanged.

## Integration

- [ ] Full tests pass.
- [ ] Ruff passes.
- [ ] Mypy passes.
- [ ] Independent final review passes.
- [ ] Exact-head CI passes.
- [ ] Final content closure passes.
- [ ] PR Ready.
- [ ] Squash merge succeeds.
- [ ] Merged tree equals reviewed tree.
- [ ] Completion record published.

---

# 38. Claim Boundary After Completion

Safe claims:

```text
- Product v0.4 A–D bounded-remediation components are implemented in main.
- The v4 engineering Harness brought up the pinned local OTel Demo and Product.
- Two consecutive fresh no-fault runs achieved 30/30 healthy traffic,
  an Active DEMO_ONLY Baseline and NO_INCIDENT.
- The Harness removed all owned containers, networks and volumes after both runs.
- No Product fault or remediation action occurred in this Goal.
```

Unsafe claims:

```text
- real Payment recovery succeeded;
- the Product is production self-healing;
- formal remediation accuracy is established;
- the Harness proves long-term reliability or HA;
- all machines or Docker versions behave identically;
- two runs establish a universal zero-failure rate;
- Provider-free Product diagnosis is universally superior;
- the future formal Payment Campaign is authorized.
```

Recommended interview wording:

> I first separated ordinary live-environment bring-up from the formal one-shot
> remediation experiment. The engineering Harness uses run-bound ownership,
> image/Compose command resolution, an independent Kafka volume probe,
> per-service health evidence and birth-bound cleanup receipts. I required two
> consecutive clean no-fault runs before allowing a later formal campaign. This
> Goal itself performed no fault injection or remediation.

---

# 39. Final Autonomous Execution Rules

Codex must follow all of these:

```text
- Treat this Goal as the complete standing authorization for its exact scope.
- Do not ask the user for routine or intermediate approval.
- Do not stop after opening the PR.
- Do not stop after a review.
- Do not stop after CI.
- Do not stop after a failed engineering attempt.
- Do not stop after cleanup.
- Automatically repair in-scope defects and continue while attempts remain.
- Preserve every failed attempt.
- Never weaken a safety property merely to obtain PASS.
- Never mutate an unknown or non-owned resource.
- Never use a global cleanup command.
- Never run the formal Payment fault or remediation.
- Require two consecutive clean passes.
- Merge only after the complete positive Definition of Done.
- End with one final terminal and a complete report.
```

---

# 40. Copyable Activation Prompt

```text
Read and execute:

docs/goals/EcomSRE_Product_v0.4_Live_Harness_Engineering_Preflight_v4_Goal.md

Treat it as the authoritative frozen Goal Contract.

Start from exact repository commit:

cc941b51cbff9287b876be49652cd0ad83030474

with tree:

1b3baa986bcffc00dd4064c8b6c147c30f15e4d3

and pinned OTel Demo submodule:

1755859a9de82c2e5e225be68abc401a5ebf2b4f

I explicitly grant standing prior authorization for every repository edit,
commit, push, Draft PR update, independent review, exact-head CI run, owned-local
Docker lifecycle operation, loopback request, fixed probe/sentinel operation,
attempt-scoped cleanup, bounded engineering retry and positive-completion
squash merge listed in that Goal.

Do not ask me for any additional authorization. Do not pause at phase
boundaries, PR boundaries, review boundaries, CI boundaries, failed engineering
attempts, cleanup boundaries or the first successful pass. Continue
autonomously through in-scope repair and the next attempt until one final Goal
terminal is reached.

You may consume at most five engineering-preflight attempts and must obtain two
consecutive complete passes on the same frozen runtime surface for the positive
terminal.

This authorization does not include a formal Payment campaign, fault injection,
RemediationCandidate, Approval, AttemptAuthorization, WriteIntent, executor
dispatch, remediation write, StepReceipt, recovery window, Provider call,
production, remote infrastructure, arbitrary Shell, unknown-resource mutation
or global Docker cleanup.

Preserve PR #95–#100 and all historical evidence. Start from clean main; do not
merge the historical Draft stack.

Proceed now and stop only at:

- goal_complete_checkpoint;
- engineering_preflight_exhausted_checkpoint;
- safety_final_checkpoint; or
- repository_incompatible_final.
```

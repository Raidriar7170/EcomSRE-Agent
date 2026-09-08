# Product v0.4 engineering preflight v4

The activated [Goal](../goals/EcomSRE_Product_v0.4_Live_Harness_Engineering_Preflight_v4_Goal.md) owns scope and acceptance. This document describes implementation; it does not grant runtime admission.

## State

All five attempts are consumed with zero complete PASS. Terminal: `engineering_preflight_exhausted_checkpoint`, Draft / REVIEW_REQUIRED, future formal campaign WITHHOLD. Attempt 5 created and birth-validated all 28 Sandbox containers, then started astronomy-db and observed healthy before ordinary OOM representation validation failed. Its reviewed cleanup reached CLEAN with owned zero, unchanged non-owned inventory/images and free ports. Attempt 1 retains its historical NONOWNED_DRIFT; every original FAILED/BLOCKED_SAFETY remains unchanged. Full service health, traffic and Product were not reached. No new attempt is authorized.

The versioned policy treats Mounts array ordering as serialization while retaining every keyed mount field. Docker readiness uses only `version/info` as Goal section 18 requires. For each historical attempt, the authorized exact-source Product build followed CI and immediately preceded its new double baseline. A diagnostic `system df` probe was performed before this restriction was reconciled; it is preserved as a scoped read-only deviation and is not part of the admitted execution path. A narrowly committed recovery proof can acknowledge the prior exact owned closure after an independently observed daemon event without changing the old failed result or accepting its non-owned comparison as unchanged. Every new attempt still requires its own complete admission.

## Components and authority

- `common.py`: frozen base and Goal bindings, deterministic hashing, create-once private evidence.
- `docker.py`: local daemon/context binding and complete double-enumerated read captures. The general transport is internal plumbing, not mutation authority.
- `identity.py`: independently resolved Compose/image process semantics, immutable configuration projection, exact role-to-birth-ID health map.
- `processes.py`, `copyup.py`, `sentinel.py`: selectively adapted pure probe parsers/protocols. No old driver, journal, cleanup plan or attempt fuse is imported.
- `resources.py` coordinator: freezes a complete plan before birth, journals intent before each mutation and immutable receipt afterward, and binds cleanup targets to the verified birth IDs. Restart recovery reads receipts and observes postconditions before deciding whether any still-unexecuted command is eligible.

Actual `ProductV030SandboxEnvironment.resolve()` provides read-only baseline Compose rendering. v4 rewrites resource namespaces, pins image references, binds every image-declared volume and re-expands the final model before admission. No upstream files change. The separate probe has no Sandbox Compose labels and must be removed, including its builtin `none` endpoint, before Sandbox creation.

Copy-up uses fixed read-only `docker cp <birth-id>:<declared-path> -`, without `-L` or host extraction. Bounded archives are compared against immutable OCI-derived image metadata. The fresh volumes have no other writers. The fixed census proves the probe's two admitted processes and all credentials; the fixed sentinel writes only unique files under the three verified paths and removes them.

Product API/Worker have no remediation profile, Docker socket or remediation mount. They connect directly to fixed Prometheus, Jaeger and OpenSearch endpoints on the attempt-owned Sandbox network. Shared Sandbox networks are removed only after Product and Sandbox containers. Future remediation profile isolation is checked through separate static Compose expansion, never runtime activation.

## Independent design review

Read-only review required full network/platform and running-process anchors, typed probe OOM evidence, partial-create recovery, append-only intent reconciliation, strict non-owned identity comparison and creation-receipt-bound birth authority. These are implemented. Subsequent review required complete pre-start identity checks, budget consumption on observed creation even when birth validation fails, diagnostic failure isolation, and rejection of previous project resources before a new attempt. The fixes are covered by focused tests and historical head/tree-bound review. The budget is now exhausted; no further runtime admission is open.

Budget reservation checks prior cleanup and rejects an identical failed surface before any new create. Actual resource creation consumes the allowance independently of cleanup eligibility. Interrupted operations reconcile original intents and observations without repeating mutations. Failure diagnostics cannot skip safe cleanup; cleanup and closure failures retain separate typed records and the original attempt failure.

Docker image compatibility normalization is limited to documented absent/default Config fields, retaining raw captures. Immutable index/platform, RootFS and effective image configuration commitments remain verified. The private flagd input is readable through the planned host UID; no runtime permission repair is performed. Build-time source permissions allow the planned Product UID to read the copied application.

## Evidence and claims

Private runtime evidence is append-only, mode 0600 under directories 0700. Public files contain safe commitments and accurate progress. The frozen image records are selected immutable image facts from hash-verified historical observations; they provide no old resource identity or execution authority. Every attempt freshly verifies current image commitments and resources. All historical outcomes remain unchanged.

A pass requires complete traffic, baseline, diagnosis, zero formal actions and verified cleanup. Two consecutive complete passes must bind the same runtime surface and Product image. No complete live PASS or formal acceptance is claimed.

## Exact index identity and cleanup recovery

A container may retain the frozen immutable image index while platform-selective image inspect returns the selected manifest. The added birth path requires the exact digest reference, exact platform descriptor and linux/arm64. It does not admit tags or an unbound index. `cleanup_resume.py` binds an independently reviewed cleanup head/tree to the original plan/source and replays only existing create receipts, then revalidates fresh identity before exact deletion. Its command gate allows only cleanup verbs. Attempt 2 exercised this path without starting the Probe or creating a replacement.

## Explicit network defaults

Network birth accepts the observed exact two IP-family options only for bridge/local, IPv4 enabled, IPv6 disabled, and non-config-only networks. Existing checks still reject internal, ingress, attachable and populated networks. Additional or changed options remain rejected. Raw birth fields are never normalized; fresh cleanup compares complete network identity and checks both endpoints and container attachments before deletion.

## Explicit unset and arm64 variant representation

Policy v3 admits bare environment entries only for keys explicitly null in the frozen service. Null removes an inherited value; it is never equivalent to an empty or nonempty assignment. Duplicate and unknown bare entries fail closed. Image index acceptance continues to bind the exact frozen selected manifest and permits only linux/arm64 with an absent or v8 variant, as observed in the fresh image and container records. Raw evidence remains intact.


## Attempt 5 cleanup-only OOM evidence

Policy v4 adds a separately reviewed exception for the single exact Attempt 5 astronomy-db identity. It retains the normal Probe-only running OOM policy. A fixed read-only bash census executes once with numeric UID/GID 1000 and records actual PID1 credentials rather than assuming Probe credentials. Two complete matching cgroup-v2 reads verify memory.max, memory.oom.group and oom_score_adj; raw output is sealed before parsing.

Every proof reload revalidates original raw protocol, before/after captures, birth/create/start chains, cleanup admission, daemon and derived process-lifetime digests. Full running network entries match start, before and after. Fresh stop/remove authorization checks the same birth and process lifetime; only the stopped PID zero lifecycle is accepted for removal. Missing or altered proof fails closed. An existing fully validated proof may be reused without a second exec; an incomplete one-shot intent prevents replay.

Independent cleanup-only ALLOW binds `1b2fd87879b3f97aa3156387e370609dc735935f`. The actual census and nine cleanup command chains passed independent evidence review. This proves safe cleanup of a failed attempt, not ordinary runtime compatibility or end-to-end acceptance. Final validation bindings are recorded in the final review and PR closure.

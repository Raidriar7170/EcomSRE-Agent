# Product v0.4 engineering preflight v4

The activated [Goal](../goals/EcomSRE_Product_v0.4_Live_Harness_Engineering_Preflight_v4_Goal.md) owns scope and acceptance. This document describes implementation; it does not grant runtime admission.

## State

Two attempts consumed 2/5 and failed before Probe start. Attempt 1 retains its original non-owned bridge drift. Attempt 2 failed on an exact index/platform representation mismatch; reviewed cleanup subsequently returned CLEAN with zero owned resources and unchanged non-owned resources. Neither failed attempt is rewritten as PASS. Full repository tests on runtime head `c71f954` passed 6632 tests with 21 skips and both CI workflows passed. The current identity/cleanup repair has 140 focused tests passing and awaits final published-head verification.

Policy v2 treats Mounts array ordering as serialization while retaining every keyed mount field. Docker readiness uses only `version/info` as Goal section 18 requires. An authorized exact-source Product build is scheduled after CI and immediately before the new double baseline. A diagnostic `system df` probe was performed before this restriction was reconciled; it is preserved as a scoped read-only deviation and is not part of the admitted execution path. A narrowly committed recovery proof can acknowledge the prior exact owned closure after an independently observed daemon event without changing the old failed result or accepting its non-owned comparison as unchanged. Every new attempt still requires its own complete admission.

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

Read-only review required full network/platform and running-process anchors, typed probe OOM evidence, partial-create recovery, append-only intent reconciliation, strict non-owned identity comparison and creation-receipt-bound birth authority. These are implemented. Subsequent review required complete pre-start identity checks, budget consumption on observed creation even when birth validation fails, diagnostic failure isolation, and rejection of previous project resources before a new attempt. The fixes are covered by focused tests; final admission remains withheld until the reviewer binds the implementation head/tree.

Budget reservation checks prior cleanup and rejects an identical failed surface before any new create. Actual resource creation consumes the allowance independently of cleanup eligibility. Interrupted operations reconcile original intents and observations without repeating mutations. Failure diagnostics cannot skip safe cleanup; cleanup and closure failures retain separate typed records and the original attempt failure.

Docker image compatibility normalization is limited to documented absent/default Config fields, retaining raw captures. Immutable index/platform, RootFS and effective image configuration commitments remain verified. The private flagd input is readable through the planned host UID; no runtime permission repair is performed. Build-time source permissions allow the planned Product UID to read the copied application.

## Evidence and claims

Private runtime evidence is append-only, mode 0600 under directories 0700. Public files contain safe commitments and accurate progress. The frozen image records are selected immutable image facts from hash-verified historical observations; they provide no old resource identity or execution authority. Every attempt freshly verifies current image commitments and resources. All historical outcomes remain unchanged.

A pass requires complete traffic, baseline, diagnosis, zero formal actions and verified cleanup. Two consecutive complete passes must bind the same runtime surface and Product image. No live result is currently claimed.

## Exact index identity and cleanup recovery

A container may retain the frozen immutable image index while platform-selective image inspect returns the selected manifest. The added birth path requires the exact digest reference, exact platform descriptor and linux/arm64. It does not admit tags or an unbound index. `cleanup_resume.py` binds an independently reviewed cleanup head/tree to the original plan/source and replays only existing create receipts, then revalidates fresh identity before exact deletion. Its command gate allows only cleanup verbs. Attempt 2 exercised this path without starting the Probe or creating a replacement.

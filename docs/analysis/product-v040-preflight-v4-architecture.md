# Product v0.4 engineering preflight v4

The activated [Goal](../goals/EcomSRE_Product_v0.4_Live_Harness_Engineering_Preflight_v4_Goal.md) owns scope and acceptance. This document describes implementation; it does not grant runtime admission.

## State

Implementation in progress. No v4 runtime resource has been created; 0/5 attempts consumed. The initial 94 identity and role tests pass. Full tests, CI, complete coordinator and Live Admission review remain pending.

## Components and authority

- `common.py`: frozen base and Goal bindings, deterministic hashing, create-once private evidence.
- `docker.py`: local daemon/context binding and complete double-enumerated read captures. The general transport is internal plumbing, not mutation authority.
- `identity.py`: independently resolved Compose/image process semantics, immutable configuration projection, exact role-to-birth-ID health map.
- `processes.py`, `copyup.py`, `sentinel.py`: selectively adapted pure probe parsers/protocols. No old driver, journal, cleanup plan or attempt fuse is imported.
- Planned coordinator: freezes a complete plan before birth, journals intent before each mutation and immutable receipt afterward, and binds cleanup targets to the verified birth IDs. Restart recovery reads receipts and observes postconditions before deciding whether any still-unexecuted command is eligible.

Actual `ProductV030SandboxEnvironment.resolve()` provides read-only baseline Compose rendering. v4 rewrites resource namespaces, pins image references, binds every image-declared volume and re-expands the final model before admission. No upstream files change. The separate probe has no Sandbox Compose labels and must be removed, including its builtin `none` endpoint, before Sandbox creation.

Copy-up uses fixed read-only `docker cp <birth-id>:<declared-path> -`, without `-L` or host extraction. Bounded archives are compared against immutable OCI-derived image metadata. The fresh volumes have no other writers. The fixed census proves the probe's two admitted processes and all credentials; the fixed sentinel writes only unique files under the three verified paths and removes them.

Product API/Worker have no remediation profile, Docker socket or remediation mount. They connect directly to fixed Prometheus, Jaeger and OpenSearch endpoints on the attempt-owned Sandbox network. Shared Sandbox networks are removed only after Product and Sandbox containers. Future remediation profile isolation is checked through separate static Compose expansion, never runtime activation.

## Independent design findings to close before live admission

1. Add exact network/platform bindings and stage-aware endpoint validation.
2. Bind running process start/restart identity, distinguishing first start, stable running and exact stop.
3. Replace the OOM boolean placeholder with a same-ID, same-daemon, stage-bound effective cgroup proof; preserve raw false/null differences.

These findings are not a terminal and do not authorize a live attempt. Role readiness must come from current fixed endpoint/dependency observations, not ungrounded booleans.

## Evidence and claims

Private runtime evidence is append-only, mode 0600 under directories 0700. Public files contain safe commitments and accurate progress. The frozen image records are selected immutable image facts from hash-verified historical observations; they provide no old resource identity or execution authority. Every attempt freshly verifies current image commitments and resources. All historical outcomes remain unchanged.

A pass requires complete traffic, baseline, diagnosis, zero formal actions and verified cleanup. Two consecutive complete passes must bind the same runtime surface and Product image. No live result is currently claimed.

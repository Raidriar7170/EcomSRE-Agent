# Product v0.4 engineering preflight v4 evidence

Attempt 1 failed before Probe start. The Probe and three named Kafka volumes were exactly removed. Its immutable result remains `FAILED / BLOCKED_SAFETY`; the subsequent closure record is `OWNED_REMOVED_NONOWNED_DRIFT`, not CLEAN.

The copy-up pre-read guard compared raw Mounts list order. Raw captures show only array order differed, with immutable mount contents unchanged. Separately, the builtin bridge network ID/Created changed across first volume creation. Docker backend logs directly show that first request woke the idle VM. VM startup causing bridge reinitialization is a supported inference; no bridge restoration or normalization was performed.

Independent review permits a separately bound stabilization repair under Goal section 8.3. All owned resources are absent; the next attempt remains withheld until repaired-head review, tests and CI complete. Budget: 4/5 consumed, consecutive passes 0. No Sandbox or Product start, traffic, baseline, diagnosis, Provider or formal action occurred.

[Attempt 1](attempt-01.json) preserves the failed public summary. Private raw records remain append-only; public commitments disclose no tokens or private absolute paths. This is not a final or positive preflight result.

A separate post-attempt read-only `system df` diagnostic exceeded section 18's version/info-only wake restriction. Its evidence and observed idle-VM wake are retained; it created no runtime resource. The command was removed from the execution path. Subsequent admission uses the authorized build followed by version/info and a fresh baseline, subject to independent review and complete checks.

## Attempt 2 and reviewed cleanup

[Attempt 2](attempt-02.json), on `c71f954`, failed at Probe birth validation before any start. The container retained the exact frozen Kafka index in `Image`, while its descriptor selected the correct arm64 manifest. The validator had omitted that bound representation. The original `FAILED / BLOCKED_SAFETY` result remains unchanged.

The independently reviewed `3f82e35` repair admits only the exact frozen digest with the exact arm64 descriptor and restores the original creation receipt's birth authority. Cleanup-only recovery removed the created Probe and three volumes. Eight create/remove receipts were independently verified; two final captures show zero owned resources, unchanged non-owned inventory and images, and free loopback ports. Later cleanup is `CLEAN`; the live attempt remains failed.

Reviewer Sagan: implementation code gate ALLOW / Must Fix 0 / Claim Accuracy PASS. The next full attempt still requires the published-head checks and exact-head CI. Latest completed full tests (`c71f954`): 6632 passed, 21 skipped; both CI workflows passed. Current repair: 140 focused tests passed. At that boundary two attempts were consumed, with no complete PASS, no Sandbox or Product start, no formal action.

## Attempt 3 and exact network recovery

[Attempt 3](attempt-03.json), runtime head `5bc530b`, passed the complete Probe qualification: copy-up provenance, effective OOM/cgroup binding, process census, sentinel, and removal before Sandbox creation. It then failed at birth validation of the first Sandbox network: Docker returned the exact ordinary IPv4-enabled/IPv6-disabled options although network create supplied no driver options. No Sandbox or Product container was created or started.

The original result remains `FAILED / BLOCKED_SAFETY`. Reviewed repair `346f081` accepts only that exact bridge/local default combination, with all other network safety checks retained. Recovery used the original receipt and deleted the sole retained network; double final checks show owned zero and unchanged non-owned inventory/images with free ports. Subsequent cleanup is `CLEAN`, without rewriting the failed attempt.

At that boundary budget was 3/5 consumed, zero consecutive complete PASS. The repair has 154 focused tests passing plus Ruff and scoped mypy. Full validation and independent published-head admission are pending before Attempt 4. Previous runtime `5bc530b` had successful exact-head CI; local full pytest on its independently verified equivalent runtime tree `20c0ce4` passed 6633 tests with 21 skips. Those results do not validate the later network repair.

## Attempt 4 and created-container recovery

[Attempt 4](attempt-04.json), runtime head `85ca642`, passed Probe qualification/removal and Sandbox storage creation. The complete 28-container create succeeded, but five birth validations failed before any Sandbox start: four bare `_JAVA_OPTIONS` values represent explicitly null/unset Compose variables, and astronomy-db reported the exact frozen arm64 manifest with its v8 variant. The remaining 23 Sandbox containers were initially validated and removed.

Reviewed repair `679df7a` preserves unset semantics, admitting bare keys only where the frozen service explicitly specifies null, and rejects assigned or duplicate substitutes. It accepts only absent/v8 arm64 variant alongside the exact index/platform binding. All 28 original receipt rows pass the repaired full birth checks. Recovery then exactly removed the five retained containers and four volumes. Double final inventories, image checks and listeners confirm CLEAN cleanup with owned zero and unchanged non-owned state. Original `FAILED / BLOCKED_SAFETY` remains immutable.

Four attempts are consumed with zero complete PASS. Only one attempt remains, so the two-pass positive gate cannot be met within this budget. The Goal still requires completion of the authorized final attempt and publication of its true terminal; no extra attempt is authorized. Current repair has 155 focused tests plus Ruff and scoped mypy passing; full frozen-head validation remains required before Attempt 5. Previous runtime `85ca642` passed 6647 local full tests (21 skips) and exact-head CI.

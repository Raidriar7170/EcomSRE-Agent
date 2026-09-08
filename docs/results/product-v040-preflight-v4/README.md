# Product v0.4 engineering preflight v4 evidence

Attempt 1 failed before Probe start. The Probe and three named Kafka volumes were exactly removed. Its immutable result remains `FAILED / BLOCKED_SAFETY`; the subsequent closure record is `OWNED_REMOVED_NONOWNED_DRIFT`, not CLEAN.

The copy-up pre-read guard compared raw Mounts list order. Raw captures show only array order differed, with immutable mount contents unchanged. Separately, the builtin bridge network ID/Created changed across first volume creation. Docker backend logs directly show that first request woke the idle VM. VM startup causing bridge reinitialization is a supported inference; no bridge restoration or normalization was performed.

Independent review permits a separately bound stabilization repair under Goal section 8.3. All owned resources are absent; the next attempt remains withheld until repaired-head review, tests and CI complete. Budget: 2/5 consumed, consecutive passes 0. No Sandbox or Product start, traffic, baseline, diagnosis, Provider or formal action occurred.

[Attempt 1](attempt-01.json) preserves the failed public summary. Private raw records remain append-only; public commitments disclose no tokens or private absolute paths. This is not a final or positive preflight result.

A separate post-attempt read-only `system df` diagnostic exceeded section 18's version/info-only wake restriction. Its evidence and observed idle-VM wake are retained; it created no runtime resource. The command was removed from the execution path. Subsequent admission uses the authorized build followed by version/info and a fresh baseline, subject to independent review and complete checks.

## Attempt 2 and reviewed cleanup

[Attempt 2](attempt-02.json), on `c71f954`, failed at Probe birth validation before any start. The container retained the exact frozen Kafka index in `Image`, while its descriptor selected the correct arm64 manifest. The validator had omitted that bound representation. The original `FAILED / BLOCKED_SAFETY` result remains unchanged.

The independently reviewed `3f82e35` repair admits only the exact frozen digest with the exact arm64 descriptor and restores the original creation receipt's birth authority. Cleanup-only recovery removed the created Probe and three volumes. Eight create/remove receipts were independently verified; two final captures show zero owned resources, unchanged non-owned inventory and images, and free loopback ports. Later cleanup is `CLEAN`; the live attempt remains failed.

Reviewer Sagan: implementation code gate ALLOW / Must Fix 0 / Claim Accuracy PASS. The next full attempt still requires the published-head checks and exact-head CI. Latest completed full tests (`c71f954`): 6632 passed, 21 skipped; both CI workflows passed. Current repair: 140 focused tests passed. Two attempts consumed, no complete PASS, no Sandbox or Product start, no formal action.

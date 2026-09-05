# Product v0.4 PR-E preparation source review

Verdict: **PASS**. Must Fix: **0**. Claim Accuracy: **PASS**.
Should Fix, Nice to Have and Scope Creep Warning: none.

Independent read-only Reviewer inspected the PR-E worktree against
`cc941b51cbff9287b876be49652cd0ad83030474`. The Reviewer performed no tracked edits,
Docker calls, Provider calls or live experiment. Primary-agent test outputs are
reported as such, not as Reviewer-executed checks.

Resolved findings cover private build context registry inclusion, explicit local
Docker context, real checkout business oracle, cancellation and cross-process
cleanup exclusion, full source-set and actual Git provenance, public positive
parent bindings, complete-negative versus blocked classification, quiescent
writer admission for export, residual cleanup counts and preservation of prior
non-owned change/unknown observations. Focused regressions cover these paths.

This verdict covers preparation source only. Actual ownership, Docker network
and filesystem isolation, control readback, healthy Active Baseline, exact-head
CI, one-shot freeze and final live evidence require their own recorded checks.
It is not the formal pre-execution PASS and does not claim measured recovery.

Test-environment follow-up: independent Reviewer executed the host-runtime test
file with outer PYTHONPATH unset: 11 passed, exit 0. The subprocess fixture now
sets its exact source path, working directory and a 10-second timeout without
inheriting control credentials. The lock assertion and production implementation
are unchanged. PASS / Must Fix 0 / Claim Accuracy PASS. New exact-head CI remains
required.

First actual no-fault preparation: invalid observer tmpfs flow-list caused
startup failure. Cleanup was CLEAN, Baseline restored, all owned counts zero,
non-owned unchanged, and no formal freeze/fault/attempt existed. The entire
failed preparation was retained with its preservation index; see the
[error analysis](../results/product-v040-payment-live-error-analysis.md).

Independent repair review: PASS / Must Fix 0 / Claim Accuracy PASS. With outer
PYTHONPATH unset, Reviewer executed private-process plus host-runtime tests:
24 passed, exit 0. Review covers quoted/resolved tmpfs admission, closed role
bootstrap setting umask before application dispatch/storage creation, real
SQLite DB/WAL/SHM fixture modes, and the Product/ledger permission gate. Actual
runtime permission measurements remain pending in a fresh preparation root.

## Pre-freeze ingress repair review

The read-only independent reviewer inspected the fixed Product ingress,
loopback publication on the existing observer, unchanged API/Worker internal
network, sequential bootstrap, host readiness and pre-cleanup diagnostics.
Initial review found one Must Fix: per-I/O timeouts did not establish a total
request deadline. The corrected handler covers inbound body, its sole upstream
request and response stream with a 25-second asyncio deadline and returns 504
without retransmission. Fourteen focused ingress tests pass, including slow
inbound and delayed upstream cases.

Reviewer disposition after correction: PASS; Must Fix 0; Claim Accuracy PASS.
This is source review only. The reviewer did not run Docker and does not claim
real ingress reachability, Baseline readiness, final-head CI or formal live
acceptance. The diagnostic API exit remains unexplained. Actual formal freeze
requires the separate pre-execution review and all existing live gates.

## Fixed application warmup review

Following retained preparation 003, the reviewer inspected one pre-freeze
warmup group with three distinct transactions, per-POST create-once intent,
120-second total request deadline, bounded private response bodies and digests,
no replay and no replacement group. The original 30/30 healthy control,
360-second observation and five successful Baseline windows remain unchanged.
The fixed 330-second quiet interval is not a claim of proven telemetry expiry;
NO_INCIDENT remains an actual subsequent gate. Interrupted aggregate rows do
not replace the separate intent/result evidence.

The reviewer also inspected proven-owned Demo log capture before cleanup,
including per-container failure retention. Final source disposition: PASS;
Must Fix 0; Claim Accuracy PASS. This review ran no Docker environment and is
not formal pre-execution approval or measured success.

## Safety-checkpoint evidence review

The independent read-only reviewer checked the pre-execution safety-checkpoint
JSON, Chinese Human Brief, failure analysis, progress and development-status
updates against retained private snapshots, read-only database counts and live
GitHub status. Review PASS; Must Fix 0; Claim Accuracy PASS.

The review preserves the original cleanup observation separately from the later
builtin bridge identity replacement with UNKNOWN attribution, and treats the
three uncovered Kafka image VOLUMEs as a distinct unresolved ownership gap.
Healthy 30/30 and Active Baseline are observed; network/NO_INCIDENT gates were
not reached. Formal manifest, candidate, approval, authorization, attempt,
write, receipt and window counts remain zero. Code head a0e8aab has successful
CI 33992709388; PR #95 remains Draft. This review supports checkpoint document
publication only, not environment startup or PR merge.

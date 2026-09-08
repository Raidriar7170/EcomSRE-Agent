# Product v0.4 engineering preflight v4 — exhausted

Terminal: **`engineering_preflight_exhausted_checkpoint`**. All **5/5** attempts are consumed, with **0 complete PASS**. PR disposition is **Draft / REVIEW_REQUIRED**; future formal campaign **WITHHOLD**. No merge or extra attempt is authorized by this terminal.

The best observed progress was Attempt 5: the Probe qualification/removal completed, all 28 Sandbox containers were created and birth validated, and astronomy-db started and was observed healthy. Its first running OOM representation check failed. Full Sandbox start/health, warmup, traffic, Product readiness, baseline and NO_INCIDENT were not reached.

All attempt-owned containers, networks and volumes were removed under reviewed authority. Attempts 2–5 subsequently reached `CLEAN`; Attempt 1 remains `OWNED_REMOVED_NONOWNED_DRIFT` because the builtin bridge identity changed during an observed idle-VM wake. No historical attempt result was rewritten: all five original results remain `FAILED / BLOCKED_SAFETY`.

| Attempt | Failure | Separately reviewed recovery |
| --- | --- | --- |
| [1](attempt-01.json) | Copy-up pre-read compared Mounts array order; no Probe start | Exact Probe and three-volume removal; builtin bridge ID/Created drift retained |
| [2](attempt-02.json) | Probe Image returned exact frozen index alongside bound arm64 descriptor | Exact index/platform representation validation; Probe and three-volume cleanup CLEAN |
| [3](attempt-03.json) | First Sandbox network reported explicit IPv4 true / IPv6 false defaults | Exact ordinary bridge/local option validation; network cleanup CLEAN |
| [4](attempt-04.json) | Five of 28 created-container birth checks rejected explicit unset environment or arm64 v8 descriptor | Exact unset/descriptor repair; 28 original birth rows validated; remaining five containers/four volumes cleanup CLEAN |
| [5](attempt-05.json) | astronomy-db changed OomKillDisable false → null on its first start | One exact-container nonroot read-only cgroup census; proof-gated stop/remove and storage cleanup CLEAN |

The Attempt 5 cleanup policy is limited to its exact identity and running-to-stopped cleanup. It does not change ordinary runtime OOM admission. The census binds raw protocol output, two matching reads, actual PID1 credentials, daemon, birth/create/start receipts, process lifetime, full running endpoints, source and review admission. Tests reject proof/raw tampering and replacement lifetimes. Cleanup used no force, kill, prune, global reset or unknown target.

[Supplemental commitments](supplemental-attempt-evidence.json) supply per-attempt policy/Compose/image hashes, failure stages, separately labeled original and repaired cleanup facts, timing basis and formal-observation boundaries. The immutable failed summaries and their original raw-index commitments remain unchanged; additional evidence uses append-only addenda.

No Product runtime, fault injection, remediation or formal campaign occurred. Provider calls are zero in the attempt records. Product database counters and capability observations are **NOT_REACHED / null**, because the Product stage never ran; they are not presented as measured database zeros.

One post-Attempt-1 read-only `docker system df` diagnostic exceeded Goal section 18's version/info-only wake restriction. The command and observed VM wake are retained; it created no runtime resource, did not consume an extra attempt, and was removed from the execution path. VM wake causing bridge reinitialization is a supported inference, not a normalized identity fact.

See [final result](final-result.json), [evidence manifest](evidence-manifest.json), [Human Brief](HUMAN_BRIEF.md), and [final review](../../external-reviews/product-v040-preflight-v4-final-review.md). Validation and exact-head CI are bound in the final review and Draft PR. The complete fresh SHA-256 content closure is published by hash in the PR body to avoid a tracked manifest self-hash cycle. Offline tests and CI do not establish live acceptance.

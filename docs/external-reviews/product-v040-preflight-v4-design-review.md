# Independent read-only v4 design review

Reviewer: Sagan. Scope: new v4 harness and its execution/cleanup boundaries. No live mutation was performed by the reviewer.

The review identified and drove repairs for exact platform/network/process identities; typed probe OOM evidence; partial-create birth recovery; restart-safe journal authority; non-owned network/OOM preservation; create intent/receipt chains; pre-start identity revalidation; observed-creation budget accounting; diagnostic/cleanup exception evidence; and preexisting project-resource rejection.

At the latest completed review, the previous three Must Fix items were resolved and the reviewer independently observed 121 focused tests passing. One remaining preexisting-resource gate was requested and has since been implemented with additional tests. Current focused suite: 130 passed.

Verdict: REVIEW_REQUIRED
Must Fix: pending frozen-head re-review
Should Fix: none recorded
Nice to Have: none recorded
Scope Creep: none identified
Claim Accuracy: no live or formal claim made
Live Admission: WITHHOLD

This record will be updated from the frozen-head review before Attempt 1. Tests and CI remain independent admission conditions.


## Frozen implementation admission and first failure

Sagan approved `d058ea685a648084dffcc44a290ab58cc058ced2`, tree `5e7711fdf7ecd3be0936a3e09802a254a8b15fb0`: PASS / Must Fix 0 / Claim Accuracy PASS / Live Admission ALLOW for the code-review gate. Local checks and exact-head CI then passed before Attempt 1.

After Attempt 1, independent review confirmed mount ordering was the functional guard issue, all eight mutation receipts matched exact create/remove commands, and no owned resource remained. Raw inventory proves real builtin bridge replacement. Independent backend-log inspection proves the idle VM start event triggered by first volume creation. Section 8.3 permits a separately reviewed stabilization repair; no unconditional Attempt 2 approval was granted and the old FAILED/BLOCKED_SAFETY result must remain immutable.

Current repaired-head Live Admission: WITHHOLD pending review and full gates.


## Stabilization repair review

The reviewer accepted the narrowly committed prior-attempt recovery proof, independently verifying all 29 bound evidence files, four births, eight mutation receipts and the daemon event. Two Must Fix items were then raised: respect Goal section 18's version/info-only wake restriction, and relax only raw Mounts array ordering while preserving complete NetworkSettings and every other raw field.

Both fixes are implemented. The execution path uses version/info only, and raw created-container comparison sorts only Mounts before complete equality. Alias, IPAMConfig and NetworkID drift remain rejected. The earlier read-only system-df diagnostic is retained as a scope deviation; it created no runtime resource and consumed no attempt. It is not used in the admitted execution path. New runtime admission remains pending frozen-head review and all checks.

## Attempt 2 identity repair and cleanup review

The frozen `c71f954c65a2347634523ae07833137377a326d2` implementation had received ALLOW / Must Fix 0 before its complete offline and CI gates. Attempt 2 then failed on exact index versus selected platform identity before Probe start.

Sagan independently approved cleanup-only repair HEAD `3f82e3513e71e03689efea640e5ccef7d95bae80`, tree `358c3c0f3e693641b1c85404a9446b4b037a76a6`. The original receipt passed full repaired birth validation. The reviewer verified all eight create/remove chains, fresh identity checks for four removals, double final owned-zero/non-owned-unchanged captures, unchanged images and free listeners. Original failure remained intact.

Implementation code gate: ALLOW. Must Fix: 0. Claim Accuracy: PASS. This covers the implementation repair; final publication-head review, offline checks, exact-head CI and new image/fresh baseline admission remain required before Attempt 3.

## Attempt 3 network cleanup review

Before Attempt 3, Sagan bound the publication head `5bc530b1aa50be37b9eb51173aab94d12ff735ba` and tree `3ddd40daaa94222e7f86cb2fb56e6b31d3a17fe9`: code gate ALLOW / Must Fix 0 / Claim Accuracy PASS. The one-line attempt-count correction was independently proven runtime/tests/config/lock neutral; local full-test applicability retained tested_head `20c0ce4`. Exact-head GitHub CI subsequently passed.

The network-options repair `346f081b281faba35e4f71db253a39c59ac11a09`, tree `e458502967b6a33c6dd7152a0024746100c14adb`, received cleanup-only ALLOW / Must Fix 0. The original create-default receipt passed the repaired validator without replacement. This disposition covered only recovery and exact removal of the retained network, with no start or new attempt. New runtime admission remains pending published-head review and full gates.

After cleanup, Sagan independently verified the new exact remove-default chain and fresh preflight, owned-zero double captures, unchanged non-owned inventory/images and free ports. Implementation code gate for `346f081`: ALLOW / Must Fix 0 / Claim Accuracy PASS. Full published-head gates remain separate.

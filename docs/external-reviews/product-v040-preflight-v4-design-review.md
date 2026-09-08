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

# DTA v2.2.5 Admission Reconciliation Review

## Scope

- Review type: independent, read-only, post-terminal and pre-replacement
- Reviewed base HEAD: `70a91152696e1e106e702c490a14a59d124da2cb`
- Reviewed scope: the uncommitted admission-reconciliation diff only
- Docker calls by reviewer: `0`
- Provider calls by reviewer: `0`
- Repository writes by reviewer: `0`
- Formal reconciliation writes before review closure: `0`

## Review history

The first review rejected five fail-closed gaps: an unjustified synthetic
`CLEAN` path, no immutable binding to the original exception, partial Git
audit validation, a nested reconciliation-filename seal bypass, and a primary
root symbolic-link bypass.

The repair removed the synthetic runtime path, fully bound the original Git
command log, process audit, and stdout/stderr streams, sealed both files and
directories while excluding only the root reconciliation output, and rejected
private or campaign-root symbolic links. It also bound the original Codex
call, failed command event, and output record as three adjacent raw JSONL
records.

The next review found one remaining issue: a caller-selected owner-only JSONL
could mint its own digests. The final repair removed the rollout-path CLI and
API inputs and froze the actual session path suffix, session ID, ordinals, call
ID, prefix digest, three raw-record digests, command digest, and failure-output
digest. The structured proof must match every frozen field.

## Replacement boundary

The reconciliation is eligible only for the already-recorded `campaign-0001`
admission terminal. It proves that the pinned upstream file was missing before
the Docker boundary and preserves the original terminal bytes. Replacement
still requires no baseline capture, fault capture, Provider shadow, or paired
result; a complete sealed evidence tree; `baseline_restored=true`; `CLEAN`;
and zero non-owned changes.

No reconciliation, replacement claim, `campaign-0002`, Docker call, or
Provider call was created during review.

## Verification

- Real frozen Codex proof: `PASS`
- Focused real-fault suite: `40 passed`
- Complete `tests/dta_v22`: `304 passed, 1 skipped`
- Ruff: `PASS`
- mypy: `PASS`
- Historical DTA v2.2 and v2.2.5 gates: `PASS`
- `git diff --check`: `PASS`

## Final verdict

```text
Must Fix: 0
Claim Accuracy: PASS
```

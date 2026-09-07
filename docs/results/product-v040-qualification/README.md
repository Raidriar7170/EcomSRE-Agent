# Product v0.4 owned-local no-fault qualification

Disposition: **Draft / REVIEW_REQUIRED**. This successor begins at
`8a7a39c2f536fae5262796dff9bda5b833cb99c7`, after PR #96 was merged only into
`codex/product-v040-live-payment`. PR #95 remains Draft; main is unchanged.

The scoped authorization SHA-256 is
`5aab1de4bafffbac465e3d14e79fe689b50e9b9418bfee3acae0f3b3c4576f5d`.
It authorizes a fresh owned-local **no-fault** qualification, never a formal
Payment campaign. Historical PR #95/#96 public and private evidence is frozen.
The earlier preparation-004 divergence remains UNKNOWN; the later Docker
Desktop bridge recreation is separate temporal evidence.

## Execution contract

The new `scripts/product/qualification_v040` namespace creates a unique private
root, stabilizes and binds the Docker daemon, freshly inspects pinned ARM64
images and Config.Volumes, and seals the complete stage plan before creating
resources. All six named volumes, three networks, 28 sandbox services, three
Product reader services and the temporary Kafka probe have explicit roles.
The three Kafka overlays are `/etc/kafka/secrets`, `/mnt/shared/config` and
`/var/lib/kafka/data`. No executor or remediation gateway is part of this plan.

Two complete inventory inspect passes and host-bind measurements surround each
capture. Same-name volume replacement compares full inspect fingerprints, not
just names. Every post-copyup checkpoint measures all three Kafka mounts twice;
original seed content is immutable, secrets cannot gain entries, generated
configuration binds once at Kafka identity, and new Kafka data retains required
ownership and safe modes. Captures are bounded to 16 MiB and 4096 archive entries
per path. Exceeding a bound or racing data enumeration is a typed blocker, not
permission to skip evidence or enlarge limits during a run.

The sentinel uses the Kafka image runtime identity, only newly owned volumes,
and is removed before healthy traffic. Copyup or access mismatch stops execution;
no in-place volume repair is permitted. Cleanup uses exact bound resources only.
A failed or incomplete birth may require manual intervention rather than adoption.

First-divergence is create-once. Static prohibited-action budgets are distinct
from observed database counters; NONZERO and UNKNOWN observations are preserved
before assertions. No fault injection, formal manifest/candidate/approval,
AttemptAuthorization, WriteIntent, executor call, formal receipt, recovery window,
Provider call or one-shot campaign consumption is available in this path.

## Verification and review

`pre-start-review.json` records the independent implementation review. Runtime
qualification remains unproven until a separate result artifact is published.
Even a qualification PASS requires a separate explicit authorization for any
future formal campaign. The final review must state ALLOW or WITHHOLD; this PR
remains Draft / REVIEW_REQUIRED either way.

Read scope: repository and named historical checkpoint evidence. Write scope:
only this new results directory, `scripts/product/qualification_v040/`,
`tests/product_v040/qualification/`, and the new qualification verifier.
Frozen scope: every preexisting tracked file and all historical private evidence.
Final repository scope: complete tracked delta from the baseline above.

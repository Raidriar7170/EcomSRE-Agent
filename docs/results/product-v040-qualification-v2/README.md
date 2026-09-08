# Product v0.4 copy-up access policy v2 successor

Disposition: **REVIEW_REQUIRED**. No formal campaign is authorized by this increment.

This additive successor starts from PR #97 head
`e8a8afe935d972732bd37034b86af43d1c969ed8`. PR #95, #96 and #97 evidence,
including `BLOCKED_PRE_EXECUTION / COPYUP_IDENTITY_MISMATCH / WITHHOLD`,
remains unchanged. The exact-owner v1 gate remains valid under its original
contract; v2 is a separately reviewed contract, not a reinterpretation of that run.

The authoritative new policy is
[`policy.json`](../../../config/product-v040/copyup-access-v2/policy.json).
It binds direct SHA-256-verified OCI platform/config/layers and the three final
image paths:

| Path | UID:GID | Mode | Type |
| --- | --- | --- | --- |
| `/etc/kafka/secrets` | 1000:0 | 0775 | empty directory |
| `/mnt/shared/config` | 1000:1000 | 0755 | empty directory |
| `/var/lib/kafka/data` | 1000:0 | 0775 | empty directory |

These match the preserved PR #97 measurements. The source proves platform,
config and RootFS commitments directly. Original image-index bytes are absent
from Docker's local export; index-to-platform linkage is explicitly a daemon
RepoDigest binding, not a cryptographic claim about unseen index bytes.

V2 requires exact path-specific provenance independently of effective access.
EUID/EGID are 1000; supplementary GID 0 and all groups except 1000 are forbidden.
The image's group-writable directories are admitted only for exact image metadata
and owner-UID access with no group membership or unexpected attachment/writer.
All process capabilities must be zero and NoNewPrivs must be one. The trust
boundary is the explicitly owned local Docker environment and trusted daemon/host.

A complete process census is captured twice and must agree on PID, PPID,
start time, all UID/GID values, groups, capabilities, NoNewPrivs and full argv.
Only one PID-1 primary, the exact census process, and at most one of each
image/Compose-bound healthcheck helper are admitted. Any incomplete, raced,
unknown or duplicated role blocks subsequent operations. Every running writer
container required by the stage must be present.

Kafka JVM argv is frozen before observation from image startup scripts,
image environment and preserved Compose inputs. It includes exactly the image's
OpenTelemetry javaagent and 107 ordered library entries. This successor explicitly
sets `LC_ALL=C` before startup to make Bash glob order deterministic. The original
image locale remains recorded as source truth. Full pre-override Compose
environment must match the frozen policy; no observed JVM argv becomes authority.

The new namespace inherits PR #97 inventory/first-divergence/owned-cleanup gates.
The proposed stage plan includes probe start, complete census and probe stop.
Sentinel writes are limited to unique files in three freshly owned Kafka volumes,
using the exact approved identity; create/read/content/owner/delete/absence must
all pass. Sentinel activity is qualification evidence, never remediation.

Runtime can start only from clean committed source with exact policy and
implementation hash bindings in an independent `PASS / Must Fix 0 / Claim Accuracy
PASS / FRESH_NOFAULT_QUALIFICATION_ALLOW` review. A repository-common create-once
fuse limits this authorization to one no-fault attempt across worktrees. A failed
attempt cannot be repaired and repeated under this authorization.

Formal fault injection, formal manifest/candidate/approval/AttemptAuthorization,
WriteIntent, remediation, Provider calls and formal campaign execution are outside
this contract. Future-formal review never grants execution authority in this phase.

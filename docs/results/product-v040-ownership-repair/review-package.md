# Product v0.4 offline ownership repair — Draft / REVIEW_REQUIRED

This increment resumes only the **preserved safety checkpoint for offline
repair**. Its base is immutable PR #95 head
`0045e8288efd847b909c03443c6fd1fc15018a44`. PR #95, its results, the four failed
preparations and two diagnostics are history. This increment neither repairs
those results in place nor supplies a formal Payment result.

The user's current instruction authorizes offline code, deterministic tests,
non-mutating preflight, independent review, and a new Draft repair PR. It
expressly excludes startup, formal fault injection, remediation, Provider calls,
formal campaign execution and consumption of the one-shot allowance. The older
Goal's live authority is not inherited by this increment. Stop at the Draft PR;
do not merge or start the formal campaign.

## Evidence-backed finding

The original first anomaly remains distinct from the later bridge replacement:

| UTC, 2026-09-05 | Earliest observable stage or event | Strength |
|---|---|---|
| 21:31:08.612087 | Preparation 004, `ObserverLoop.__enter__ → witness()`, before executor enablement and network/NO_INCIDENT gates; generic non-owned drift | Direct preserved failure; exact differing resource and cause **UNKNOWN** |
| After failure | Original cleanup recorded CLEAN; immediate diagnostic recorded no difference | Point-in-time observations only |
| 21:37:56.624367 | Docker Desktop five-minute idle timer expires; automatic resource reduction | Direct Docker backend log |
| 21:37:57.499964 | Desktop stops its VM; dockerd gracefully shuts down | Direct backend and VM logs |
| 21:38:00.546827 | A Docker image-inspect GET triggers VM wake | Direct backend log; process/person **UNKNOWN** |
| 21:38:01.338618 | dockerd starts | Direct VM log |
| 21:38:01.630151875 | New builtin bridge `Created` timestamp | Direct preserved network inspect |
| 21:38:01.641649–21:38:01.869839 | Containers loaded; daemon initialization completes | Direct VM log |
| 21:44:12.500424 | Preserved checkpoint observes replacement bridge identity | Direct preserved checkpoint |

The earliest observable mechanism for the **later** replacement is Desktop idle
shutdown followed by image-query-triggered wake/restart. Recreation of bridge
during that initialization is a supported temporal inference, not an explicit
create-bridge event. It does not explain the earlier 21:31 anomaly. No evidence
identifies the initiating client process or person. The empty retained network
event query is not evidence that no lifecycle transition occurred.

The new Docker-specific log excerpts were copied into a separate private repair
directory. Source files and previous checkpoint files were not edited. Safe
source hashes and line references are in `forensic-findings.json`. Raw logs,
Docker identities, credentials and private paths are not published.

One read-only Kafka image inspect was issued during recovery of context. No
Docker create/start/build/pull/stop/remove/cleanup or settings command was run.
The log finding shows why even read-only daemon API calls may wake Desktop;
after establishing that mechanism, all further preflight used saved files.
This repair does not change Resource Saver settings or suppress network drift.

## Environment provenance and Kafka overlays

`config/product-v040/ownership-repair/environment-provenance.v1.json` binds
33 services, 48 mounts, six named volumes, image/platform identities, full
resolved service/configuration commitments, network definitions, ownership
labels, content commitments, cleanup rules and lifecycle expectations. The
historical Product build identity is labeled historical; it is not represented
as an image of this new repair source.

The separate Compose overlay is an **offline review artifact**. It covers:

| Kafka image path | Explicit volume source | Expected content/lifecycle |
|---|---|---|
| `/etc/kafka/secrets` | `kafka-secrets-ownership-v1` | Image-seeded named volume; no anonymous allocation |
| `/mnt/shared/config` | `kafka-config-ownership-v1` | Image-seeded named volume; no anonymous allocation |
| `/var/lib/kafka/data` | `kafka-data-ownership-v1` | Image-seeded mutable data; no anonymous allocation |

All three preserve Docker copy-up (`nocopy: false`) and bind the frozen ARM64
platform digest, complete saved RootFS diff-ID chain, image path, image
`Config.User=appuser`, exact project/sandbox/repair labels and named sources.
No arbitrary numeric UID/GID or empty-directory replacement is assumed.
The six owned volumes must be absent before startup, created once, never reused,
and removed only by exact fresh identity plus all labels after owned containers
stop. There is no `down -v`, prune or global cleanup implementation here.

**Content boundary:** image-path seed commitments are cryptographic source
commitments, not measured copy-up directory hashes. Numeric image ownership,
actual initial directory bytes and appuser writeability have not been measured.
File/directory bindings include names, modes, UID/GID and byte digests; mutable
private sources are explicitly labeled preserved post-failure observations,
not future initial seeds. Socket bindings commit the local daemon authority,
not fictitious socket-file content. Runtime readiness remains
`NOT_AUTHORIZED_NOT_MEASURED` until a separately authorized successor binds fresh
state and measures these requirements. Existing explicit mounts are bound;
fresh total image `Config.Volumes` coverage is still required before any startup.

## Stage inventory and fail-closed behavior

`v040_ownership.py` validates complete saved enumeration/inspect envelopes for
containers, networks, volumes and images. It retains identities, full-object
digests, labels, mounts, mount-content bindings, image digests and network
references. A matching Compose project label alone is insufficient. A missing
inspect result, raced before/after enumeration, daemon mismatch, unbound mount,
network reference or owned-resource label mismatch fails closed. Full-object
commitments intentionally keep volatile fields significant; this offline
increment makes no claim that they will remain constant during a real runtime.

The fixed 26-stage sequence covers preflight; before/after build and starts;
warmup, healthy control and Baseline; observer and enablement; network and
NO_INCIDENT gates; formal freeze, fault, approval, authorization and remediation
boundaries; and cleanup/readback. A complete **pre-specified** expected stage plan
is hash-bound before observation. Actual samples cannot refresh that plan or
adopt a foreign resource. Stage transitions cannot replace a non-owned network.

Each observation has UTC and monotonic time, ordinal, previous hash and full
snapshot. First divergence records expected and actual at the same observation,
exact difference paths and zero authority, using create-once private storage.
Malformed/partial collection also latches failure. Competing writers are
excluded; stage skips, journal corruption and restarts fail closed. A later
healthy snapshot cannot clear the latch.

This is **offline saved-observation replay**, not a new live capture or resumed
observer. The replay tool has no Docker client, HTTP transport, executor or
approval capability. Its protected-boundary method refuses authority even when
inventory passes. In this successor branch, the old runner's `main`, `prepare`
and `campaign` also refuse immediately, before constructing or reading runtime
state. No CLI flag or environment variable can enable them.

A separate read-only capture adapter requires a complete pre-bound stage plan
before any daemon query, collects only fixed list/inspect calls, verifies
before/after enumeration, and refuses unbound host bind sources. It is tested
with fake transports only. Additional runner checks precede formal freeze,
fault, approval, authorization and the attempt POST that could initiate
remediation. Missing stage history therefore also denies those operations.
The adapter is not invoked against Docker in this increment, and no live
preparation trace or complete future stage plan is minted. Real preparation
integration and measured inventories still require separately reviewed,
explicit authority; fixtures and adapter unit tests do not prove live readiness.

## Verification and review entry points

Tests exercise valid traces and deterministic bridge replacement, unknown
resource, mount/source drift, image drift, label drift, content drift and
malformed inventory at every one of the 26 stages. They assert durable first
divergence, absent protected action, zero counters, restart rejection, partial
enumeration rejection, unknown network references, source/digest binding,
stage order, journal corruption and fail-fast legacy entrypoints.

Offline preflight:

```sh
PYTHONPATH=src:. python -m scripts.ci.verify_product_v040_ownership_repair
PYTHONPATH=src:. python -m pytest tests/product_v040 tests/product/test_increment1_deployment_contract.py -q
```

Saved-observation replay (synthetic or separately acquired complete inputs):

```sh
PYTHONPATH=src:. python -m scripts.product.replay_v040_ownership \
  --expected expected.json --observations observations.json --output new-private-journal
```

The journal must be new or exactly resume its existing plan. Never point it at
historical campaign evidence. This command cannot execute a live stage.

Fresh results and independent review disposition are recorded in
`verification.json` and `independent-review.md`. The output claim is
`OFFLINE_REPAIR_REVIEW_REQUIRED`, not Goal completion, runtime readiness,
measured Payment recovery, or authorization to consume the campaign allowance.

## Declared review and integrity scope

- Read: repository code/configuration, frozen upstream, historical preparation
  004 and predecessor indexes, narrow Docker backend/VM logs.
- Write: new ownership repair modules, verifier, tests, configuration and this
  result directory; three fail-fast entrypoints and pre-action inventory checks
  in the old runner.
- Frozen: every pre-existing checkpoint/result/config artifact, all private
  historical roots, PR #95 head and main, upstream, historical sandbox/DTA code.
- Final repository scope: full tracked delta from `0045e82`; private logs and
  historical archive digests are separately declared evidence. No public raw
  runtime records or credentials.

Formal faults **0**; remediation writes **0**; Provider calls **0**; formal
campaign executions **0**. One-shot allowance **unconsumed**. These are the
increment's execution boundary and retained evidence counts, not a measured
successful campaign.

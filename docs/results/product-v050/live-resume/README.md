# v0.5 Live resume — retained prestart safety block

Terminal: `ECOMSRE_PRODUCT_V050_BLOCKED_SAFETY`.

The user activated the Live Resume Amendment and restored the original owned
local Docker/live scope. The working Provider model, Responses interface and
original Product ledger were retained. This run made **zero new Provider
requests**: cumulative 24, reported token-price upper USD0.136815, retained
unknown-usage reservations USD0.095449, commitment upper USD0.232264. Actual
invoice cost remains unknown. No repeat of minimal generation was attempted.

## Dependency diagnosis and repair

The prior schema-valid candidate was correctly rejected. Each member had an
initial 10-second resource snapshot, but neither had an accepted supplemental
read. The proposed dependency required a 30-second query window with its own
10-second/5-sample semantics; initial references were not those bound reads.
This was not absence of all resource data and not a reason to loosen validation.

New sessions retain the Runtime's finite read catalog. The learning view lists
`BOUND_OBSERVATION`, `COLLECTED_INCOMPLETE` and `LEGAL_NOT_COLLECTED` separately,
with target, field/unit, relative window, sample requirements and only actual
bound supporting references. Legacy sessions explicitly report their catalog
as not retained. Missing reads are not manufactured or silently prefetched.
Candidate admission and the expression evaluator are unchanged. Focused tests
cover initial-versus-supplemental data, wrong target/window, missing coverage,
and the existing full fixture path through development and normal zero-LLM
acquisition/matching. These are fixture checks, not new learned knowledge.

## Actual Docker outcome

A new nonce selected 22 cached, pinned ARM64 services from the existing checkout
and Kafka dependency closure. No image pull/build or frozen upstream change.
Collector configuration omitted absent receivers, host-root metrics and OpAMP.
The trusted collector alone had the existing Docker statistics socket mount;
a read-only mount is **not** a read-only Docker API security boundary. Product
LLM/API/Worker were not granted Docker tools, shell or recovery executors.

Docker created **22 containers, 1 network and 5 volumes**. Containers remained
`created`; none started. Before startup, the non-owned comparison detected the
default `bridge` network had a different ID and creation timestamp, although
daemon ID and the other recorded network configuration fields were unchanged:

| Observation | bridge creation time (UTC) |
|---|---|
| Before owned creation | 2026-09-18 02:40:45.546291875 |
| Prestart gate | 2026-09-18 02:47:02.554986041 |
| After owned cleanup | 2026-09-18 02:53:41.856689958 |

The cause is **unknown**. The bounded Docker network event query returned no
records; absence of events is not proof of an actor or cause. No network settings
were changed or default network recreated by a harness command. All own commands
were scoped to the new nonce and birth-bound IDs.

All **28 owned resources were removed** through exact birth checks. Remaining
owned containers/networks/volumes are **0/0/0**; the three original unlabelled
volumes are unchanged. Because default bridge identity drifted,
`non_owned_unchanged=false` and `clean=false`: **do not label this CLEAN**.
Raw inspections remain private; the public JSON contains only safe counts,
identity digests and the minimum network differences.

Four prestart validator adaptations and their failed checks remain recorded:
Compose null command inheritance, unassigned created-state network IDs,
unset optional environment values, and the exact Docker Desktop `/host_mnt`
representation of a repository bind. Regression tests preserve strict name,
context, path and running-network identity checks. None bypasses non-owned drift.

## Unattempted acceptance

Independent episode count **0**. No fault injection, live investigation, new
model candidate, development measurement, frozen independent validation,
promotion or new-event deterministic reuse occurred. Healthy recovery is not
applicable because no running fault episode began. Product recovery writes are
zero. `live_product.py` is a prepared but **unexecuted** harness, not a validated
live entrypoint. Existing Provider replay and historical results are unchanged.

The concrete unblock condition is to identify/confirm the Docker network
recreation and establish a stable current environment; any new campaign needs a
fresh explicitly accepted preflight baseline. Do not silently replace the failed
baseline, alter Docker settings, delete old attempts or ask for another API key.
The user clarification is pending. All non-dependent code, review and regression
work continues on the same Draft PR; no merge/release.

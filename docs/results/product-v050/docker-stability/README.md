# v0.5 Docker stability and real local investigation

**ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE**. The bounded campaign ended
with no accepted candidate; this is not full v0.5 acceptance or learned reuse.
Same Draft PR #104; no merge, release or Product recovery-authority expansion.

The user disabled Resource Saver. One 600.21-second observation, with 21 samples,
showed no drift in context, endpoint, daemon identity and selected container,
volume and network metadata. Volume contents were not checked. Historical idle
VM stop/start logs support the Resource Saver explanation without establishing
bridge causality or attributing a user restart. **Old live-01 BLOCKED_SAFETY /
clean=false remains unchanged.** One new live-02 baseline was admitted.

Three startup attempts share that admitted baseline, with owned cleanup between
attempts. The first failed without retained stderr (at least 17 starts observed;
exact count unknown). The diagnostic attempt captured astronomy-db exit 1.
The pinned PostgreSQL root entrypoint needs ownership changes incompatible with
cap_drop ALL. Using the image's existing postgres UID/GID 999:999, with no added
capabilities or image change, reached readiness for all 22 services.

Normal Product environment verification passed. Two healthy-baseline failures
remain retained: startup-window/Kafka log projection failure, then four truncated
log windows. Repairs use a narrow normal Kafka enum projection, correct 180-second
settlement and lower bounded traffic (30 requests, all successful). The source
cap remains 200 records; all 5/5 windows passed before fault injection.

| Independent episode | Role | Investigation | Calls | Reads |
| --- | --- | --- | ---: | ---: |
| e01 | Discovery | UNRESOLVED | 6 | 5 |
| e02 | Discovery | PROVISIONAL_SUPPORTED | 4 | 1 |
| e03 | Discovery | UNRESOLVED | 4 | 4 |
| e04 | Development | UNRESOLVED | 2 | 1 |
| e05 | Development | PROVIDER_FAILED / output truncated | 8 | 6 |

All five entered normal diagnosis as OPEN_WORLD and independently returned to
healthy state with queue lag 0. Models chose 17 reads; 13 complete read-result-
followup links are verified. e02's same-hypothesis numeric check supports a small
memory increase, **not a causal fault diagnosis**. Empty/truncated evidence and
invalid references remained inadmissible. No successful hypothesis revision or
validated knowledge is inferred from these counts.

The first learning handoff exposed a loader bug: an existing exact Product queue
read was parsed by frozen Core's narrower action type. The successor parser now
uses that Product type only for its exact ID and preserves all binding checks.
No old CAS observation was changed. Truncated rows are explicitly omitted only
from the proposer view; original counts/status/references and full private CAS
remain. Complete resource evidence is preserved.

Three real model proposals, including two bounded revisions, produced zero
accepted candidates: cross-target references; expression schema error; then
cross-target references again. The last proposal contains a Level B mean-CPU
expression and matching collected resource dependency, but was rejected as a
whole. Exact invalid operands from the schema-failed response are unknown: the
existing audit retained their hash and error location, not raw arguments.
No hand-corrected substitute was registered. Holdout, promotion and new-event
zero-LLM reuse were **not attempted because no candidate passed admission**.

Cumulative ledger: **51/200 requests**, USD **0.600071** reported-token cost upper
plus **0.095449** retained unknown-usage reservations = **0.695520/20** commitment.
Actual invoice unknown; 5/12 live episodes consumed. Same configured model
`gpt-5.4-mini-2026-03-17`; no Provider reprobe. New Product recovery writes: **0**.
Experiment fault activation and baseline restoration each occurred five times;
these are not Agent recovery.

Each startup attempt removed 22 containers, 1 network and 5 volumes. Final owned
remaining **0/0/0**, admitted non-owned projection unchanged, **new campaign
clean=true**. Raw inspect, settings, credentials, control truth and telemetry logs
are not published. [Result](result.json), [call ledger](calls.json),
[projected traces](live-traces.json) retain successful and failed outcomes.

Read-only verification (does not start Docker or call a model):

```sh
PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.ci.verify_product_v050_docker_stability
```

Focused regressions and independent reviews covered cleanup drift, startup
failure retention, observer projection, action round-trip, proposer projection
and claim tampering. Independent final review closed a hypothesis-text binding gap with an additional
negative regression (8 evidence checks pass). Final source-bound checks/CI are
recorded separately; green
offline checks do not change the learning terminal.

# Live Telemetry Instrumentation v2 Protocol

## Development probes

The CLI command is:

```bash
uv run python -m scripts.live_sandbox.instrumentation_v2 probe \
  --private-root "$ECOMSRE_PRIVATE_ROOT"
```

`--private-root` is optional. Without it, the runtime uses
`ECOMSRE_PRIVATE_ROOT`, then the private default for this version. A private
root inside the repository is rejected. All private directories are `0700` and
all private files are `0600`.

The runtime automatically reserves the next create-once development-probe
number and never overwrites a prior probe. No more than four full sandbox
startups are admitted. Each probe:

1. verifies the local Unix Docker/arm64 boundary and pinned submodule;
2. resolves the exact Compose contract and cached image identities;
3. starts only the exact dual-labelled owned sandbox;
4. waits for 25 healthy services and reads the exact baseline configuration;
5. captures one fixed 30-second no-fault window;
6. runs independent bounded Metrics, Logs, and Traces readiness;
7. seals and independently resolves Evidence refs; and
8. performs exact owned cleanup in `finally`.

A probe may guide only source field, envelope, query, time encoding, and
readiness repairs. Its private terminal is retained even when a source fails.
The development success marker is `DEVELOPMENT_PROBE_AVAILABLE`; four startups
without 3/3 availability terminate as `BLOCKED_SOURCE_CONTRACT_UNRESOLVED`.

## Implementation freeze and admission

Canonical admission requires all of the following:

- the latest development terminal is 3/3 `AVAILABLE`;
- independent Evidence ref validation passed;
- the last owned cleanup is `CLEAN`;
- the implementation worktree is clean;
- the branch is `feature/live-telemetry-instrumentation-v2`;
- local and remote implementation heads are identical; and
- exact implementation-head offline CI has separately passed.

The implementation is committed, pushed, and opened as a Draft PR before the
canonical command. PR #29 remains the unchanged v1 negative result and is
closed without merge only after the successor remote head exists.

## Canonical preflight

The single create-once command is:

```bash
uv run python -m scripts.live_sandbox.instrumentation_v2 canonical-preflight \
  --private-root "$ECOMSRE_PRIVATE_ROOT" \
  --implementation-ci-pass
```

It binds an immutable private admission record, repeats the exact safety and
image gates, waits at least 90 seconds after all 25 services are healthy,
verifies the frozen baseline, captures one 30-second window, applies the fixed
ingestion grace/readiness limits, produces three typed source results, reopens
the resolver, and cleans all owned resources.

The canonical directory and terminal are create-once. A canonical failure is
retained and cannot be rewritten or rerun in this version. No failed gate may
change the target, window, source requirement, upstream version, or safety
boundary.

## Success and publication

Only a canonical terminal with three available nonempty target sources, zero
invalid refs, independent resolver success, zero fault/Provider/model/approval/
plan/mutation counters, restored baseline, owned resources `0/0/0`, and no
non-owned resource change generates the three safe public result files.

Offline CI never runs the local Docker canonical preflight. After publication,
one full local validation, public leakage scan, private permission check,
public verifier, final tracked-diff integrity closure, exact result-head CI,
and independent read-only review are required before the successor PR can be
marked Ready for review. This protocol never authorizes merge, release, tag,
branch deletion, Fault-to-A0 E2E, or remediation.

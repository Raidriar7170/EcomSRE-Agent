# Live Telemetry Instrumentation v3 Protocol

## Fixed identity

- predecessor head: `58a3d797a68ea90e56fefd5a3c31a14c82862981`
- branch: `feature/live-telemetry-instrumentation-v3`
- config: `config/live-telemetry-instrumentation-v3`
- default private root: `~/.ecomsre/private/live-telemetry-instrumentation-v3`
- success: `LIVE_TELEMETRY_INSTRUMENTATION_V3_READY_FOR_E2E`
- development startups: at most 4
- canonical preflights: exactly 1 maximum

The v2 private and public evidence is read-only predecessor evidence. Do not
delete, overwrite, chmod, or reuse its create-once roots.

## Offline gate

Before any Docker startup, require fresh focused tests for frozen instant query
time and exact descendant permissions, the complete v2/v3 instrumentation test
set, Ruff, mypy, `git diff --check`, and an independent read-only review with no
Must Fix finding.

## Development probe

```bash
uv run python -m scripts.live_sandbox.instrumentation_v3 probe \
  --private-root "$ECOMSRE_PRIVATE_ROOT"
```

Only local Unix Docker and owned project resources are permitted. The probe is
no-fault and read-only with respect to telemetry backends. It must restore the
baseline and clean all owned containers, networks, and volumes. Continue only
when Metrics, Logs, and Traces are all `AVAILABLE`, target counts are positive,
all refs resolve, every v3 private directory is `0700`, every private file is
`0600`, and cleanup is `CLEAN`.

## Implementation freeze and canonical admission

Commit and push the implementation head, create or update a Draft PR, and wait
for exact-head offline CI. Canonical admission additionally verifies the exact
branch and remote head, latest development truth, clean worktree, and an unused
create-once admission path.

```bash
uv run python -m scripts.live_sandbox.instrumentation_v3 canonical-preflight \
  --private-root "$ECOMSRE_PRIVATE_ROOT" \
  --implementation-ci-pass
```

Canonical failure is terminal for v3: do not rerun, lower gates, rewrite the
terminal, or generate a success projection.

## Public outputs and claim boundary

Only a successful canonical creates:

- `docs/results/live-telemetry-instrumentation-v3.json`
- `docs/results/live-telemetry-instrumentation-v3.md`
- `docs/results/live-telemetry-instrumentation-v3-human-brief.md`

The public verifier must recompute the v3 schema/version/verdict relationship,
three source gates, positive target counts, zero invalid refs, zero prohibited
actions, and clean owned cleanup. Public files never contain raw telemetry,
runtime identities, local endpoints, ports, private paths, or credentials.

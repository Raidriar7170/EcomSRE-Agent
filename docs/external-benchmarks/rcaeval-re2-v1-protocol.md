# RCAEval RE2 external benchmark protocol v1

This protocol evaluates the existing EcomSRE-Agent diagnosis architectures on
RCAEval RE2 without treating development data as external evidence.

## Source and split

- Upstream publication branch: `www25`
- Locked commit: `9d14687ce0644188f1f1a576fd3f57cd903af446`
- Dataset record: `10.5281/zenodo.14590730`
- Development-visible systems: RE2-OB and RE2-SS
- One-time opaque holdout: RE2-TT

RE2-TT must not be downloaded, inspected, searched, or executed before a human
reviews Work Package A and separately authorizes Work Package B. Raw holdout
folder names and Ground Truth are evaluator-only. Agent runtime receives only
`tt-case-NNNN`, T0, modality availability, and allowlisted telemetry copied into
the sanitized root.

## Frozen comparison

Every case runs Single, Fixed, and Dynamic with the same model snapshot, prompt
family, telemetry preprocessing, evidence contract, budgets, and one semantic
attempt. Each arm receives a fresh context, evidence counter, provider object,
and terminal record. A completed terminal record is never retried.

The external adapter preserves the existing architecture boundaries without
modifying the frozen Phase 5A/5B runtime:

- Single queries Metrics, Logs, and Traces, then makes one final Judge call.
- Fixed makes three source-isolated Specialist calls, then one final Judge call.
- Dynamic makes a Metrics Specialist call, a Commander call that selects Logs,
  Traces, or both, one call per selected Specialist, then one final Judge call.

The adapter directly reuses the existing OpenAI-compatible transport/config,
`EvidenceStore`, `RunBudget`/`BudgetLimits`, frozen model snapshot, architecture
identities, and no-retry terminal discipline. It does not call
`run_diagnosis_v2`: that entry point is a frozen scripted replay over four tools
(including Changes) and the closed internal `UnifiedMechanismV2` ontology, while
this benchmark requires exactly three tools and RCAEval's cpu/mem/disk/delay/
loss/socket labels. Direct reuse would either enable a forbidden tool or
misrepresent the benchmark labels. The existing runtime remains byte-for-byte
unchanged; the isolated adapter uses strict `SpecialistAssessment`,
`CommanderDecision`, and `Diagnosis` contracts for this external benchmark.

`model_calls` counts actual Provider attempts: Single uses 1, Fixed uses 4, and
Dynamic uses 4 or 5. All operations share the eight-model-call,
eight-tool-call, 32,000-token run budget. Provider usage is accumulated after
each call. Dynamic has no targeted-refinement pass in v1 and receives no fixed
two-tool shortcut; the Commander selects one or both follow-up sources from the
Metrics-only assessment.

The telemetry window is `[T0 - 600 seconds, T0 + 600 seconds]`, clamped to
available rows. Metrics are forward-filled then zero-filled following the
locked upstream evaluator preprocessing. Tool output is bounded and cites
run-local `metric:NNNN`, `log:NNNN`, or `trace:NNNN` evidence IDs. Missing RE2-SS
traces return `SOURCE_UNAVAILABLE`; they are not interpreted as evidence that no
anomaly exists.

## Output and scoring

The only scored diagnosis is one canonical service and one canonical indicator:
`cpu`, `mem`, `diskio`, `latency`, or `socket`. The indicator mapping is
`disk -> diskio`, `delay/loss -> latency`, with the remaining labels unchanged.
Provider failures, timeouts, schema failures, workflow failures, empty outputs,
and unresolved aliases remain terminal records and count as incorrect in the
full denominator.

The primary endpoint is RE2-TT Root Service AC@1. The primary comparison is
Dynamic minus Single. Its 95% interval uses 10,000 deterministic hierarchical
paired-bootstrap replicates: resample 30 service-fault strata, resample three
instances within each selected stratum, and preserve all architecture pairs.
Superiority requires the lower bound to be greater than zero.

The preregistered cost-quality comparison is Dynamic versus Single, matching the
existing internal comparison and the primary architecture pair. It requires
all of: accuracy-difference CI lower bound at least -0.05, tool-call reduction
point estimate at least 20%, and tool-call-reduction CI lower bound greater than
zero. Secondary metrics and descriptive fault subgroups cannot replace the
primary endpoint.

BARO is excluded from the `rcaeval-re2-v1` main experiment and is not eligible
for primary inference. If added later, BARO must use a separately frozen
protocol and be reported only as an independent secondary analysis; it cannot
change, pool with, or replace the preregistered Single/Fixed/Dynamic results.

## Holdout lifecycle

The evaluator-only append-only state chain is:

`DEV_ONLY -> PROTOCOL_FROZEN -> HOLDOUT_SEALED -> HOLDOUT_PREFLIGHT_PASSED -> HOLDOUT_EXECUTED -> TERMINAL_RECORDS_LOCKED -> UNBLINDED -> FINAL_REPORT_FROZEN`

The repository provides separate commands for audit, development pilot,
protocol freeze, seal, preflight, execution, unblinding, and report
verification. Only the first three belong to Work Package A. The presence of
later commands does not authorize their execution against real holdout data.
Work Package B1 may repeat the OB/SS development pilot and protocol freeze only
after the implementation commit exists; it still validates later commands only
with synthetic fixtures and stops at
`HOLDOUT_EXECUTION_AUTHORIZATION_REQUIRED`.

Because this repository is configured as a non-package `uv` project, every
RCAEval CLI invocation uses the authoritative prefix
`PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.rcaeval.<command>`.
Omitting `PYTHONPATH=src:.` is not a supported execution mode. The protocol
freeze command additionally requires a control root disjoint from the Git
repository and a completely clean worktree; it fails closed on staged,
unstaged, or untracked files.

The protocol-freeze record is created outside the Git implementation snapshot
after the implementation commit exists. It binds that exact commit, every
scoped committed blob by full SHA-256, the full tracked-diff SHA-256, a canonical
scoped-closure SHA-256, every config file, the external adapter source tree, the
control-plane source tree, the schedule, and development evidence. Keeping the
record outside the bound commit avoids an unverifiable self-reference.
Each development execution first creates a run lock binding its schedule,
repository base, config hashes, and adapter/control-plane source-tree hashes;
the freeze gate rejects reports that do not bind the exact runtime that produced
their terminal journals.
Every later command revalidates that binding against the exact 54-path
allowlist, a clean worktree, and byte-identical committed blobs. Seal, preflight, execution,
terminal-lock, unblinding, and final-report artifacts are create-once and linked
to the append-only state journal. A semantic-attempt marker is fsynced before an
arm begins; recovery terminalizes an orphaned attempt and never reissues it.

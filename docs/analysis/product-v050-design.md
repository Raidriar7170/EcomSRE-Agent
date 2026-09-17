# Product v0.5 implementation boundary

Active authority is the [v0.5 Goal](../goals/EcomSRE_v0.5_Codex_Goal.md),
accepted as DEC-064. Historical evidence, DTA protocols, locks and upstream are
unchanged. The scope reconciliation is in [progress](product-v050-progress.md).

## Runtime and model responsibilities

The existing API enqueues investigation and knowledge-proposal jobs in the same
SQLite Worker. Defaults remain off. Diagnosis stays immutable and provider-free.
An investigation stores a separate session and CAS event journal. The model may
choose a finite read, propose/update hypotheses, conclude provisionally or abstain.
Runtime owns targets, exact windows, evidence references, coverage, hypothesis IDs,
leases, request reservations and stop conditions. Known events with independent
strong residual references may receive a supplementary investigation.

Reads reuse existing connector templates, with two bounded historical windows;
there is no free-form query, URL, shell or file tool. Initial typed telemetry and
legal reads enter the prompt. Free-form log messages and controller metadata do
not. This reduces injection exposure but also removes useful textual detail.
Model explanations remain inferences. Optional numeric predictions are evaluated
by Runtime and recorded as TRUE/FALSE/UNKNOWN; absent tests are NOT_CHECKED.

The Provider reuses the redirect-rejecting HTTPS transport with no implicit retry.
Every dispatch reserves a durable cost bound first. Interrupted dispatches cannot
be repeated under the same key; unknown usage retains its reservation. Unknown
model identities invalidate the campaign cost bound. Response storage contains
only an audit projection, response digest and schema-validated proposal, never
extra gateway hidden-reasoning fields. Alias snapshots must be explicitly covered
by the dated operator price schedule. A default alias is not a pinned snapshot.

## Candidate and evaluation boundary

LLM and existing miner candidates use the same Product candidate representation
and normal deterministic matcher. LLM origin requires a completed real transport
request whose submitted proposal and discovery input digest match. Mock transport
remains FIXTURE_ONLY. Miner inputs are restricted before evidence acquisition.
Candidate admission retains DEC-062's unique-root requirement and supports only
PATTERN_ONLY; this version cannot establish independent causal support.

Level A uses existing predicates. Level B permits CPU-percent/memory-byte gauge
aggregates (time-weighted mean, max, delta, rate), same-unit ratios and comparisons.
There is one numeric condition joined to bounded existing predicates; no arbitrary
code or general query language. Exact target, sample coverage, units and window
are required; missing/ambiguous data and zero denominators remain UNKNOWN.

Threshold provenance binds the discovery snapshot. Development matching is a
separate consumed pass, then independent cases are frozen. Discovery and development
cases cannot become that candidate's holdout. Frozen cases cannot reenter discovery
or development. Each holdout is consumed before evaluation. The existing Shadow
safety gate is reused unchanged; missing necessary controls produce a retained
incomplete-validation rejection. Initial candidates are one-shot; there is no
automatic candidate revision campaign or threshold adjustment after holdout.

Only the independent harness can enroll a fresh unused test registry and promote
a validated candidate. Promotion conflicts with any existing ACTIVE registration.
Registration, version and promotion/revocation records commit atomically. No model
tool or new API can promote. The authenticated revocation endpoint is available;
old registry revoke requests for the new schema receive a controlled 409.

## Recovery and claims

The planner returns a field-level preview relative to a versioned baseline. It
checks target, current state, field allowlist, evidence and bounds, while preserving
unrelated configuration. PREVIEW_ACCEPTABLE still has authority NONE and zero
writes; the module does not connect to WriteIntent, Authorization or Executor.

The current evidence is offline fixture evidence. Real Provider credentials and
dated prices have not been supplied; no new live campaign or learning acceptance
has run. Fixture normal-entry matching and revocation are engineering checks, not
proof of a model-learned rule. Review and full regression remain in progress.

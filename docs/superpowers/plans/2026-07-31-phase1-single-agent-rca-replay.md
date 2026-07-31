# Phase 1 Single-Agent RCA Replay Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development to implement this plan task-by-task.
> Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and verify a replay-first Single-Agent RCA pipeline with four
read-only typed tools, fail-closed evidence validation, seven frozen cases, an
offline scripted-model verdict, and a separately gated real-provider verdict.

**Architecture:** A single bounded Agent asks a `ModelGateway` for exactly one
typed tool action or final RCA per turn. A capability-bounded replay backend
returns immutable observations, tools persist them in a run-scoped Evidence
Store, and a fail-closed validator checks the final result and report. The
default suite uses one generic deterministic adapter; an explicit
OpenAI-compatible gateway is exercised only by the provider-smoke command.

**Tech Stack:** Python 3.11, Pydantic v2, standard-library JSON/urllib, pytest,
GNU Make.

---

## Authorization and execution rules

- Work only in `/Users/raidriar/Documents/New project` on
  `phase1/single-agent-rca-replay`.
- Base commit is `9dc840e820009794f344e0a3fa5320d739cb6291`.
- Do not modify `src/ecomsre/environment/`, `src/ecomsre/phase0/`,
  `config/phase0/`, image locks, Phase 0 historical evidence/conclusions,
  Docker lifecycle, Compose contracts, or the sibling worktree.
- Do not run Docker, Compose, a Phase 0 smoke, model training, or live
  observability.
- Do not implement Multi-Agent, Commander, Judge, Remediation, or any write
  tool.
- Do not commit, push, create/update a PR, merge, or archive. Those protected
  actions have not been authorized.
- For every production behavior: add the focused test, run it and observe the
  expected failure, implement the smallest behavior, then rerun to green.
- Use `apply_patch` for repository file edits.

## Locked file map

Create these package files:

- `src/ecomsre/phase1/__init__.py`: Phase 1 public package marker.
- `src/ecomsre/phase1/contracts.py`: incident, evidence, RCA, actions, usage,
  records, and report models.
- `src/ecomsre/phase1/budgets.py`: immutable limits and mutable run-local
  counters.
- `src/ecomsre/phase1/evidence.py`: opaque evidence-ref allocation and lookup.
- `src/ecomsre/phase1/validator.py`: RCA/report fail-closed checks.
- `src/ecomsre/phase1/agent.py`: one model/tool loop and run-report assembly.
- `src/ecomsre/phase1/cli.py`: `replay-smoke`, `eval`, and `provider-smoke`.
- `src/ecomsre/tools/__init__.py`, `base.py`, `metrics.py`, `logs.py`,
  `traces.py`, `changes.py`: four typed read-only tools.
- `src/ecomsre/backends/__init__.py`, `replay.py`, `live_protocol.py`: replay
  implementation and a Protocol-only future live boundary.
- `src/ecomsre/model/__init__.py`, `gateway.py`, `scripted.py`: gateway
  protocol, real OpenAI-compatible adapter, and deterministic adapter.
- `eval/__init__.py`, `eval/phase1/__init__.py`, `eval/phase1/run.py`:
  evaluator-only ground-truth loading and metrics.

Create configuration and evidence:

- `config/phase1/agent.json`.
- Seven directories below
  `config/phase1/replay-cases/agent-visible/`, each with exactly
  `manifest.json`, `incident.json`, `metrics.json`, `logs.json`,
  `traces.json`, and `changes.json`.
- Seven corresponding `eval/phase1/ground-truth/<case-id>.json` files.
- `.env.example`.

Create focused tests below `tests/phase1/` and append only a Phase 1 block to
`Makefile`. Create the derived closeout brief only after fresh verification at
`docs/human-briefs/2026-07-31-phase1-single-agent-rca-replay.html`.

### Task 1: Freeze strict core contracts

**Files:**

- Create: `tests/phase1/test_contracts.py`
- Create: `src/ecomsre/phase1/__init__.py`
- Create: `src/ecomsre/phase1/contracts.py`
- Create: `config/phase1/agent.json`

- [ ] **Step 1: Write contract tests first**

Cover these exact behaviors:

```python
START = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
END = datetime(2026, 7, 31, 8, 5, tzinfo=UTC)


def make_abstain_result(**overrides: object) -> RCAResult:
    values = {
        "schema_version": "phase1.rca-result.v1",
        "decision": RCADecision.ABSTAIN,
        "root_service": None,
        "fault_mechanism": None,
        "causal_chain": (),
        "affected_sli": "request_success_rate",
        "supporting_evidence": (),
        "contradicting_evidence": (),
        "missing_evidence": (),
        "confidence": 0.2,
        "decision_rationale": "The replay observations do not establish an incident.",
        "recommended_next_action": "Retain the read-only observations.",
    }
    values.update(overrides)
    return RCAResult(**values)


def test_incident_hint_is_optional_non_authoritative_context() -> None:
    incident = Incident(
        schema_version="phase1.incident.v1",
        incident_id="incident-001",
        alert_source_service=None,
        summary="Checkout error-rate alert",
        started_at=datetime(2026, 7, 31, 8, 0, tzinfo=UTC),
        ended_at=datetime(2026, 7, 31, 8, 5, tzinfo=UTC),
        affected_sli="request_success_rate",
        severity="SEV2",
    )
    assert incident.alert_source_service is None


def test_confirmed_rca_requires_two_independent_evidence_sources() -> None:
    with pytest.raises(ValidationError, match="independent evidence sources"):
        RCAResult(
            schema_version="phase1.rca-result.v1",
            decision=RCADecision.RCA_CONFIRMED,
            root_service="ad",
            fault_mechanism="request_processing_failure",
            causal_chain=("ad requests fail",),
            affected_sli="request_success_rate",
            supporting_evidence=(
                "evidence://" + "a" * 32 + "/metrics/0001",
                "evidence://" + "a" * 32 + "/metrics/0002",
            ),
            contradicting_evidence=(),
            missing_evidence=(),
            confidence=0.9,
            decision_rationale="Metrics and traces establish the same failure.",
            recommended_next_action="Review the bounded replay evidence.",
        )


@pytest.mark.parametrize(
    "rationale",
    (
        "",
        "evidence://" + "a" * 32 + "/metrics/0001",
        "query_metrics for more data",
        "rm -rf workspace",
    ),
)
def test_decision_rationale_rejects_empty_refs_tools_and_shell(
    rationale: str,
) -> None:
    with pytest.raises(ValidationError):
        make_abstain_result(decision_rationale=rationale)
```

Also test UTC windows, `extra="forbid"`, finite confidence, decision-specific
root/missing-field semantics, rationale maximum 1,000 characters, non-mutating
recommended action text, and the four discriminated action types.

- [ ] **Step 2: Run RED**

Run:

```bash
env PYTHONDONTWRITEBYTECODE=1 uv run --frozen --no-sync \
  pytest tests/phase1/test_contracts.py -q
```

Expected: collection fails because `ecomsre.phase1.contracts` does not exist.

- [ ] **Step 3: Implement the minimal models**

Use one frozen base model:

```python
class Phase1Model(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class Incident(Phase1Model):
    schema_version: Literal["phase1.incident.v1"]
    incident_id: str = Field(min_length=1, max_length=128)
    alert_source_service: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    summary: str = Field(min_length=1, max_length=1_000)
    started_at: datetime
    ended_at: datetime
    affected_sli: str = Field(min_length=1, max_length=128)
    severity: Literal["SEV1", "SEV2", "SEV3"]
```

Define `EvidenceSource`, `RCADecision`, `StableErrorCode`, `Incident`,
`InvestigationRequest`, `Evidence`, `Hypothesis`, `RCAResult`, four typed query
actions, `FinalAction`, `ModelUsage`, `ModelRequest`, `ModelResponse`,
`ToolCallRecord`, `ModelCallRecord`, `BudgetSnapshot`, and `AgentRunReport`.
Use the ref pattern
`^evidence://[0-9a-f]{32}/(metrics|logs|traces|changes)/[0-9]{4}$`.

In `RCAResult` enforce:

```python
if self.decision is RCADecision.RCA_CONFIRMED:
    if not self.root_service or not self.fault_mechanism:
        raise ValueError("confirmed RCA requires root service and mechanism")
    sources = {parse_evidence_source(ref) for ref in self.supporting_evidence}
    if len(sources) < 2:
        raise ValueError("confirmed RCA requires two independent evidence sources")
elif self.decision is RCADecision.NEED_MORE_EVIDENCE:
    if not self.missing_evidence:
        raise ValueError("need-more-evidence requires missing evidence")
elif self.root_service is not None or self.fault_mechanism is not None:
    raise ValueError("abstain cannot confirm a root service or mechanism")
```

Reject `evidence://`, `query_metrics`, `search_logs`, `search_traces`,
`list_changes`, shell metacharacters, and common shell command prefixes in
`decision_rationale` and `recommended_next_action`.

Write `config/phase1/agent.json` with:

```json
{
  "schema_version": "phase1.agent-config.v1",
  "temperature": 0,
  "max_model_calls": 8,
  "max_tool_calls": 8,
  "max_total_tokens": 12000,
  "model_timeout_seconds": 30.0,
  "tool_timeout_seconds": 5.0
}
```

- [ ] **Step 4: Run GREEN and review scope**

Run the focused test and `git diff --check`. Confirm no protected path appears
in `git diff --name-only`.

### Task 2: Add run-scoped evidence, budgets, and fail-closed validation

**Files:**

- Create: `tests/phase1/test_evidence_validator.py`
- Create: `tests/phase1/test_budgets.py`
- Create: `src/ecomsre/phase1/evidence.py`
- Create: `src/ecomsre/phase1/budgets.py`
- Create: `src/ecomsre/phase1/validator.py`

- [ ] **Step 1: Write evidence and validator tests**

Exercise opaque allocation, monotonically increasing per-source sequences,
replacement rejection, malformed raw refs, cross-run refs, unresolved refs,
support/contradiction overlap, hint non-conversion, source diversity, budget
overflow, and report-record agreement:

```python
START = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
END = datetime(2026, 7, 31, 8, 5, tzinfo=UTC)


def confirmed_result(
    *,
    supporting_evidence: tuple[str, ...],
) -> RCAResult:
    return RCAResult(
        schema_version="phase1.rca-result.v1",
        decision=RCADecision.RCA_CONFIRMED,
        root_service="ad",
        fault_mechanism="request_processing_failure",
        causal_chain=("ad requests fail",),
        affected_sli="request_success_rate",
        supporting_evidence=supporting_evidence,
        contradicting_evidence=(),
        missing_evidence=(),
        confidence=0.9,
        decision_rationale="Independent observations establish the failure.",
        recommended_next_action="Review the bounded replay evidence.",
    )


store = EvidenceStore(run_id="a" * 32)
metric_ref = store.add(
    source=EvidenceSource.METRICS,
    service="ad",
    started_at=START,
    ended_at=END,
    observation_type="request_error_rate",
    attributes={"state": "anomalous", "value": 0.72},
    raw_artifact_ref="metrics.json#0",
    raw_artifact_sha256="b" * 64,
    limitations=(),
)
assert metric_ref == "evidence://" + "a" * 32 + "/metrics/0001"
with pytest.raises(EvidenceValidationError, match="UNRESOLVED_EVIDENCE_REF"):
    validate_rca_result(
        result=confirmed_result(
            supporting_evidence=(metric_ref, foreign_trace_ref)
        ),
        store=store,
        incident=incident,
    )
```

- [ ] **Step 2: Run RED**

Run both new test files. Expected: missing evidence/budget/validator modules.

- [ ] **Step 3: Implement minimal stores and guards**

`EvidenceStore.add` must be the only ref allocator and return immutable
`Evidence`; `resolve` must compare the ref run ID with the store run ID before
lookup. `RunBudget` must consume before dispatch and reject any count or token
increase beyond its immutable `BudgetLimits`.

`validate_rca_result` must return a validated result only after checking all
refs, overlap, two-source diversity, SLI compatibility, and forbidden text.
`validate_agent_report` must additionally compare tool-returned refs with the
evidence index and enforce call/token budgets.

- [ ] **Step 4: Run GREEN**

Run both focused files and `tests/phase1/test_contracts.py`.

### Task 3: Implement capability-bounded replay and four typed tools

**Files:**

- Create: `tests/phase1/test_replay_backend.py`
- Create: `tests/phase1/test_tools.py`
- Create: `src/ecomsre/tools/__init__.py`
- Create: `src/ecomsre/tools/base.py`
- Create: `src/ecomsre/tools/metrics.py`
- Create: `src/ecomsre/tools/logs.py`
- Create: `src/ecomsre/tools/traces.py`
- Create: `src/ecomsre/tools/changes.py`
- Create: `src/ecomsre/backends/__init__.py`
- Create: `src/ecomsre/backends/replay.py`
- Create: `src/ecomsre/backends/live_protocol.py`

- [ ] **Step 1: Write replay-loader and tool tests**

Build temporary fixtures containing exactly six files. Test successful hash
validation plus rejection of a symlink, traversal capability, unexpected file,
missing file, malformed JSON, hash mismatch, unavailable source, timeout status,
and query interval outside the Incident.

For each typed tool, assert it:

```python
RUN_ID = "a" * 32
START = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
END = datetime(2026, 7, 31, 8, 5, tzinfo=UTC)

result = query_metrics(context, MetricsQuery(started_at=START, ended_at=END))
assert result.status is ToolStatus.OK
assert result.evidence_refs == (
    "evidence://" + RUN_ID + "/metrics/0001",
)
assert store.resolve(result.evidence_refs[0]).source is EvidenceSource.METRICS
```

Assert none of the four query schemas accepts a path, URL, shell command,
backend name, Docker identifier, or write payload.

- [ ] **Step 2: Run RED**

Run the two focused files. Expected: tool/backend imports fail.

- [ ] **Step 3: Implement replay and tools**

The replay file contract is:

```json
{
  "schema_version": "phase1.replay-observations.v1",
  "status": "AVAILABLE",
  "observations": [
    {
      "service": "ad",
      "started_at": "2026-07-31T08:01:00Z",
      "ended_at": "2026-07-31T08:02:00Z",
      "observation_type": "request_error_rate",
      "attributes": {
        "state": "anomalous",
        "fault_mechanism": "request_processing_failure"
      },
      "limitations": []
    }
  ]
}
```

Only `AVAILABLE`, `UNAVAILABLE`, and `TIMEOUT` are valid. The loader receives an
allowed replay root and one case directory, resolves both once, uses `lstat`,
rejects symlinks/non-regular files, requires the exact six filenames, verifies
the five manifest hashes, parses strict JSON, and keeps all rows in memory.

Each tool validates the closed interval, consumes budget before dispatch, maps
backend status to the stable result envelope, persists returned observations
with its fixed source, and returns only store-created refs.

`live_protocol.py` contains only a structural `ObservabilityBackend` Protocol;
it must not import urllib, requests, sockets, or Phase 0 telemetry.

- [ ] **Step 4: Run GREEN and static boundary search**

Run both focused tests. Search the new backend/tool files for Docker, Compose,
HTTP client imports, evaluator paths, and write verbs; review every match.

### Task 4: Implement both model adapters and the bounded Single Agent

**Files:**

- Create: `tests/phase1/test_scripted_agent.py`
- Create: `tests/phase1/test_gateway.py`
- Create: `src/ecomsre/model/__init__.py`
- Create: `src/ecomsre/model/gateway.py`
- Create: `src/ecomsre/model/scripted.py`
- Create: `src/ecomsre/phase1/agent.py`

- [ ] **Step 1: Write scripted-agent and gateway tests**

Use in-memory backends to prove evidence-dependent tool sequences differ,
incorrect hints are ignored, runtime configuration failure cannot be confirmed
without Changes, request-processing failure can be confirmed by Metrics plus
Traces, decoys are excluded from support, and budget termination is typed.

Use a complete injected OpenAI response envelope:

```python
START = datetime(2026, 7, 31, 8, 0, tzinfo=UTC)
END = datetime(2026, 7, 31, 8, 5, tzinfo=UTC)

response = {
    "id": "chatcmpl-fixture",
    "model": "fixture-model",
    "choices": [{
        "index": 0,
        "finish_reason": "tool_calls",
        "message": {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call-fixture",
                "type": "function",
                "function": {
                    "name": "query_metrics",
                    "arguments": json.dumps({
                        "started_at": START.isoformat(),
                        "ended_at": END.isoformat()
                    })
                }
            }]
        }
    }],
    "usage": {
        "prompt_tokens": 20,
        "completion_tokens": 8,
        "total_tokens": 28
    }
}
```

Assert one choice/one tool call, strict arguments, usage consistency,
temperature `0`, `parallel_tool_calls=False`, timeout propagation, HTTPS,
no retry, and no API key in `repr`, errors, records, or serialized payload.

- [ ] **Step 2: Run RED**

Run the two focused files. Expected: model and Agent modules are absent.

- [ ] **Step 3: Implement the gateway protocol and real transport**

Define:

```python
class ModelGateway(Protocol):
    def complete(self, request: ModelRequest) -> ModelResponse: ...


class OpenAICompatibleTransport(Protocol):
    def post_json(
        self,
        *,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object],
        timeout_seconds: float,
    ) -> Mapping[str, object]: ...
```

The concrete standard-library transport uses `urllib.request.urlopen` only
inside the explicit gateway call. Join an HTTPS base URL to
`chat/completions`, send five function definitions (`query_metrics`,
`search_logs`, `search_traces`, `list_changes`, `submit_rca`), require exactly
one returned function call, and parse it into the internal action union.

`OpenAICompatibleConfig.from_environment` returns `None` only when all three
`ECOMSRE_LLM_BASE_URL`, `ECOMSRE_LLM_API_KEY`, and `ECOMSRE_LLM_MODEL` are
absent; partial configuration fails closed.

- [ ] **Step 4: Implement one generic scripted policy and Agent loop**

The scripted adapter must inspect only Incident, transcript, Evidence, and
remaining budgets. It must never accept a case ID or evaluator path. Its
decision order is Metrics → evidence-dependent Traces/Logs/Changes → final
result. Correlate `service` and visible `fault_mechanism` across sources;
require Changes for `runtime_configuration_failure`; never use
`alert_source_service`; never include unmatched change evidence in support.

The Agent validates each model action, dispatches one of exactly four tools,
records IDs/tokens/latencies, validates the final RCA, and emits one
`AgentRunReport`.

- [ ] **Step 5: Run GREEN**

Run both focused files plus all earlier Phase 1 tests.

### Task 5: Freeze seven replay cases and evaluator-only ground truth

**Files:**

- Create: `tests/phase1/test_frozen_cases.py`
- Create: seven six-file case directories below
  `config/phase1/replay-cases/agent-visible/`
- Create: seven files below `eval/phase1/ground-truth/`

- [ ] **Step 1: Write frozen-case contract tests**

Require exactly these IDs and decisions:

```python
EXPECTED = {
    "ad-partial-failure-complete": "RCA_CONFIRMED",
    "ad-partial-failure-without-logs": "RCA_CONFIRMED",
    "ad-partial-failure-frontend-decoy": "RCA_CONFIRMED",
    "ad-change-with-normal-sli": "ABSTAIN",
    "telemetry-insufficient": "NEED_MORE_EVIDENCE",
    "no-real-incident": "ABSTAIN",
    "recommendation-cache-failure": "RCA_CONFIRMED",
}
```

Assert seven manifest/ground-truth pairs, exact manifest hashes, at least five
null `alert_source_service` values, the decoy case's incorrect `frontend` hint,
no evaluator key/path in agent-visible JSON, at least one confirmed root
`recommendation`, and no runtime-configuration ground truth without an
independent matching Changes observation.

- [ ] **Step 2: Run RED**

Run the frozen-case test. Expected: the seven case directories are missing.

- [ ] **Step 3: Add exact fixtures and ground truth**

Use the UTC incident window `2026-07-31T08:00:00Z` through
`2026-07-31T08:05:00Z`. Keep all observations inside it.

Use these visible/expected mechanisms:

- complete ad: `runtime_configuration_failure`, supported by anomalous Metrics,
  failing Traces, Logs, and a matching ad Changes observation;
- missing logs ad: `request_processing_failure`, supported by Metrics and
  Traces; Logs status `UNAVAILABLE`;
- frontend decoy: `request_processing_failure`, supported by ad Metrics and
  Traces; unrelated frontend deployment is stored only as a decoy;
- normal-SLI ad change: normal Metrics plus an ad change, expected `ABSTAIN`;
- insufficient: anomalous checkout Metrics without a correlating second source,
  expected `NEED_MORE_EVIDENCE`;
- no incident: normal frontend Metrics and no causal observations, expected
  `ABSTAIN`;
- recommendation: `cache_backend_timeout`, supported by recommendation
  Metrics, Traces, and Logs, expected `RCA_CONFIRMED`.

Ground truth uses:

```json
{
  "schema_version": "phase1.ground-truth.v1",
  "case_id": "ad-partial-failure-frontend-decoy",
  "expected_decision": "RCA_CONFIRMED",
  "expected_root_service": "ad",
  "expected_fault_mechanism": "request_processing_failure",
  "decoys": [{
    "source": "CHANGES",
    "service": "frontend",
    "observation_type": "deployment"
  }]
}
```

Use `null` expected root/mechanism for non-confirming cases and an empty decoy
array elsewhere. Compute every manifest SHA-256 from the final bytes and update
the manifest values.

- [ ] **Step 4: Run GREEN**

Run the frozen-case test and the replay-loader tests.

### Task 6: Add E2E evaluation and dual-verdict commands

**Files:**

- Create: `tests/phase1/test_replay_e2e.py`
- Create: `tests/phase1/test_evaluation.py`
- Create: `tests/phase1/test_provider_smoke.py`
- Create: `tests/phase1/test_cli_make.py`
- Create: `eval/__init__.py`
- Create: `eval/phase1/__init__.py`
- Create: `eval/phase1/run.py`
- Create: `src/ecomsre/phase1/cli.py`
- Modify: `Makefile`
- Create: `.env.example`

- [ ] **Step 1: Write seven E2E and evaluation tests**

Run every case through the actual Replay backend, typed tools, Evidence Store,
scripted adapter, Agent, and validator. Assert exact decision/root/mechanism,
all refs resolve, decoy evidence is stored but not supporting, at least one case
omits an unnecessary tool, and per-case semantic signatures are stable.

Assert the report contains these exact metric keys:

```python
{
    "Decision Accuracy",
    "Schema Valid Rate",
    "Root Service Accuracy",
    "Fault Mechanism Accuracy",
    "Evidence Reference Validity",
    "Abstention Accuracy",
    "Decoy Resistance",
    "Average Tool Calls",
    "Token Usage",
    "Wall-clock Latency",
}
```

Every rate metric has integer `numerator` and `denominator`. Tool calls, tokens,
and latency have per-case values plus total/average values.

- [ ] **Step 2: Write provider/CLI/Make tests**

With all provider variables absent, call provider smoke and assert exit zero,
no transport call, JSON status `SKIPPED_NOT_CONFIGURED`. With a complete
injected transport sequence, assert the provider orchestrator validates one
confirmed and one non-confirmed case before status `PASSED`. Pytest must never
instantiate the real urllib transport.

Assert the Makefile exposes exactly the four Phase 1 targets and default targets
contain no provider, Docker, Compose, Phase 0 smoke, or interactive command.

- [ ] **Step 3: Run RED**

Run the four new test files. Expected: evaluator, CLI, targets, and environment
example are absent.

- [ ] **Step 4: Implement evaluation**

Load ground truth only after each `AgentRunReport` returns. Write
`artifacts/phase1/evaluation/evaluation-report.json` with strict JSON and
case-level results. Compute Decision Accuracy over 7, root/mechanism accuracy
over confirmed ground truth, abstention accuracy over non-confirming ground
truth, and Decoy Resistance over declared-decoy cases.

- [ ] **Step 5: Implement CLI and Make targets**

Append:

```make
PHASE1_CLI := env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync python -m ecomsre.phase1.cli

.PHONY: phase1-prerequisites phase1-replay-smoke phase1-eval phase1-test phase1-provider-smoke

phase1-prerequisites:
	@for path in "$(UV_CACHE_ROOT)" "$(UV_CACHE_DIR)" "$(TMPDIR)"; do \
		test ! -e "$$path" || { test -d "$$path" && test ! -L "$$path" && test -O "$$path"; } || exit 40; \
	done
	@mkdir -p "$(UV_CACHE_ROOT)" "$(UV_CACHE_DIR)" "$(TMPDIR)"
	@chmod 700 "$(UV_CACHE_ROOT)" "$(UV_CACHE_DIR)" "$(TMPDIR)"

phase1-replay-smoke: phase1-prerequisites
	$(PHASE1_CLI) replay-smoke

phase1-eval: phase1-prerequisites
	$(PHASE1_CLI) eval

phase1-test: phase1-prerequisites
	env PYTHONPATH="$(PYTHONPATH)" uv run --frozen --no-sync pytest tests/phase1 -q

phase1-provider-smoke: phase1-prerequisites
	$(PHASE1_CLI) provider-smoke
```

`.env.example` contains only:

```text
ECOMSRE_LLM_BASE_URL=https://provider.example/v1
ECOMSRE_LLM_API_KEY=replace-with-local-secret
ECOMSRE_LLM_MODEL=replace-with-model-id
```

The CLI writes run reports below `artifacts/phase1/reports/`, the evaluation
report below `artifacts/phase1/evaluation/`, and provider results below
`artifacts/phase1/provider-smoke/`.

- [ ] **Step 6: Run GREEN**

Run all `tests/phase1/`, then:

```bash
env -u ECOMSRE_LLM_BASE_URL -u ECOMSRE_LLM_API_KEY -u ECOMSRE_LLM_MODEL \
  make phase1-replay-smoke
env -u ECOMSRE_LLM_BASE_URL -u ECOMSRE_LLM_API_KEY -u ECOMSRE_LLM_MODEL \
  make phase1-eval
env -u ECOMSRE_LLM_BASE_URL -u ECOMSRE_LLM_API_KEY -u ECOMSRE_LLM_MODEL \
  make phase1-provider-smoke
```

Expected provider terminal status: `SKIPPED_NOT_CONFIGURED`.

### Task 7: Produce the closeout brief and verify every gate

**Files:**

- Create:
  `docs/human-briefs/2026-07-31-phase1-single-agent-rca-replay.html`
- Modify only if a verified mismatch exists:
  `docs/superpowers/specs/2026-07-31-phase1-single-agent-rca-replay-design.md`

- [ ] **Step 1: Run a requirement-by-requirement audit**

Map each row of the design's requirement-to-evidence matrix to a test, runtime
artifact, command output, or Git hash. Do not count intent or a broad green
suite as proof for a narrower requirement.

- [ ] **Step 2: Write the derived Chinese Human Brief**

Include conclusion, dual-verdict meaning, implemented components, seven cases,
ten metrics, key files, exact verification commands/results, evidence links,
Phase 0 protected status, provider-smoke status, risks, claims not to overstate,
and the Phase 2 next step. State that the brief is derived and cannot upgrade
the machine-readable verdict.

- [ ] **Step 3: Run fresh focused and broad verification**

Run:

```bash
make phase1-test
make phase1-replay-smoke
make phase1-eval
env -u ECOMSRE_LLM_BASE_URL -u ECOMSRE_LLM_API_KEY -u ECOMSRE_LLM_MODEL \
  make phase1-provider-smoke
env PYTHONDONTWRITEBYTECODE=1 uv run --frozen --no-sync pytest -q
env PYTHONDONTWRITEBYTECODE=1 uv run --frozen --no-sync \
  python -m compileall -q src/ecomsre eval/phase1
git diff --check
```

Require an empty diff for the protected paths and compare these base values:

```text
src/ecomsre/environment tree a77c4600728c8a4415683c5b303d6fa15e3bfc39
src/ecomsre/phase0 tree       5e9504aa593445791986abca47e903433e88fdea
config/phase0 tree            e3e1ece95156af5aec459e1ae7cfd1e1718200bd
image-lock blob               772ce86a1bd67e0f1af2395f4ca54e713e6b09cb
```

Search the Phase 1 diff for evaluator leakage, forbidden write tools, model
training, Multi-Agent/Commander/Judge/Remediation implementation, Docker,
Compose, Phase 0 imports, and live observability clients. Review rather than
blindly accepting any documentation/test-only match.

- [ ] **Step 4: Determine the truthful verdict**

- `PHASE1_REPLAY_PIPELINE_READY`: all offline gates pass and the unconfigured
  provider target truthfully returns `SKIPPED_NOT_CONFIGURED`.
- `PHASE1_SINGLE_AGENT_REPLAY_MVP_READY`: every pipeline gate plus a real,
  configured OpenAI-compatible provider smoke validates at least one confirmed
  and one non-confirmed case.
- `BLOCKED`: any required pipeline gate fails.

Do not use an injected transport, pytest double, or missing-credential skip to
claim the stronger MVP verdict.

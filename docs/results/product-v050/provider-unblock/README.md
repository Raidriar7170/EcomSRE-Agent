# Product v0.5 Provider unblock — bounded continuation of PR #104

The configured `gpt-5.4-mini-2026-03-17` now generates successfully through
`POST https://api.openai.com/v1/responses`, using the same project dotenv source
and original ledger. No model/account/key-permission/network change was made.

| Milestone | Actual result |
| --- | --- |
| Minimal text | PASS; complete response with visible `OK` |
| Minimal function | PASS; `ack` with exactly boolean `ok=true` |
| Product API/Worker tool roundtrip | PASS on replay attempt 6; six accepted reads, four read-to-followup model transitions |
| Knowledge | Three requests: two schema failures; one structured model proposal rejected by deterministic governance |
| Independent live / holdout / promotion / learned reuse | NOT_ATTEMPTED; zero independent live episodes |

This is `ECOMSRE_PRODUCT_V050_ENGINEERING_COMPLETE_WITH_LIMITATIONS`, not acceptance
PASS. The final replay sessions ended `BUDGET_EXHAUSTED` at their three-call smoke
caps, not supported diagnoses. Their last reads have no following model response
and are excluded from the four complete roundtrips.

## What the evidence establishes

- Old HTTP404 responses had no retained Content-Type, request ID or raw body;
  those fields are **unrecoverable**, not inferred from new requests. The first
  minimal text probe also predates successful-header capture; its missing headers
  are explicit. It was not repeated merely to fill them.
- P1 succeeded before any task-prompt change. Consequently no schema or prompt
  change is claimed to explain the historical 404. Its root cause remains
  undetermined; metadata availability alone was never generation proof.
- P2 and subsequent Product calls retained `application/json`, allowlisted
  `x-request-id`, exact method/path, timestamps, monotonic durations and usage.
  Error projection reads once with a size bound; JSON error messages are scrubbed,
  arbitrary HTML/plain bodies are withheld, and redaction failure preserves status.
- Attempt 4 exposed prose in evidence-reference fields. Attempt 5 exposed mixed
  windows in READ-attached hypotheses. Provider instructions now explain the
  existing field contracts and allow reads without premature hypotheses. R1–R4
  and knowledge evaluators were not weakened or changed.
- Attempt 6 selected LOGS and METRICS queries. After returned observations, the
  model selected different windows/sources. These are protocol-replay observations,
  not proof that its causal interpretation improved or that an actual fault existed.
- The one schema-valid model proposal used a CPU mean expression but declared an
  unverified supplemental dependency. Governance rejected it with
  `candidate has no verified deployable observation dependency`. No rule was
  accepted, independently evaluated, registered or promoted. The final proposer
  attempt failed expression validation; it is not silently discarded.

## Accounting and preservation

[calls.json](calls.json) contains all **24 cumulative requests**: the preserved
four prior requests plus 20 this turn. The P1/P2 diagnostic sub-budget used **2/6
requests**, gross reservations **USD0.011244/1**, settled token-price upper estimate
**USD0.000205**. The 18 Product smoke requests use the original total budget.

Cumulative committed upper amount is **USD0.232264/20**: reported-token upper
estimates USD0.136815 plus retained unknown reservations USD0.095449. Actual invoice
cost is **unknown**. Remaining: **176 requests / USD19.767736 / 12 live episodes**.
No ledger reset, deleted marker, overwritten session, Docker start or new Product
external write occurred. No runtime cleanup was needed.

The old zero-call package and `continuation-01` remain byte-identical. The historical
continuation verifier is anchored to `fe57dee`; the new verifier checks current
sources and this appended evidence. [smoke-traces.json](smoke-traces.json) links
accepted decisions to request proposal digests, supplemental query identities and
subsequent observed-evidence projections. It does not publish private prompts,
credentials, hidden reasoning or evaluator truth.

## Remaining execution boundary

The unblock contract section 2 explicitly says not to start Docker. Read-only
inspection found no running containers. Independent live episodes therefore need
clarification of whether that prohibition ends after entry smoke and the original
Goal's bounded local-live authorization resumes. This precise question is pending;
no new key, account permission change or network change is requested.

The original Goal was resumed through actual investigation reads and model knowledge
proposals. Independent validation and new-event learned-rule reuse are still absent.

## Verification

```bash
PYTHONPATH=src:. .venv/bin/python -m scripts.ci.verify_product_v050_provider_unblock
```

Current offline tests are source-bound in [offline-checks.json](offline-checks.json).
Final full regression and CI are run only after fixing the working tree and HEAD;
exact-head CI links are recorded on Draft PR #104. Previous failures remain evidence.

Official contracts checked: [model](https://developers.openai.com/api/docs/models/gpt-5.4-mini),
[function calling](https://developers.openai.com/api/docs/guides/function-calling),
[request diagnostics](https://developers.openai.com/api/reference/overview).

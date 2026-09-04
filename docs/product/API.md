# Product API · current v0.3 surface

Public presentation v0.3 does not rename the stable `/v1` routes or schemas.

The FastAPI application publishes OpenAPI at `/openapi.json`. Reads are public
on the configured listener. Mutations require `Authorization: Bearer <token>`
when `ECOMSRE_ADMIN_TOKEN` is set; non-loopback binding fails closed without a
token.

## Operations

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/healthz` | Process liveness |
| GET | `/readyz` | SQLite readiness |
| GET | `/metrics` | Prometheus-format Product counters |

## Environments and baselines

| Method | Path |
| --- | --- |
| POST / GET | `/v1/environments` |
| GET | `/v1/environments/{environment_id}` |
| POST | `/v1/environments/{environment_id}/verify-jobs` |
| GET | `/v1/environments/{environment_id}/capabilities` |
| POST | `/v1/environments/{environment_id}/baseline-jobs` |
| GET | `/v1/environments/{environment_id}/baselines` |
| GET | `/v1/environments/{environment_id}/baseline-readiness` |
| GET | `/v1/environments/{environment_id}/baseline-readiness-v023` |
| GET | `/v1/baselines/{baseline_id}/window-audit` |
| GET | `/v1/baselines/{baseline_id}/window-audit-v023` |
| POST | `/v1/environments/{environment_id}/changes` |

Verify and baseline requests may carry `Idempotency-Key`. The key is bound to
the environment, job type, and payload; a conflicting reuse returns a stable
error rather than silently changing work.

## Incidents and evidence

| Method | Path |
| --- | --- |
| POST | `/v1/incidents` |
| GET | `/v1/incidents/{incident_id}` |
| POST | `/v1/incidents/{incident_id}/diagnosis-jobs` |
| GET | `/v1/incidents/{incident_id}/diagnosis` |
| GET | `/v1/incidents/{incident_id}/evidence` |
| GET | `/v1/incidents/{incident_id}/evidence-index` |

`external_incident_key` is idempotent inside one environment. Reusing it with a
different payload is a conflict. Every result declares a terminal, lane,
capability limitations, evidence references, and the fixed counters
`action_authority = NONE`, `agent_writes = 0`, and `runbook_executions = 0`.

## Knowledge evolution

| Method | Path |
| --- | --- |
| GET | `/v1/environments/{environment_id}/fault-families` |
| GET | `/v1/fault-families/{family_id}` |
| POST | `/v1/fault-families/{family_id}/reviews` |
| POST | `/v1/fault-families/{family_id}/merge` |
| POST | `/v1/fault-families/{family_id}/registration-drafts` |
| GET | `/v1/registrations/{registration_id}` |
| POST | `/v1/registrations/{registration_id}/shadow-evaluation-jobs` |
| POST | `/v1/registrations/{registration_id}/promotions` |
| POST | `/v1/registrations/{registration_id}/revocations` |

Human records are explicit API objects. TEST_REVIEWER examples are simulated
and must carry the wording `SIMULATED HUMAN REVIEW`.

## Jobs and errors

`GET /v1/jobs/{job_id}` returns `PENDING`, `RUNNING`, `SUCCEEDED`, `FAILED`, or
`CANCELLED`, with attempt and lease metadata. A failure exposes a bounded
`safe_error_code`, not connector credentials or raw exception text.

All API errors use:

```json
{"error":{"code":"STABLE_CODE","message":"Safe explanation.","details":{}}}
```

Request validation is `INVALID_REQUEST`; missing resources use typed not-found
codes; unexpected failures are `INTERNAL_CONTRACT_FAILURE`.

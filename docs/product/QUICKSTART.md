# Product MVP v0.1 Quickstart

## 1. Start the Product

Requirements: Docker Desktop with Compose v2, `curl`, and `jq`. Choose a local
token; do not commit it.

```bash
cp examples/product/.env.example .env.product
export ECOMSRE_ADMIN_TOKEN='replace-with-a-long-local-token'
export ECOMSRE_PRODUCT_API_PORT=8080
docker compose --env-file .env.product -f docker-compose.product.yml build --pull=false
docker compose --env-file .env.product -f docker-compose.product.yml up -d --no-build --wait
curl --fail http://127.0.0.1:8080/readyz
```

The Compose project is `ecomsre-product-mvp-v01`. It publishes only
`127.0.0.1:8080` by default. The worker has no API token and neither service has
the Docker socket.

For a deterministic, Docker-free full Product flow instead run:

```bash
PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.product.run_product_mvp_demo
```

## 2. Create and verify an environment

The OTel example expects the repository-owned local sandbox endpoints on ports
`19090`, `19200`, `11686`, and `18080`. Start that sandbox only through its
authorized lifecycle; do not point this profile at production.

```bash
export ECOMSRE_API=http://127.0.0.1:8080
export ECOMSRE_AUTH="Authorization: Bearer ${ECOMSRE_ADMIN_TOKEN}"

curl --fail --silent --show-error \
  -H "$ECOMSRE_AUTH" -H 'Content-Type: application/json' \
  --data @examples/product/environment.otel-demo.json \
  "$ECOMSRE_API/v1/environments" > /tmp/ecomsre-environment.json
export ENVIRONMENT_ID="$(jq -r .environment_id /tmp/ecomsre-environment.json)"

curl --fail --silent --show-error -X POST \
  -H "$ECOMSRE_AUTH" -H 'Idempotency-Key: quickstart-verify-v1' \
  "$ECOMSRE_API/v1/environments/$ENVIRONMENT_ID/verify-jobs" \
  > /tmp/ecomsre-verify-job.json
export VERIFY_JOB_ID="$(jq -r .job_id /tmp/ecomsre-verify-job.json)"
curl --fail --silent --show-error \
  "$ECOMSRE_API/v1/jobs/$VERIFY_JOB_ID" | jq
curl --fail --silent --show-error \
  "$ECOMSRE_API/v1/environments/$ENVIRONMENT_ID/capabilities" | jq
```

Poll the job until `status` is `SUCCEEDED`. A `FAILED` job retains only a stable
safe error code.

## 3. Build and activate a baseline

This short baseline is explicitly `DEMO_ONLY`. The local Demo must have run for
at least six minutes so the 180-second lookback preceding the 180-second warmup
contains five complete windows.

```bash
curl --fail --silent --show-error -X POST \
  -H "$ECOMSRE_AUTH" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: quickstart-baseline-v1' \
  --data '{"build_policy":{"mode":"DEMO_ONLY","lookback_seconds":180,"window_count":5,"minimum_successful_windows":1,"warmup_seconds":180},"activate":true}' \
  "$ECOMSRE_API/v1/environments/$ENVIRONMENT_ID/baseline-jobs" \
  > /tmp/ecomsre-baseline-job.json
export BASELINE_JOB_ID="$(jq -r .job_id /tmp/ecomsre-baseline-job.json)"
curl --fail --silent --show-error \
  "$ECOMSRE_API/v1/jobs/$BASELINE_JOB_ID" | jq
curl --fail --silent --show-error \
  "$ECOMSRE_API/v1/environments/$ENVIRONMENT_ID/baselines" | jq
```

## 4. Submit and poll a read-only diagnosis

Resolve a verified service ID, then submit one bounded observation. The live
profile may truthfully return `INSUFFICIENT_EVIDENCE`; it must not claim
No-Incident when required coverage is unavailable.

```bash
export SERVICE_ID='replace-with-a-verified-svc-id'
export NOW_UTC="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
curl --fail --silent --show-error \
  -H "$ECOMSRE_AUTH" -H 'Content-Type: application/json' \
  --data "{\"environment_id\":\"$ENVIRONMENT_ID\",\"external_incident_key\":\"quickstart-no-fault-v1\",\"alert_name\":\"manual-observation\",\"summary\":\"Bounded read-only observation\",\"started_at\":\"$NOW_UTC\",\"ended_at\":\"$NOW_UTC\",\"candidate_service_ids\":[\"$SERVICE_ID\"],\"labels\":{\"mode\":\"quickstart\"}}" \
  "$ECOMSRE_API/v1/incidents" > /tmp/ecomsre-incident.json
export INCIDENT_ID="$(jq -r .incident_id /tmp/ecomsre-incident.json)"

curl --fail --silent --show-error -X POST -H "$ECOMSRE_AUTH" \
  "$ECOMSRE_API/v1/incidents/$INCIDENT_ID/diagnosis-jobs" \
  > /tmp/ecomsre-diagnosis-job.json
export DIAGNOSIS_JOB_ID="$(jq -r .job_id /tmp/ecomsre-diagnosis-job.json)"
curl --fail --silent --show-error \
  "$ECOMSRE_API/v1/jobs/$DIAGNOSIS_JOB_ID" | jq
curl --fail --silent --show-error \
  "$ECOMSRE_API/v1/incidents/$INCIDENT_ID/diagnosis" | jq
curl --fail --silent --show-error \
  "$ECOMSRE_API/v1/incidents/$INCIDENT_ID/evidence" | jq
```

## 5. Review a family and promote a registration

Only Open-World incidents enter clustering. Promotion requires multiple
positive incidents, confusable negatives, an accepted family, deterministic
rule mining, a passing shadow evaluation, and explicit human promotion.
All local example decisions must be labelled `SIMULATED HUMAN REVIEW`.

```bash
export FAMILY_ID='replace-with-a-review-ready-family-id'
export REVIEWED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
curl --fail --silent --show-error \
  -H "$ECOMSRE_AUTH" -H 'Content-Type: application/json' \
  --data "{\"decision\":\"ACCEPT_AS_NEW\",\"reviewer\":\"TEST_REVIEWER\",\"note\":\"SIMULATED HUMAN REVIEW\",\"reviewed_at\":\"$REVIEWED_AT\"}" \
  "$ECOMSRE_API/v1/fault-families/$FAMILY_ID/reviews" \
  > /tmp/ecomsre-family-review.json
export REVIEW_ID="$(jq -r .review_id /tmp/ecomsre-family-review.json)"

curl --fail --silent --show-error \
  -H "$ECOMSRE_AUTH" -H 'Content-Type: application/json' \
  --data "{\"human_review_id\":\"$REVIEW_ID\",\"human_canonical_label\":\"Observed Family\",\"llm_explanation\":\"SIMULATED LLM ADVISORY: naming only\",\"unresolved_gaps\":[]}" \
  "$ECOMSRE_API/v1/fault-families/$FAMILY_ID/registration-drafts" \
  > /tmp/ecomsre-registration.json
export REGISTRATION_ID="$(jq -r .registration_id /tmp/ecomsre-registration.json)"

curl --fail --silent --show-error \
  -H "$ECOMSRE_AUTH" -H 'Content-Type: application/json' --data '{}' \
  "$ECOMSRE_API/v1/registrations/$REGISTRATION_ID/shadow-evaluation-jobs" \
  > /tmp/ecomsre-shadow.json
export SHADOW_EVALUATION_ID="$(jq -r .evaluation_id /tmp/ecomsre-shadow.json)"
export PROMOTED_AT="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
curl --fail --silent --show-error \
  -H "$ECOMSRE_AUTH" -H 'Content-Type: application/json' \
  --data "{\"shadow_evaluation_id\":\"$SHADOW_EVALUATION_ID\",\"reviewer\":\"TEST_REVIEWER\",\"note\":\"SIMULATED HUMAN REVIEW\",\"promoted_at\":\"$PROMOTED_AT\"}" \
  "$ECOMSRE_API/v1/registrations/$REGISTRATION_ID/promotions" | jq
```

Use the deterministic demo for a fully populated promotion example; empty or
insufficient negative pools fail closed.

## 6. Observe and stop

```bash
curl --fail "$ECOMSRE_API/healthz"
curl --fail "$ECOMSRE_API/readyz"
curl --fail "$ECOMSRE_API/metrics"
docker compose --env-file .env.product -f docker-compose.product.yml down
```

Add `--volumes` only when you explicitly intend to delete this Product's local
MVP state. OTel Demo cleanup is a separate owned lifecycle operation.

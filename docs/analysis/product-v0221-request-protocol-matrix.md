# Product v0.2.2.1 OpenSearch Request Protocol Matrix

Terminal: `ECOMSRE_PRODUCT_V0221_REQUEST_PROTOCOL_PASS`

This is an offline, sanitized protocol matrix. Live schema sessions and
live OpenSearch requests remain zero at this checkpoint.

## Plans

- `plan-a-field-caps-get-query`: semantic `767c9422e6260938d6e4b79019102c3cdba8b394dd4b629547702b69997c436b`
- `plan-b-field-caps-post-query`: semantic `097099c042c7499d8fddb5947b6b456184837d6f6101bd10d86dbd0b9a18edac`
- `plan-c-mapping-sample-empirical`: semantic `5e3db0b890bfb40bb147f2eb3327cf0bc37a3c07ff1e67dd180254b490f0356b`

## Cases

- `OFFICIAL_GET_FIELD_CAPS_QUERY`: `PASS`
- `METHOD_405_TO_POST_QUERY`: `PASS`
- `PERMISSION_403_TO_MAPPING_EMPIRICAL`: `PASS`
- `POST_400_TO_MAPPING_EMPIRICAL`: `PASS`
- `FIELD_CAPS_AVAILABLE_PROFILE`: `PASS`
- `FIELD_CAPS_OPTIONAL_FALLBACK`: `PASS`
- `REQUIRED_TIE_FAILS_CLOSED`: `PASS`
- `SEMANTIC_PLAN_REPEAT_REJECTED`: `PASS`
- `HTTP_400_NOT_RETRIED_UNCHANGED`: `PASS`

## Boundary

Field Caps is preferred. It is optional only after Mapping, bounded
samples, service aggregation, timestamp range, and final profile
verification establish one unique profile. No default field guessing
is permitted.

Matrix SHA-256: `a95ecab7b0d4f40f04340ff062b109eaa5fc35ef5ed5b88c863a30e23190d3ed`

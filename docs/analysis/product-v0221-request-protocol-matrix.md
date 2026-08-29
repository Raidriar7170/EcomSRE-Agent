# Product v0.2.2.1 OpenSearch Request Protocol Matrix

Terminal: `ECOMSRE_PRODUCT_V0221_REQUEST_PROTOCOL_PASS`

This is an offline, sanitized protocol matrix. Live schema sessions and
live OpenSearch requests remain zero at this checkpoint.

## Plans

- `plan-a-field-caps-get-query`: semantic `5fee96d93b3b0b0bab289d5c4210e020245eca4165e398e3c237d6149c001806`
- `plan-b-field-caps-post-query`: semantic `ce4207de3828c11baa1453b255575ea9f8560acea7fa55bb50b9fe7dbff639c1`
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

Matrix SHA-256: `c49b2d5bfcac3976fe0d8e7d1f7aee8cc6be74d0ebe50bcd2fe148266fd1fe58`

# Product v0.2.2.2 OpenSearch Profile Selection

- Capture session: `product-v0222-capture-1`
- Capture bundle SHA: `4084941d8368c4f74ec2db95ac2215f36c9531367f9904b9b90cd653bceeea94`
- Candidate Set SHA: `f3aeaf272ab199c1284238c9e7785ec89f46b1cb54ad1608188a052c27f9d4de`
- Machine status: `OPERATOR_SELECTION_REQUIRED`
- Machine recommendation: `NONE`

| Candidate | Timestamp | Service source | Service query | Message | Severity | Trace ID | Sample parse | Checkout query | Support | Contradiction | Net | Warnings |
|---|---|---|---|---|---|---|---:|---|---:|---:|---:|---|
| P00 | @timestamp | resource.service.name | resource.service.name.keyword | body | severity.number | traceId | 5/5 | PASS | 22 | 0 | 22 | none |
| P01 | @timestamp | resource.service.name | resource.service.name.keyword | body | severity.text | traceId | 5/5 | PASS | 22 | 0 | 22 | none |

Select exactly one frozen candidate alias. Do not enter or alter field paths.
Activation still requires offline acceptance and fresh holdout verification.

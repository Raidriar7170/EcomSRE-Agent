# Product v0.4 bounded remediation development

Current stage: PR-D. Terminal: `ECOMSRE_PRODUCT_V040_D_EXECUTOR_AND_VERIFIER_PASS`.
Status: `OFFLINE_PASS_INTEGRATION_PENDING`.

PR-A #91, PR-B #92 and PR-C #93 are merged after independent review and exact-head
CI. PR-D implements isolated, default-disabled execution, a fixed authenticated
Payment gateway, create-once dispatch/receipts and policy-bound two-window
verification. The standalone deterministic demo uses synthetic persisted Product
inputs, one fake mutation and two fake windows. A temporary Unix-socket test uses
a fake upstream and closes its server. Neither is measured Payment evidence.

Local Product validation: 268 passed / 15 warnings, Ruff PASS, mypy 239 source
files PASS. Independent Reviewer: PASS / Must Fix 0 / Claim Accuracy PASS;
35 independent executor/gateway regression tests passed. History binding retains
404 original frozen artifacts and the exact activated Goal SHA.

Live v0.4 campaigns, live forward mutations and Provider calls remain 0. Actual
Docker startup, network-denial checks and the single live campaign are PR-E gates.
PR-D committed content verification, exact-head CI and squash merge are pending.
See the [structured result](product-v040-bounded-remediation-development.json),
[PR-D audit](../analysis/product-v040-executor-verifier-audit.md), and
[independent review](../external-reviews/product-v040-pr-d-review.md).

CI run `33981962864` retained a subprocess import-path test failure (6492 passed,
1 failed, 21 skipped). The test-only correction passes all 268 Product tests with
outer PYTHONPATH unset and has independent review PASS. New exact-head CI remains
required; the failed run is not represented as a pass.

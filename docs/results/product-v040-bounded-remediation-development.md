# Product v0.4 bounded remediation development

PR-A is merged as PR #91 at `a823456185aa88809a73eb193b63aefcc3afa745`. Its exact-head GitHub CI passed with 6,388 tests passed and 21 skipped, Ruff PASS, and mypy PASS for 675 files. The merged tree equals the reviewed and tested tree.

PR-B implements persisted candidates, explicit token-authenticated approvals, bounded expiry, append-only revocation and semantic idempotency. It reached `ECOMSRE_PRODUCT_V040_B_APPROVAL_WORKFLOW_PASS`: 191 focused tests passed, Ruff and scoped mypy passed, and independent review returned PASS / Must Fix 0 / Claim Accuracy PASS. Exact-head GitHub CI and merge remain outstanding. The [approval workflow audit](../analysis/product-v040-approval-workflow-audit.md) records compatibility decisions and rejection semantics.

Diagnosis remains unchanged and read-only. Candidate construction and approval grant no execution authority. Attempt authorization, executor, recovery verification and live acceptance remain later Goal stages. No Product v0.4 live campaign has run.

The canonical machine-readable development record is [product-v040-bounded-remediation-development.json](product-v040-bounded-remediation-development.json). Frozen Goal and history are verified by `PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.ci.verify_product_v040_history`.

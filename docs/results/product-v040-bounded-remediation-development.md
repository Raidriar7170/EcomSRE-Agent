# Product v0.4 bounded remediation development

PR-A (#91) and PR-B (#92) are merged. PR-B merged at `e1292b34d3fbca00522347db4d7c63af52907b9d`; its exact-head CI passed with 6,416 tests passed, 21 skipped, Ruff PASS and mypy PASS for 679 source files. Both merges preserved the reviewed and tested trees.

PR-C implements fresh state binding, transactional single-use authorization, one-active-target enforcement, fenced leases and persistent write intents. Local validation passed with 233 focused tests, Ruff, mypy for 227 source files, and frozen-history verification. It reached `ECOMSRE_PRODUCT_V040_C_STATE_BOUND_AUTHORIZATION_PASS`; independent review returned PASS / Must Fix 0 / Claim Accuracy PASS after two repaired findings. Committed-content closure, exact-head GitHub CI and merge remain pending. The [state authorization audit](../analysis/product-v040-state-authorization-audit.md) records the guarantees and limits.

Diagnosis remains unchanged and read-only. PR-C contains no real state provider, executor, mutation adapter, receipt or recovery verifier. A committed intent does not itself execute a write. No Product v0.4 live campaign has run, and overall Goal completion is not claimed.

The canonical machine-readable development record is [product-v040-bounded-remediation-development.json](product-v040-bounded-remediation-development.json). Frozen Goal and history are verified by `PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.ci.verify_product_v040_history`.

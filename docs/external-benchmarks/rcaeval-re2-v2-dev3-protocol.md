# RCAEval RE2 v2-dev.3 Development Protocol

Protocol: `rcaeval-re2-v2-dev.3`

Classification: `DEVELOPMENT_VISIBLE / DESIGN_SET / NOT_EXTERNAL_HOLDOUT / NOT_PRIMARY_INFERENCE`

This is the final infrastructure-only development protocol. It starts from the immutable PR #16 head and preserves the failed-gate evidence in PR #14, PR #15, and PR #16.

Agent, Specialist, Judge, model, prompt, split, and F0 semantics are unchanged. The bounded change adds an identity-free audit of the five dev.2 Provider failures, one strictly allowlisted transport retry per semantic operation, typed Provider-attempt evidence, and token accounting that separates a known lower bound from explicitly unknown failed attempts and a frozen conservative upper bound.

The retry allowlist is limited to connection reset or disconnect, transient TLS termination, timeout before a valid response, HTTP 429, and HTTP 5xx. A retry is byte-identical, uses the same model, prompt, tool schema, evidence, timeout, and operation identity, and has no jitter or exponential backoff. Schema, protocol, local-contract, semantic-result, and result-quality failures are never retried.

The execution order is implementation commit, passing implementation CI, evaluation-root lock, dev.2 Failure Audit lock, 72/360/480 zero-Provider Admission, Provider-ready verification, 72-run Smoke, and—only after a passing Smoke Gate—the remaining DESIGN rows. Smoke terminals are reused without another Provider call.

The 120-case DEV_VALIDATION split is metadata-only and is not executed. RE2-TT is forbidden. Regardless of the Provider Smoke result, this protocol does not create a dev.4; the next engineering stage is the Single-first Adaptive RCA Agent.

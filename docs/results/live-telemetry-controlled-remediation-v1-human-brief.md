# Live Telemetry Controlled Remediation v1 — Human Brief

**Current marker:** `IMPLEMENTATION_READY_FOR_NO_FAULT_PREFLIGHT`

This branch implements one local, reversible OpenTelemetry Demo scenario. It
does not yet claim that the environment preflight passed, that a human approved
the plan, or that a positive remediation run occurred. Invocation A must first
prove real Metrics, Logs, and Traces and clean owned-resource teardown. It will
then freeze the exact approval request and stop for a human command.

The only possible live change after human approval is restoring the registered
`payment` configuration from the frozen fault document to the frozen baseline
document. There is no shell command surface, second remediation candidate,
production endpoint, remote Docker, Kubernetes, release, or autonomous
approval.

Evidence boundary: `LIVE_LOCAL_SANDBOX_DEMO`,
`CONTROLLED_FAULT_INJECTION`, `HUMAN_APPROVED_REMEDIATION`, `NOT_PRODUCTION`,
`NOT_EXTERNAL_BENCHMARK`, `NOT_SECURITY_VULNERABILITY_DETECTION`.

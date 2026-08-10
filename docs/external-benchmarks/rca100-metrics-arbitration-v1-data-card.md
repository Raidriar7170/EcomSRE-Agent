# RCA100 Metrics Arbitration v1 Data Card

Status: `FROZEN_EXTERNAL_HOLDOUT_DATA_CARD`

RCA100 is an official benchmark of 103 chaos-drill incidents over a Kubernetes
and OpenTelemetry demo store. This evaluation binds the repository
`https://www.aiops.cn/gitlab/aiops-live-benchmark/agenticopseval.git` at commit
`fd92cae17e6e14fa3ed0f3963c31838151fbdaa7`. The data license is CC BY-NC-SA
4.0. The label-blind input lock contains 103 case directories and 721
agent-facing files; benchmark labels are excluded from that checkout.

The model-visible data is limited to task alert text and bounded projections of
Metrics, Logs, and Traces. Events and full Alerts are excluded. Topology supports
deterministic canonical identity only. The complete 103-case manifest remains
the denominator regardless of parse, projection, model, or schema failures.

The source is a public benchmark and the model snapshot predates its formal
2026 publication. That timing reduces but cannot prove the absence of indirect
pretraining exposure. No external-identifier web lookup, case-specific rule,
manual whitelist, answer-guided formula choice, or post-result tuning is used.
This caveat must accompany any public claim.

The public result is aggregate-only. The case data, schedule mapping, model
payloads, terminals, evaluator records, and labels remain Git-external. No
dataset file or case-level record is committed. The intended use is narrow:
independent evidence about the frozen deterministic M3 rule, not a release,
deployment, equivalence, non-inferiority, or general autonomous-agent claim.

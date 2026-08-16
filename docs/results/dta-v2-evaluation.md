# DTA v2 Replay Held-out Evaluation

Result: `COMPLETED_HELD_OUT_NEGATIVE`

The one-time sealed replay evaluation completed with truth isolation, scorer
verification, zero unsafe proposals, and all prohibited-action counters at
zero. It did not support Tool Use superiority or held-out generalization.

| Metric | One-shot Full Context | Adaptive Tool-Using |
|---|---:|---:|
| Root exact match | 3/3 | 3/3 |
| Mechanism accuracy | 3/3 | 2/3 |
| Runbook Top-1 | 3/3 | 1/3 |
| Evidence validity | 3/3 | 1/3 |
| Action precision | 3/3 | 1/3 |
| No-action accuracy | 0/0 | 0/0 |
| Escalation accuracy | 0/0 | 0/0 |
| Unsafe proposal attempts | 0 | 0 |

## Cost

| Cost | One-shot Full Context | Adaptive Tool-Using |
|---|---:|---:|
| Semantic read-tool dispatches | 0 total / 0.00 mean | 9 total / 3.00 mean |
| Deterministic context reads | 12 total / 4.00 mean | 0 total / 0.00 mean |
| Provider turns | 6 total / 2.00 mean | 15 total / 5.00 mean |
| Input tokens | 12,898 total / 4,299.33 mean | 42,986 total / 14,328.67 mean |
| Output tokens | 1,152 total / 384.00 mean | 1,422 total / 474.00 mean |
| Total tokens | 14,050 total / 4,683.33 mean | 44,408 total / 14,802.67 mean |
| Latency | 13,269 ms total / 4,423.00 ms mean | 27,481 ms total / 9,160.33 ms mean |

The held-out set contained no no-action or escalation cases, so those two
denominators are zero. Those paths passed only in the separate visible
development evaluation.

The seal SHA-256 is
`0f944e79f0958f285006c3bdc3cf8f82b8a71731d8d96d02b474f254a54e247a`;
the immutable source report semantic SHA-256 is
`26b4002fe0232a2d8b03295e98b3c023e9409ae30eaba3b2e21ae1d1523524e6`.
The result is bound to historical Agent identity
`aa08b5869aaac7e4ad4b1084367fc99a01c6dd05521ea933fddf9b5fb364ca61`
and was not rerun after PR-F changed the Prompt.

This is replay diagnosis and action-selection evidence only. It is not live
recovery, production, release, or arbitrary autonomous-remediation evidence.

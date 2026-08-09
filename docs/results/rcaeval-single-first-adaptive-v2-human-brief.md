# Single-first Adaptive v2 开发结论简报

结论：`ADAPTIVE_V2_TUNE_GATE_NOT_PASSED`

这不是外部验证，也不是生产效果结论。三轮均使用已经消费的 60-case
开发集、同一个 Agent v2、同一模型、同一重试策略、同一节奏和同一开发门槛。

- Candidate-1：0/60 完成，59 个 HTTP 429、1 个 TLS transient。
- Candidate-2：0/60 完成，60 个 HTTP 429。
- 操作者随后确认 API credit 已耗尽并完成充值。
- Candidate-3：充值后 60/60 完成、0 个 Provider failure，证明容量已恢复。

Candidate-1/2 只能证明 Provider 当时不可用，不能作为算法准确率。唯一可评估的
Candidate-3 达到 Root 49/60、Pair 25/60、Direct 60/60、Mean Ops 1.0、
Damage 6、Rescue 2。它满足完成率、成本、Trace、Override 和本地合同门槛，
但未达到 Root 51、Pair 29、Damage 不高于 Rescue、Damage Rate 不高于 5%
四项要求。

因此不选择候选、不运行唯一一次 120-case regression、不生成 Fresh External
Holdout 计划。全部结果继续分类为 `CONSUMED_OBSS_DEVELOPMENT_RESULT /
NOT_EXTERNAL_VALIDATION`，不得包装为外部改进证据。

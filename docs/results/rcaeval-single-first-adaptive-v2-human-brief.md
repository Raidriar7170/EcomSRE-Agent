# Single-first Adaptive v2 开发结论简报

结论：`ADAPTIVE_V2_TUNE_GATE_NOT_PASSED_AFTER_CANDIDATE5`

这不是外部验证，也不是生产效果结论。所有算法结果都来自已经消费的
60-case TUNE_SET；没有访问 RE2-TT 或任何新外部数据。

- Candidate-1：0/60 完成，59 个 HTTP 429、1 个 TLS transient。
- Candidate-2：0/60 完成，60 个 HTTP 429。
- Candidate-3：首次充值后 60/60 完成，但 60 个全部 Direct。
- Candidate-4：59/60 完成，Gate 选择性升级 16 个记录，但 8 个
  Initial-wrong escalation 中没有正确 Specialist alternative。

Candidate-4 的零 Provider Metrics Top-K 分析显示：8 个 completed
Initial-wrong 案例全部被 Gate 升级；True Root Coverage@1 / @2 / @3 / @6
为 6/8 / 8/8 / 8/8 / 8/8；确定性 Metrics alternative 命中 7/8。该结果
支持 `CASE_E_SPECIALIST_GENERATION_FAILURE`，但 truth-matching alternative
在 bounded Logs 中的可见数为 0，因此从一开始就是高风险假设。

Candidate-5 只做一个主要算法修改：用 Metrics 锚定的
Initial-vs-Alternative Logs pairwise verifier 替代自由生成 Specialist，
并由确定性、keep-by-default Fusion 消费其结果。Gate、Trace、Indicator、
模型、节奏、重试、数据切分和验收门均未改变。

Candidate-5 TUNE 结果：

- 60/60 完成，HTTP 429、Provider failure、schema/privacy/schedule failure
  均为 0，说明 API credit 充值后容量已恢复；
- Initial → Final Root：45 → 45，Damage / Rescue / Net 为 0 / 0 / 0；
- Initial → Final Pair：23 → 23，Damage / Rescue / Net 为 0 / 0 / 0；
- Direct / Logs / Traces / Both：37 / 23 / 0 / 0；
- Pairwise INITIAL / ALTERNATIVE / INCONCLUSIVE：7 / 1 / 15；
- Correct / Wrong Override：0 / 0；Mean Ops 1.3833；
- Provider attempts 85，transport retries 2。

Pairwise verifier 的 23 次调用全部完成，但唯一一次 `ALTERNATIVE` 偏好
没有满足 root-role 支持条件，因此 Fusion 仍在 60 个记录上全部保留
Initial。它通过了执行、路由成本、Trace 与 override 门，但 Final Root
45 < 51、Final Pair 23 < 29，且同轮 Root Rescue 没有严格大于 Damage，
Root Net 也没有达到 1。

因此 Candidate-5 未通过冻结 TUNE Gate。按协议停止：不运行 120-case
Regression，不创建 Candidate-6，不做结果驱动重跑，不生成 fresh external
holdout。下一步应是算法复盘。PR #19 只标记 Ready for algorithm review，
全部结论保持 `CONSUMED_OBSS_DEVELOPMENT_RESULT / NOT_EXTERNAL_VALIDATION`。

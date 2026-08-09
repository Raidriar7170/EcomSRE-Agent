# Single-first Adaptive v2 开发结论简报

结论：`ADAPTIVE_V2_TUNE_GATE_NOT_PASSED_AFTER_REAL_ALGORITHM_ITERATIONS`

这不是外部验证，也不是生产效果结论。所有算法结果都来自已经消费的
60-case TUNE_SET；没有访问 RE2-TT 或任何新外部数据。

- Candidate-1：0/60 完成，59 个 HTTP 429、1 个 TLS transient。
- Candidate-2：0/60 完成，60 个 HTTP 429。
- Candidate-3：充值后 60/60 完成、0 个 Provider failure，证明容量恢复；
  但 60 个全部 Direct，Gate 没有真正触发 Specialist。
- 零 Provider 诊断：Initial Root 49/60；10 个 unstable 全部 Direct；离线
  Policy A / B 分别会升级 3 / 20 个记录。

Candidate-4 使用修复后的选择性 Gate，结果为：

- 完成 59/60，HTTP 429 为 0，另有 1 个 schema failure；
- Initial → Final Root：51 → 51，Damage / Rescue / Net 为 0 / 0 / 0；
- Initial → Final Pair：27 → 27，Damage / Rescue / Net 为 0 / 0 / 0；
- Direct / Logs / Traces / Both：43 / 16 / 0 / 0；
- Escalation Precision / Recall：8/16、8/8；
- Correct / Wrong Override：0 / 0；Mean Ops 1.25。

它满足完成率、Root、路由成本、Trace、429 和 override 门槛，但没有通过
TUNE Gate：Final Pair 27 < 29；Root Rescue 0 没有严格大于 Damage 0；Root
Net 0 < 1；并且存在 1 个 schema failure。

Candidate-5 未执行。Direct 43 与 Recall 1.0 不支持继续调 Gate；8 个
Initial-wrong escalation 中没有正确 Specialist Root alternative，不能授权
Fusion 阈值修改；Pair 也没有因 indicator override 发生 Damage。schema
failure 不属于 Work Package F Case A-D 的单一算法方向。强行运行会变成
无证据调参或结果驱动重试。

因此不选择候选，不运行唯一一次 120-case Regression，不生成 Fresh External
Holdout 计划，也不创建 candidate-6。全部结论保持
`CONSUMED_OBSS_DEVELOPMENT_RESULT / NOT_EXTERNAL_VALIDATION`，PR #19 只进入
algorithm review，不得包装成外部提升证据。

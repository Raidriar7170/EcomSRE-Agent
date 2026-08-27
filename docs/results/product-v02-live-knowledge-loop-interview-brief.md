# Product v0.2 live knowledge-loop pilot — Human Brief

## 一句话结论

本阶段完成了可审计的 v0.2 live pilot 工程骨架，但唯一授权的校准在首次
fault attempt 之前因 `BASELINE_INSUFFICIENT_WINDOWS` 停止，最终状态必须保留为
`BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE`。

## 可以确认的事实

- 已实现受控 flag controller、确定性 checkout traffic、append-only attempt
  ledger、真实 Runtime authority、恢复验证和公开/私有证据分离。
- 校准 campaign 已消费，`live_attempt_count=0`。
- 外层 baseline 已恢复，owned Demo cleanup=`CLEAN`。
- action authority=`NONE`，agent writes=`0`，Runbook executions=`0`。

## 不能宣称的结果

不能宣称 profile 可观测或不可观测，不能宣称 Open-World 成功，不能宣称三次
正样本形成同一 family，也不能宣称规则挖掘、shadow gate、promotion 或 held-out
recurrence 成功。后续恢复必须使用新的、单独授权的 Goal，而不能修改或重跑本次
冻结结果。

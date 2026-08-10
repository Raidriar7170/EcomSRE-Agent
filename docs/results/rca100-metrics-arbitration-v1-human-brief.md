# RCA100 外部评估 Human Brief

状态：`RCA100_EVALUATOR_REPAIR_FINAL_REPORT_FROZEN_READY_FOR_PUBLICATION_REVIEW`

方法状态：`POST_LOCK_EVALUATOR_REPAIR_DISCLOSED`

结果分类：`RCA100_EXTERNAL_M3_NOT_SUPPORTED`

## Post-lock Evaluator Repair Disclosure

103 个预测在 answer material 获取前已经一次性生成并锁定。原 frozen
evaluator 错误理解官方 mapping envelope，原协议永久保留为
`BLOCKED_PROTOCOL_DRIFT`。本次单独授权的 evaluator-only repair 只提取冻结的
`task_to_case_id`；没有 Provider 调用、预测重跑、M3 修改或 case replacement。
除 envelope extraction 外，scorer、entity matching、statistics 和固定 denominator
均未修改。

- Initial / Final Root 正确：16 / 10（固定分母 103）
- Root Damage / Rescue / Net：6 / 0 / -6
- Root Damage Rate：0.375000
- 95% 配对区间：[-0.106796, -0.019417]
- McNemar exact p：0.03125
- KEEP / OVERRIDE：63 / 36

允许的声明边界：External RCA100 predictions were generated answer-blind and
scored after a separately authorized evaluator-envelope repair. 不得声称原
preregistered evaluator 未经修改地端到端执行。

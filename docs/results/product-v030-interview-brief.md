# Product v0.3 面试简报 — 实测知识演化与 H1 根一致性通过

当前实测终态：ECOMSRE_PRODUCT_V030_H1_EXTENSION_KNOWN_PASS。
这是 2026-09-03 16:25:35 UTC 的已清理、待整合快照；独立代码、实测与文档
评审已通过，Must Fix / Should Fix 均为 0。完整 Goal 仍要求一次 exact-head
全仓 CI 和合并。整合结果以
[PR #88](https://github.com/Raidriar7170/EcomSRE-Agent/pull/88) 的最终记录为准，
本文不提前宣称 CI 或 merge 成功。

## 可以准确讲述的闭环

在固定版本 OTel Demo 的本地 full-mode 环境里，Product 先识别三次未知队列
异常，将它们聚为同一个家族；经用户明确预授权的人工治理检查点，Runtime
从真实正负例自动挖掘规则、执行严格 Shadow，再注册为环境级只读扩展。
新的 H1 窗口通过正常诊断顺序命中该扩展，且发现阶段与扩展阶段的根均为
fraud-detection。没有把新机制加进 frozen Core，也没有让模型手写通过规则。

| 阶段 | live-005 实测 |
| --- | --- |
| Baseline | 新环境、5/5 DEMO_ONLY 窗口、30/30 健康交易、四服务 Resources |
| N0-A / N0-B | 各 30/30，均 NO_INCIDENT，零 capability limitation |
| C1 | 10 次预期 HTTP 500；CORE_KNOWN / CONFIGURATION_ERROR / payment |
| C1 队列阴性 | lag=0<20，fraud Runtime healthy，queue anomaly 缺失，子句 false，CONCLUSIVE |
| P1 / P2 / P3 | 各 3/3；OPEN_WORLD / CONCURRENCY / fraud-detection |
| 家族 | 唯一三成员、三窗口，root consistency=1.0；N0/C1 排除 |
| 规则 | Runtime 自选 queue-lag + Runtime healthy，两来源，三正三负 |
| Shadow | 严格指标全过；14 outcomes 中 13 条实际评估 |
| Promotion | 一条 ACTIVE 扩展，registry version 1，家族 PROMOTED |
| H1 | EXTENSION_KNOWN / kafka-queue-backlog / fraud-detection，完整门禁 PASS |

三例相似度为 0.8061 / 0.8300 / 0.9732，均高于冻结的 0.65。
所选规则 recall=1.0，FPR、Core overlap、No-Incident FPR 均为 0。
Shadow 的引用有效性、source reachability、反事实一致性均为 1.0；
来源失败安全，权限违规为 0。H1 无 provisional report、无新家族，支持引用
来自实际 queue Metrics 与 Runtime，根精确匹配本次家族多数根。

## 两个窄修复及一次根语义修复

1. 孤立十秒内存增长不能独立成为强 Product Open-World 残余；需要同服务
   内存压力日志、restart/unhealthy Runtime、错误 Metrics、错误 Trace
   定位，或第二个独立持续增长窗口佐证。Bridge 和 Knowledge 重建共用策略；
   原 Resource 证据、数值阈值与 Core 内存泄漏条款不变。
2. C1 只按 payment/checkout/fraud-detection 及队列阴性所需证据验收。
   无关 Logs/Traces 覆盖缺口原样披露，不虚构全局完整性。
3. 新 Open-World 报告的根跟随选择其域的唯一异常归属服务；存在多服务歧义
   时保留原 fallback。不是服务名硬编码，也不是额外因果证明。

历史 live-002/live-003 控制失败及 live-004 的 H1 根不一致失败均保留。
live-004 曾出现发现根 checkout、扩展根 fraud-detection，不能改写为成功。
用户随后明确授权了本次 live-005 完整修正闭环；这次三例发现根、家族根与
H1 根实际一致，且未降低任何门禁。原失败 Baseline 请求和受限恢复证据也保留；
后续成功诊断不等于证明首次请求失败的根因。

## 必须主动说明的边界

- 这是一个固定机制、有限正负例的本地 Demo，不证明生产环境泛化能力。
- C1 的 SOURCE_LOGS_COVERAGE_GAP / SOURCE_TRACES_COVERAGE_GAP 仍存在；
  queue-negative 结论只依赖已充分观测的相关来源。
- 首个扩展没有 OTHER_EXTENSION 对照，标为 NOT_AVAILABLE，不冒称做过该实验。
- ACCEPT_AS_NEW 与 Promotion 执行的是用户此前明确预授权，记录没有冒称
  用户刚刚逐项人工检查证据。
- Product 始终只读：action/remediation authority=NONE；Provider、Agent 写、
  Runbook 均为 0。Harness 故障注入与 Product 行动权限是两回事。

cleanup=CLEAN：本次 28 容器、1 网络、3 临时卷已移除，所属资源为零，
非所属资源未变，私有 DB 和全部失败证据保留。新聚焦回归 221 passed，
Ruff/mypy 通过；唯一最终全仓验证由 PR exact-head CI 执行，不额外在本地重复。

完整机器数据见[结果](product-v030-live-knowledge-evolution.json)和
[家族/规则摘要](../analysis/product-v030-family-and-rule-summary.json)。

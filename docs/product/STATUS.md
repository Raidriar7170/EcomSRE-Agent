# EcomSRE-Agent 当前状态

**v0.3 已完成：可部署的单租户、只读 Product 原型。**

当前支持环境登记、服务身份归一、连接器验证、多窗口 Baseline、
持久化证据、确定性诊断和人引导的环境级知识演化，不是生产 SRE 控制平面。

## 已验证结果

### 健康系统验收

[Product v0.2.4 最终验收](../results/product-v024-nofault-acceptance-final.json)：
30/30 checkout 事务成功；Metrics / Resources / Traces / Logs / Runtime 均有证据；
`NO_INCIDENT`；No-Fault scorer =
`ECOMSRE_PRODUCT_V023_NOFAULT_FULLY_SUPPORTED`（简称 `FULLY_SUPPORTED`）；
能力限制 0；action_authority = `NONE`；cleanup = `CLEAN`。
这是指定窗口的结果，不是所有服务、所有时间的完整覆盖证明。

### 知识演化闭环

[Product v0.3 实验](../results/product-v030-live-knowledge-evolution.json)
中的 `live_005` 是最终完整循环；前面的循环为保留历史。

- N0-A / N0-B：各 30/30 健康事务，均为 `NO_INCIDENT`。
- C1：`CORE_KNOWN / CONFIGURATION_ERROR / payment`，
  队列负对照明确成立，无关 Logs / Traces 缺口仍保留。
- P1 / P2 / P3：`OPEN_WORLD / CONCURRENCY / fraud-detection`。
- 一个三成员故障族，根一致性 1.0，两两相似度约 0.8061 / 0.8300 / 0.9732。
- Runtime 选中 `core:RUNTIME_HEALTHY + ga:METRIC_QUEUE_LAG_OUTLIER`。
- Shadow recall 1.0、FPR 0.0、Core overlap 0.0、No-Incident 误报 0；
  13 个已评估用例，另一个 `OTHER_EXTENSION` 分层不可用。
- 一个 ACTIVE 扩展 `kafka-queue-backlog`；H1 =
  `EXTENSION_KNOWN / kafka-queue-backlog / fraud-detection`，
  无新故障族、无临时报告。

直接证据：[故障族、规则、Shadow、Promotion 与 H1 摘要](../analysis/product-v030-family-and-rule-summary.json)。
门控执行方式和指标分母见[知识演化](KNOWLEDGE_EVOLUTION.md)。

## 当前权限

Product action/remediation authority = `NONE`；
Provider / Agent write / Runbook = 0；cleanup = `CLEAN`。
实验故障注入由另行授权的本地控制器执行，不归模型所有。
只读指被诊断系统；Product 仍会写自己的 SQLite、证据对象和人工治理记录。

## 集成状态与历史结果说明

[PR #88 最终完成记录](https://github.com/Raidriar7170/EcomSRE-Agent/pull/88#issuecomment-5529165572)
确认已合并：`ECOMSRE_PRODUCT_V030_LIVE_KNOWLEDGE_EVOLUTION_COMPLETE`。

最终 CI：6,326 passed / 21 documented skips / 一个已有 Starlette warning；
Ruff PASS；mypy PASS（669 个源文件）；两项工作流通过。
15 个 skip 来自未提交的私有证据回放，另 6 个保留历史 DTA 边界；
不同范围的测试数量不可相加。

结果 JSON 保留为集成前快照；`completion_terminal_minted = false`
和早期阻塞字段不代表当前状态。
当前 Git 与上述合并后记录负责最终集成状态，原始结果负责实验观测事实，
两者不互相改写。

## 限制与入口

一个有界本地 OTel 环境、一个学到的机制、单租户 SQLite；
未验证 Kubernetes、HA、生产规模或跨公司泛化。
完整 live 镜像锁与部分原始证据未公开，公开演示使用确定性夹具。

[快速体验](QUICKSTART.md) · [架构](ARCHITECTURE.md) ·
[限制](LIMITATIONS.md) · [历史索引](../history/PROJECT_EVOLUTION.md)

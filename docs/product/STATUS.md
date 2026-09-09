# 当前状态 · Product v0.4.1

**收尾完成；v0.4 的真实 Payment 恢复已合并。单租户本地 Product 原型。**

## Diagnosis authority

Diagnosis `action_authority = NONE`。证据、SQLite、CAS 与治理记录的持久化不代表有权修改被诊断服务。未知机制进入人引导知识演化，不自动获得执行权限。

## Remediation authority

默认关闭。唯一映射为 `CORE_KNOWN / payment / CONFIGURATION / CONFIGURATION_ERROR` → `ROLLBACK_CONFIGURATION` → `RESTORE_BASELINE_CONFIGURATION`，maximum forward steps = 1。Candidate、独立批准、fresh Current State、单次授权、WriteIntent、隔离执行与双窗口验证共同约束动作。见 [REMEDIATION](REMEDIATION.md)。

## 已验证结果

| 版本 | 结果与范围 | 来源 |
| --- | --- | --- |
| v0.2.4 | 30/30 checkout，五类证据，`NO_INCIDENT`，能力限制 0 | [JSON](../results/product-v024-nofault-acceptance-final.json) |
| v0.3 | 三个 Open-World 窗口；一个 Fault Family；Shadow recall 1.0 / FPR 0.0；H1 = `EXTENSION_KNOWN` | [摘要](../analysis/product-v030-family-and-rule-summary.json) |
| v0.4 | 194 次健康 Payment 请求，最后 30/30 fault probes 如预期失败；一次 gateway restore；两个窗口各 39 请求 / 0 错误；`RECOVERED` | [JSON](../results/product-v040-minimal-payment/live-result.json) |
| v0.4.1 | 5/5 安全案例通过；S0–S2 写入 0，S3/S4 各 1；无未授权或重复写入；每例 CLEAN | [结果包](../results/product-v041-live-safety/README.md) |

v0.3 Shadow 为 3 个正例、10 个负向/反事实/失败用例，`OTHER_EXTENSION` 未观测。v0.4 Minimal 健康 Diagnosis 是 `INSUFFICIENT_EVIDENCE`：Logs、Runtime、Traces 未提供给健康诊断，不改写为 `NO_INCIDENT`。v0.4 cleanup 移除 10 容器 / 3 网络 / 2 卷，剩余 0/0/0，非 owned 资源未变。上述 Product 实测 Provider calls = 0。

[PR #102 完成记录](https://github.com/Raidriar7170/EcomSRE-Agent/pull/102#issuecomment-5599847699)确认 v0.4 合并及 6520 tests passed / 21 skipped / mypy 705 files。这些计数仅属于 PR #102，不能与其他版本相加，也不是 v0.4.1 的测试结果。

## Current limitations

默认 Product 仍只读；一个本地恢复 Runbook；单租户 SQLite；无生产 self-healing、完整 28 服务验收、跨环境泛化、exactly-once、HA、Kubernetes、长期 SLO。见 [LIMITATIONS](LIMITATIONS.md)。

## Public quickstart

[QUICKSTART](QUICKSTART.md)提供结果导览、知识演化 Fixture、恢复 Fixture 与已有 Live Evidence Verifier。公开 verifier 不重跑实验。历史失败证据和旧 v03 手册保留在仓库中。

## v0.4.1 实测安全结果

| Case | 实测终态 | Product 外部写入 | Cleanup |
| --- | --- | ---: | --- |
| S0 Healthy | NO_CANDIDATE；健康 Diagnosis INSUFFICIENT_EVIDENCE | 0 | CLEAN |
| S1 Revoked | APPROVAL_REVOKED / NO_WRITE | 0 | CLEAN |
| S2 Drift | CONFIGURATION_DRIFT_NOT_VISIBLE / NO_WRITE | 0 | CLEAN |
| S3 Replay | RECOVERED；相同键返回原对象，第二次 run_one 拒绝 | 1 | CLEAN |
| S4 Evidence failure | APPLIED；VERIFICATION_FAILED / ESCALATE_HUMAN | 1 | CLEAN |

单次观察的完整时延、时钟语义和缺失项见 [timing 说明](../results/product-v041-live-safety/README.md#observed-timing)。

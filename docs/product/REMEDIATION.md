# 受限恢复 · Product v0.4.1

诊断提供证据结论，本身始终没有修改服务的权限。即使结论正确，批准也可能撤销，环境可能变化，目标或 Baseline 可能已失效；因此每次执行前必须建立新的状态绑定授权。

## 三种对象，三种职责

| 对象 | 含义 | 是否允许外部写入 |
| --- | --- | --- |
| Candidate | 确定性规则生成的候选动作与证据绑定 | 否 |
| Approval | 独立 Operator 批准固定范围，有时限且可撤销 | 否 |
| AttemptAuthorization | 当前状态、目标、Baseline、批准与唯一 Attempt 的单次绑定 | 仍须通过 WriteIntent 与执行前检查 |

真实实验中的批准记录执行用户事先明确的 Goal 授权；不声称运行中逐个人工点击，也不是模型自我批准。

## 唯一 Runbook

`CORE_KNOWN / payment / CONFIGURATION / CONFIGURATION_ERROR` → `ROLLBACK_CONFIGURATION` → `RESTORE_BASELINE_CONFIGURATION`。最大 forward steps 为 1，无替代 Runbook。扩展知识晋升不注册动作。

## 隔离和不确定结果

Product 默认不启用恢复 profile。API 接触状态读取通道；Executor 是独立进程，不是 HTTP execute route，没有 Docker socket，不接受自由 shell 或模型生成参数。gateway 用固定私有配置控制唯一 Baseline 文档。

WriteIntent 在发送前持久化，gateway 单次消费再发送外部请求；因此“消费一次”本身不等于“写成功”。`APPLIED` Receipt 及配置/flagd readback 提供执行结果，独立窗口进一步验证业务。写入或 Receipt 不确定时保留 `OUTCOME_UNKNOWN` 并转人工，禁止猜测成功或盲目重试。

## 双窗口恢复

两个非重叠、策略绑定、Receipt 之后的外部窗口必须满足配置、基础设施、端点、业务请求数和错误率要求。`APPLIED` 只说明动作结果，不能替代 `RECOVERED`。验证证据不足会得到 `VERIFICATION_FAILED / ESCALATE_HUMAN`，不再次写入。

## 真实结果

[PR #102 的实测](../results/product-v040-minimal-payment/live-result.json)：194 次健康 Payment 请求；`paymentFailure=100%` 后最后 30/30 探针失败；严格 `CORE_KNOWN`；gateway 消费 1 次；两个窗口各 39 请求 / 0 错误；`RECOVERED`；Provider calls = 0；owned cleanup 后 0/0/0。健康 Diagnosis 保留 `INSUFFICIENT_EVIDENCE`。

## Live Safety Matrix

[S0–S4 结果包](../results/product-v041-live-safety/README.md)分别检查健康无候选、撤销批准、状态漂移、重复请求/执行和恢复验证证据不足。每个案例使用独立环境、数据库、键空间与私有证据目录。实际结果和 timing 以机器结果为准。

## Claim Boundary

一个 pinned 本地配置故障、一个固定 Runbook。不能外推生产 self-healing、通用 exactly-once、跨环境泛化或所有攻击面覆盖。完整 Harness 的历史失败未被 Minimal 成功改写。cleanup 仅清理 owned 实验资源，不能充当 Product 恢复成功证据。

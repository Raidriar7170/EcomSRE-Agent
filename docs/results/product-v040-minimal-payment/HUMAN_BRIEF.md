# Product v0.4 最小 Payment Live 审查简报

实测终态为 **RECOVERED**。执行源码为 `539824ee5c3b2497c890a52a7aaa1fff455b6b8e`。最终审查、完整测试、exact-head CI 和合并状态由 PR #102 独立记录；本简报不提前宣称 Goal 已合并完成。

真实 pinned Payment 在健康配置下可成功完成 Charge；启用 `paymentFailure=100%` 后，末段 30/30 请求出现预期失败。当前 Product 根据真实 Changes 与 Prometheus 错误指标诊断为 `CORE_KNOWN / payment / CONFIGURATION_ERROR`，并生成唯一 `ROLLBACK_CONFIGURATION` Candidate。

实际持久化的 Approval 映射用户事先激活的 Goal 授权。Approval 后重新采集状态，当前 Product 创建 Authorization、WriteIntent；独立 Executor 经受限 Gateway 执行一次固定 Baseline restore，并持久化 `APPLIED` StepReceipt。两个互不重叠的恢复窗口分别为 **39 次请求、0 次错误**，当前 Recovery Verifier 判定 `RECOVERED`。Gateway 消费记录为 1，Provider/LLM 调用为 0。

清理删除本次 owned 的 10 个容器、3 个网络、2 个卷，残留分别为 0；非 owned 资源未变，Baseline flag 已恢复。构建镜像与私有证据保留。七次工程尝试全部保留，早先两个已恢复配置但缺失有效恢复窗口的 Product Attempt 仍为 `VERIFYING`，不得改写为成功。

健康阶段有 194 次真实业务请求、零启动后错误，以及满足当前 Product 5/5 窗口的 Active Baseline。但健康诊断本身为 **INSUFFICIENT_EVIDENCE**，Product 未获得 Logs/Runtime/Traces。这里依 Goal §6 使用业务与 Baseline 作为有限健康状态证明，不宣称 `NO_INCIDENT` 或完整遥测诊断覆盖。恢复业务观测类型明确为 `DIRECT_PAYMENT_TRAFFIC`，并未冒充 Checkout 流量。

公开 JSON 绑定当前 Product 对象、恢复观测 CAS 与诊断证据承诺；校验器重新运行现有纯恢复评估器。原始遥测、控制密钥和私有路径不公开。审查应核对授权与对象关联、一次固定写入、证据与清理真实性、缺失遥测的声明边界。详细记录见 [README](README.md)、[Live 结果](live-result.json) 和 [工程尝试](engineering-attempts.json)。

允许的结论仅为 pinned 本地最小 Payment 环境中的一次真实配置故障恢复闭环；不代表生产自动自愈、完整 28 服务稳定、跨环境泛化或 exactly-once 外部副作用。

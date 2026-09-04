# 能力与限制 · Product v0.3

## 已证明什么

一个有界本地 OpenTelemetry Demo 多服务环境中：

- 30/30 健康 checkout 事务，五类遥测有证据；
  `NO_INCIDENT`、No-Fault `FULLY_SUPPORTED`、能力限制 0。
- 三个 Open-World 窗口形成故障族，Runtime 挖掘两源规则，
  严格 Shadow 通过，一个扩展激活，H1 命中 `EXTENSION_KNOWN`。
- Product 动作/修复权限为 NONE，Provider / Agent write / Runbook = 0；
  实验 cleanup = `CLEAN`。
- 确定性演示覆盖 API、Worker、Baseline、知识门控和重启持久化。

证据：[当前状态](STATUS.md) ·
[健康验收](../results/product-v024-nofault-acceptance-final.json) ·
[知识闭环](../analysis/product-v030-family-and-rule-summary.json)。

## 不能外推什么

- **一个环境、一个学到的机制。** Kafka 队列积压闭环不是通用诊断准确率，
  不是跨公司、跨遥测栈泛化证明。
- **单租户 SQLite。** 一个 API、一个 Worker、SQLite WAL；
  无 PostgreSQL、分布式队列、多租户隔离或横向扩展验证。
- **无长期规模或 HA 验证。** 未建立生产负载、可用性 SLO、
  灾难恢复演练、安全认证或长期稳定性证据。
- **无 Kubernetes 生产控制平面。** Product 不挂 Docker socket，
  本地实验生命周期是独立授权的控制面。
- **只读诊断，无自主修复。** Product 写自己的证据与治理 DB，
  不等于能写被诊断系统；扩展晋升不会获得 Runbook 或 shell 权限。
- **本地阈值非普适规律。** 窗口、异常强度、相似度与规则门槛依赖环境，
  短基线 `DEMO_ONLY` 不代表生产校准。
- **有限健康窗口。** 30/30 不保证长期零误报；
  健康遥测噪声曾触发 Open-World，需要独立场景和长时间负对照。
- **其他扩展干扰未实测。** Shadow 的 `OTHER_EXTENSION` 不可用，
  destructive overlap = 0 不代表做过真实冲突实验。
- **人工门控方式有限。** 使用用户明确的事先授权，
  不是运行中重新逐条人工复核，见[知识演化](KNOWLEDGE_EVOLUTION.md)。
- **完整 live 证据部分私有。** 镜像锁、原始遥测和部分保留 DB 未提交；
  公开结果与确定性夹具核查不同层次事实，不能互相替代。

## 数据与诊断风险

标签发现不等于每条查询完整覆盖。
服务名称、Prometheus 标签、OpenSearch 字段与 Jaeger 约定会因部署不同而变；
Runtime / Changes 也必须绑定来源。
C1 的无关 Logs / Traces 缺口仍被保留。

缺失不是负证据；只有 `ABSENT_WITH_COMPLETE_COVERAGE` 可以作为反证。
聚类可能拆分同机制或合并相近机制，根一致性和高相似度不是因果关系证明。
规则挖掘与 Shadow 复用同环境证据，留出复发、反事实和来源失败检查
仍不能替代独立环境验证。

限制模型权威缩小了不可信输出影响范围，
但不证明连接器、持久化或诊断逻辑不存在缺陷。

## 下一步

优先补充独立环境/机制、真实其他扩展干扰、长期健康控制、
故障族拆合质量与部署恢复证据。
这些是后续方向，不是已执行或自动获得授权的实验。
[历史失败与教训](../history/PROJECT_EVOLUTION.md)继续保留。

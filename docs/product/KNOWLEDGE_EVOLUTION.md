# 开放世界发现与人引导知识演化

知识演化只从 `OPEN_WORLD` 报告开始，不修改冻结 Core 类型库，
不授予动作权限；新知识保存在当前环境的扩展故障库。

## 完整知识链

OPEN_WORLD → Incident Fingerprint → Fault Family → Human ACCEPT_AS_NEW →
Predicate Matrix → Runtime Rule Mining → Shadow Evaluation →
Human Promotion → ACTIVE Extension Registry。

1. Runtime 从绑定观测生成指纹：异常、来源、领域、根服务、拓扑和状态等；
   采用环境内确定性加权相似度，不是文本 embedding。
2. 故障族保留不同事件窗口和证据，多次读取同一事件不能冒充多次发生。
3. 人工决定是否接受为新类型；不足的数据继续积累。
4. 正例来自故障族，负例包括同环境已知故障与健康控制。
5. Runtime 枚举长度 1–3 的候选合取式，以 beam width 20 限制搜索；
   根据召回、误报、重叠与复杂度排序，不接受 LLM 手写即生效的规则。
6. 编译与影子评估检查引用、来源可达性、目标反事实、来源失败和既有类型冲突。
7. 人工晋升后，新事件仍按 Core → Extension → No-Incident → Open-World 诊断；
   扩展命中返回 `EXTENSION_KNOWN`，多重匹配不能任选一个。

挖掘与 Shadow 是不同门槛：当前 Shadow 要求 recall ≥ 0.75、FPR ≤ 0.10、
Core overlap = 0、健康误报 = 0、引用有效率及正例来源可达率 = 1，
并满足反事实、来源失败与权限检查。阈值是本地契约，不是普适最优值。
不足时保留 `NEEDS_MORE_INCIDENTS` / `NEEDS_MORE_NEGATIVES` 等状态。

后续故障族分配按 [DEC-062](../DECISIONS.md#dec-062--product-review-fixes-for-ambiguous-roots-and-missing-feature-similarity)
处理共同缺失：空特征、空状态签名和 `UNKNOWN` 领域一致不贡献相似分。
权重与 `0.65` 门槛保持不变，不对缺失维度重新归一化。
因此特征不完整时，即使已观测部分相同，总分也可能小于 1。
下文 v0.3 live 相似度保留为修复前的历史观测，未用新逻辑重算。

源码：[runtime.py](../../src/ecomsre/product/knowledge/runtime.py) ·
[repository.py](../../src/ecomsre/product/knowledge/repository.py) ·
[compiler.py](../../src/ecomsre/product/knowledge/compiler.py)。

## 证据的四种状态

| 状态 | 允许得出的结论 |
| --- | --- |
| `PRESENT` | 已取得满足谓词的证据 |
| `ABSENT_WITH_COMPLETE_COVERAGE` | 完整覆盖内确认未出现，可作为负证据 |
| `UNKNOWN` | 观测缺失或覆盖不足，不是反证 |
| `SOURCE_FAILED` | 数据源失败，不代表现象不存在 |

只有完整覆盖下的缺席才是负证据。
队列读取失败时，不能用“没看到 lag”证明没有队列积压。

## 实测 Kafka 队列积压案例

依据[完整实验的 live_005](../results/product-v030-live-knowledge-evolution.json)与
[故障族及规则摘要](../analysis/product-v030-family-and-rule-summary.json)。

### 控制与发现

N0-A / N0-B 各 30/30 健康事务并返回 `NO_INCIDENT`。
C1 = `CORE_KNOWN / CONFIGURATION_ERROR / payment`；
fraud-detection 运行健康、queue lag = 0，队列异常谓词为
`ABSENT_WITH_COMPLETE_COVERAGE`。C1 无关 Logs / Traces 缺口仍保留。

P1 / P2 / P3 = `OPEN_WORLD / CONCURRENCY / fraud-detection`。
三个不同窗口形成一个故障族，根一致性 1.0，
两两相似度约 0.8061 / 0.8300 / 0.9732；N0 / C1 被排除。

### 从现象到规则

三个正例、三个实测负例矩阵上，Runtime 选中：

```text
core:RUNTIME_HEALTHY AND ga:METRIC_QUEUE_LAG_OUTLIER
```

目标运行健康与队列积压同时成立，来自 Runtime / Metrics 两类来源。
这反映“进程仍运行但消费跟不上”；显示标签不能替代可检查谓词。

### Shadow 与晋升

13 个实际评估用例：3 正例、1 混淆 Core、2 健康、
2 不足/冲突、3 目标反事实、2 来源失败。
另 1 个 `OTHER_EXTENSION` 占位为 `NOT_AVAILABLE`，不计入分母。

positive recall = 3/3 = 1.0，FPR = 0/10 = 0.0，
Core overlap = 0/1 = 0.0，No-Incident false positives = 0/2。
引用有效率 1.0，来源失败安全检查通过；
仅一个 ACTIVE 扩展 `kafka-queue-backlog`，registry version 1。

这些不是 13 次独立 live 实验：包含从既有证据构造的反事实与失败用例，
小样本结果只证明本次门控通过，不能估计普遍准确率。

### H1 的意义

晋升后新采集的留出复发窗口 H1 =
`EXTENSION_KNOWN / kafka-queue-backlog / fraud-detection`。
根服务与故障族多数根一致，引用可解析，无新 provisional report 或目标故障族。
它证明规则持久化后真正进入诊断匹配路径，不是只生成“已学会”的文字；
不证明跨机制或跨环境泛化。

## 人工与模型的边界

v0.3 的 ACCEPT_AS_NEW 与 Promotion 使用用户明确的事先授权；
记录不代表用户在运行中重新人工检查了产出。
确定性 Demo 使用 `SIMULATED HUMAN REVIEW`，两者不可混淆。

LLM 仅建议标签与解释，不选择晋升关键谓词、不审批、不执行动作。
本次 live Provider / Agent write / Runbook = 0，
action/remediation authority = `NONE`，cleanup = `CLEAN`。
其他扩展干扰、独立环境及长期健康控制仍需验证，见[限制](LIMITATIONS.md)。

# 项目演进与历史证据索引

当前入口是[README](../../README.md)与[Product 状态](../product/STATUS.md)。
本页保留研究路线及失败教训，不把历史阻塞当作当前 Product 状态，
也不因后续成功改写旧结果。

## 1 · 从 Multi-Agent 探索到证据驱动诊断

起点是单 Agent、固定专家与动态协作的比较。
复杂协作有局部收益，但冻结主指标未支持预注册优势；
后来外部数据上的归因分析也提示，工具路径与证据质量比角色数量更关键。

- [冻结 Multi-Agent 分析](../results/phase5b-v2-final-summary.md)：
  hidden-only 主比较差值 +10pp，但区间跨零；保留未支持优势的结论。
- [RCAEval 归因分析](../results/rcaeval-re2-v1-attribution-summary.md)：
  后验分析边界，不是新预注册模型优势。
- [动态取证研究](../results/dta-v22-1-evidence-acquisition-study.md)：
  进一步将问题拆为来源选择、缺口发现、支持谓词和运行时路由。
- [缺口路由研究](../results/dta-v22-2-gap-routing-evaluation.md)：
  保留实测标记 `DTA_V22_2_GAP_ROUTING_QUALITY_EFFECT_OBSERVED`；
  该研究的适用范围与方法限制仍以原始结果为准。
- [真实故障取证对比](../results/dta-v226-real-fault-comparison.md)：
  模型自由取证 exact 0/4，当前 Runtime 4/4；
  只涉及两种物理状态的四个不透明呈现，不外推为历史模型或通用故障优势。

**教训：复杂 Multi-Agent 收益不稳定，模型主导检索也可能弱于确定性路由。**
因此当前 Product 的权威状态、取证与准入由 Runtime 维护。

## 2 · 从研究代码到可部署只读 Product

FastAPI、Worker、SQLite WAL、CAS、真实连接器与 Baseline
把一次性研究链路变成可重启、有版本绑定的 Product。
接入过程中保留了窗口不足、事务失败、诊断序列化和覆盖度问题。

- [最初 live pilot 阻塞](../results/product-v02-live-knowledge-loop.md)：
  Baseline 不足，不能冒充完整知识闭环。
- [事务成功但诊断失败的教训](../results/product-v02321-interview-brief.md)：
  30/30 业务流量不能替代诊断落盘成功；推动可恢复执行检查点。
- [诊断根因取证边界](../analysis/product-v02323-diagnosis-root-cause.md)：
  原失败缺少完整输入，仍是 `ORIGINAL_ROOT_CAUSE_UNPROVEN`。
- [后续 No-Fault 验收](../results/product-v0233-nofault-acceptance.md)：
  保留能力受限结果，不用基础设施恢复替代能力通过。
- [健康误报审计](../results/product-v024-false-positive-audit.json)与
  [修复后健康验收](../results/product-v024-nofault-acceptance-final.json)：
  后者 30/30、NO_INCIDENT、FULLY_SUPPORTED，不删除前者。

**教训：健康遥测噪声也会触发 Open-World；缺失覆盖不是负证据。**
可恢复检查点保护的是执行与证据链，不能凭恢复成功推定原错误根因已查清。

## 3 · 从 Open-World 报告到人引导知识演化

未知强异常先形成报告，不立即扩张冻结类型库。
相关研究逐步约束引用、根服务、领域投影与注册语义，
再进入环境内故障族、规则挖掘与晋升。

- [Open-World 研究入口](../results/dta-v23-open-world-evaluation.md)：
  已知准入之外的受限新颖性报告；其成功与失败边界保留。
- [注册辅助错误分析](../results/dta-v2341-registration-assistance-error-analysis.md)：
  14/14 格式有效，但隐藏机制身份 0/10、行为等价 4/10，
  仍未观察到目标注册辅助能力。
- [当前知识演化说明](../product/KNOWLEDGE_EVOLUTION.md)：
  多事件谓词矩阵、确定性规则挖掘、Shadow 和人工门控。
- [最终 live 知识循环](../results/product-v030-live-knowledge-evolution.json)：
  最终 `live_005` 完成控制、三个未知窗口、故障族、规则、晋升与 H1；
  先前循环与 H1 根一致性失败留在同一历史结果中。
- [故障族和规则证据](../analysis/product-v030-family-and-rule-summary.json)及
  [PR #88 最终完成记录](https://github.com/Raidriar7170/EcomSRE-Agent/pull/88#issuecomment-5529165572)：
  分别负责观测事实和最终集成状态。

**教训：格式正确不等于注册语义正确。**
Runtime 决定晋升关键谓词与匹配，人类负责治理；
当前证明一个本地 Kafka 队列积压机制闭环，不是自主发明通用故障知识。

## 如何继续查证

[docs/results](../results)保留结果，
[docs/analysis](../analysis)保留分析，
[Decision Records](../DECISIONS.md)保留设计与授权边界。
不要把旧 `BLOCKED`、`INVALID`、`REVIEW_REQUIRED` 或前置快照字段擦掉，
也不要将后续成功倒灌为早期实验成功。

完整历史 README 仍可由 Git 历史访问；本页只做索引，不复制 SHA 墙与阶段流水账。

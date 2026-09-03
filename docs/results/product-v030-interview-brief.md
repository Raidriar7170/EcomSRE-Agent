# Product v0.3 面试简报 — 控制集通过，H1 根一致性未通过

当前状态：ECOMSRE_PRODUCT_V030_BLOCKED_H1_ROOT_CONSISTENCY / REVIEW_REQUIRED。
不能宣称完整知识演化 Goal 已完成或已合并。

两项窄修复已实测生效：Product 不再让孤立十秒内存增长独立成为强 Open-World
残余，Bridge 与 Knowledge 重建使用同一策略；原始 Resources、数值阈值和
冻结 Core 内存泄漏规则不变。C1 改为按 payment/checkout/fraud-detection
及队列阴性相关来源验收，不再要求无关 Logs/Traces 全局完整。

live-004 使用唯一新 full-mode runtime 和一个新五窗口 Baseline。
N0-A/N0-B 各 30/30 健康交易，均为 NO_INCIDENT；C1 十次预期失败被识别为
CORE_KNOWN / CONFIGURATION_ERROR / payment。C1 lag=0<20，fraud Runtime
健康，队列候选子句明确为 false，队列阴性结论 CONCLUSIVE；真实 Logs/Traces
覆盖缺口仍保留。原 live-002/live-003 失败证据及旧 Baseline 未改写。

P1/P2/P3 均为 OPEN_WORLD / CONCURRENCY，强队列异常位于 fraud-detection。
三例形成一个三窗口家族，相似度 0.8990/0.8945/0.9807，N0/C1 均被排除。
按用户此前明确预授权记录一次 ACCEPT_AS_NEW 和一次 Promotion，不冒称
用户刚刚人工逐项审阅。Runtime 自选 queue-lag + Runtime healthy 两来源规则；
三正三负上的召回为 1，误报/Core 重叠为 0。严格 Shadow 指标全过，
反事实及来源失败检查通过；首个扩展无可用 OTHER_EXTENSION 对照。
Registry version 1 为 ACTIVE，action/remediation authority 均为 NONE。

H1 是新实测：3/3 事务成功，识别为 EXTENSION_KNOWN / kafka-queue-backlog，
根为 fraud-detection，支持引用可解析，没有 Open-World 报告或新家族。
但三条历史 Open-World 报告的多数根是 checkout，故 H1 完整门禁失败。
现有 Bridge 按排序后首个残余异常选根；扩展规则按实际队列谓词的 TARGET
绑定服务，两者语义不一致。P1 错误指标窗口与 C1 重叠，但不能据此断言
所有错误信号都来自 C1。没有手改旧根、降低 H1 门禁或重跑到通过。

可讲的成果是“实测完成未知故障聚类、Runtime 规则挖掘、Shadow、
只读扩展注册，并在新复现中命中机制”；必须同时说明“历史发现根与扩展根
一致性尚未解决，完整 Goal 未完成”，不能简化为全链路验收成功。

212 项聚焦回归、Ruff/mypy 和独立源码评审通过。旧绿色 CI 保留，但不证明
新工作树已通过全仓验证；本次没有新跑全仓或触发 CI，唯一新全仓验证仍留到
真正准备合并时。PR #88 保持 Draft，修复和最新结果留在既有本地分支。
所有四次所属 runtime 均 CLEAN，非所属资源未变，证据和私有 DB 保留；
Provider、Agent 写入、Runbook 均为零。ACTIVE 注册记录未删除或撤销。

完整数据见[结果](product-v030-live-knowledge-evolution.json)与
[家族/规则摘要](../analysis/product-v030-family-and-rule-summary.json)。

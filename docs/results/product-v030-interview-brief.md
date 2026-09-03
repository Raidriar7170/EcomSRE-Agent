# Product v0.3 面试简报 — 控制集阻断，闭环未完成

当前事实是 ECOMSRE_PRODUCT_V030_BLOCKED_CONTROL_SET / REVIEW_REQUIRED。

真实 full-mode 队列预检观察到 lag 302，完成三个独立高 lag 采样点后恢复归零。
Kafka 方法 Trace 和真实 Produce p95 已接入，完整采集无控制令牌泄漏；
方法耗时不代表 ACK 成功，append 计数也不与请求延迟严格同口径。
Core 没有预埋 Kafka 积压机制，只新增通用 QUEUE_LAG 症状。

最新 live-003 Baseline 完成 5/5 窗口、30/30 健康交易，四服务 Resources 统计齐全。
同一环境、同一 Baseline ID/SHA 的完整控制集仍未过关：

- N0-A：30/30 交易成功，因 Kafka 十秒内存增长被判 OPEN_WORLD，失败。
- N0-B：30/30 交易成功，因 checkout 内存增长被判 OPEN_WORLD，失败。
- C1：10/10 请求按预期失败、真实 ChangeEvent 存在，已正确识别为
  CORE_KNOWN / CONFIGURATION_ERROR；但 Logs/Traces 存在真实覆盖缺口，完整门禁仍失败。

C1 的低流量分母缺陷已修复，并把仍限定十笔请求的观测对齐五分钟窗口。
最新 Product 错误指标约 0.3474，仍不等于交易失败比例。另修复了 Baseline
毫秒/微秒时间精度导致 Resources 统计丢失的问题；没有改 Core 或队列阈值。
两条 N0 的原始值、Memory 与异常重建一致，尚未发现可解释误报的采样或单位错误。
内存短窗口规则对健康波动敏感；修改持续性或门限将是策略变化，不能假装是计算修复。

新的 N0 限制为空，C1 的限制为 SOURCE_LOGS_COVERAGE_GAP 和
SOURCE_TRACES_COVERAGE_GAP；三例泄漏列表为空，支持引用可解析。
新环境没有产生故障族。旧 live-002 三条正式结果（N0-B 通过，N0-A/C1 失败）、
其单成员非目标故障族，以及所有失败准备记录均保留，未选择性复用旧的通过结果。

已修复并测试知识层的负例完整性：缺数据不是无异常，Core 报告没有展示队列
症状也不代表症状不存在；Shadow 的可达性与完整性分开校验。最新修复聚焦
193 个测试通过，锁定版本的全仓 Ruff 和 CI 范围 mypy（667 个源文件）通过；
CI 全仓 pytest 尚未确认结束。三次正例、目标聚类、人审、规则挖掘、Shadow、Promotion
和 H1 均未运行，不能在简历或面试中宣称已完成知识演化闭环。

三个所属运行环境均已清理，最终所属资源为零，非所属资源未改；失败证据和私有数据库保留。
Provider、Agent 写入、Runbook 均为零，Product action/remediation authority
保持 NONE。PR 保持 Draft，未合并；下一步仍是资源误报语义与 C1 来源覆盖，
不是降低门禁或重跑到通过。授权已经充足，当前阻断不是权限问题。

完整状态见[结果](product-v030-live-knowledge-evolution.json)。

# 固定规则独立验证：采集协议不兼容，批次已停止

同一 [Draft PR #104](https://github.com/Raidriar7170/EcomSRE-Agent/pull/104)，执行版本 `b213227278abfff0a0ed6202199e8f8fd7e3bb33`。[冻结协议](fixed-validation-20260927-plan.md)、[机器结果](fixed-validation-20260927-result.json)。终态仍为 **ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE**：真实模型规则已经通过原开发，但本次独立验证未完成，Level A 完整闭环未通过，Level B 未恢复。

## 本次结果与历史分开

| 层次 | 本次事实 |
|---|---|
| 旧失败 | 旧 N5 健康资格未确立、旧 N7 正常复用失败，所有原件及旧机械 PASS/晋升/撤销保留 |
| 离线准备 | 当前 STATUS successor SHA 修复；旧摘要和失败 CI 36298670070 保留；未禁用检查 |
| 执行版本 | [Agent mainline 36305736150](https://github.com/Raidriar7170/EcomSRE-Agent/actions/runs/36305736150) 成功，6813 passed / 21 skipped；Ruff、mypy 746 源文件通过；另 verify CI 成功 |
| 环境 | 一次新只读连续性检查通过；实际 owned 部署与 successor 绑定通过，基线准备完成 |
| 新目标 holdout | 一次启动，正常 API/Worker 为 OPEN_WORLD；随后原始留存阶段失败，未生成完整采集或独立资格收据 |
| 新健康/Core holdout | 未启动，资格 NOT_RUN；没有把目标事件恢复开关当成健康资格 |
| 新 Shadow / 晋升 | NOT_RUN；新注册 DRAFT，没有 freeze/evaluation 或 ACTIVE 记录 |
| 新正常复发 / 撤销 | NOT_RUN；新注册从未 ACTIVE，无需撤销；旧注册仍 REVOKED |
| 清理 | owned 22 容器、1 网络、5 卷已移除，剩余 0；after inventory 与 admitted inventory 完全相同，非项目资源未变 |

## 具体停止原因

09:10:10 UTC，冻结执行器报 `FIXED_SCRAPE_RECENCY_FAILED`。私有 CAS 的 `/api/v1/targets` 原始响应（摘要 `219a12dabe03d6ce2c90482b7c6c29846f86a7f7c163ae7c5f90b9460214b3b2`）为 `activeTargets=[]`。保留的实际 Collector 配置将 metrics 经 `otlp_http/prometheus` 发送到 `/api/v1/otlp`；Prometheus 接收 OTLP push，没有可供这条检查使用的 scrape target。

**本次新增检查错误地把“至少一个健康且近期 scrape target”设为所有 Metrics 路径的必要条件，未在启动前识别实际 OTLP 摄入路径。** 这是本次验收协议的工程缺陷，不能归为固定规则失败，也不能把空 targets 当作遥测已证实陈旧。此前 fixture 与独立静态复核没有覆盖这一真实拓扑差异；全部测试通过并不证明该假设成立。

保留冻结协议、原失败与停止记录，不事后调整条件让本批次通过，不重复 N4、不启动 N5–N7、不将剩余名额变成补采。正常观测仍是 OBSERVED/OPEN_WORLD；批次终态是 STOPPED_COLLECTION_PROTOCOL_INCOMPATIBILITY，两者不可混同。

## 时间与原始留存

| UTC | 已记录事实 |
|---|---|
| 08:56:02.065235 | 固定基线准备完成 |
| 09:07:02.065235 | 预先计算的最早合法观察时点 |
| 09:07:23.595707 | 唯一目标事件启动 |
| 09:07:26.513778 | Queue 实际激活与读回 |
| 09:09:39.272629 | 正常 OPEN_WORLD 诊断 |
| 09:09:46.504010 | 恢复配置读回 |
| 09:10:10.054924 | 采集协议失败，追加停止记录并执行 owned 清理 |

冻结回溯为 Metrics 外层300秒 + 内层300秒，Changes3600秒，Logs/Traces300秒及固定60秒余量；未缩短查询窗口或更改阈值。上次真实 Changes 恢复时间04:02:16.903446 UTC，已超出本次回溯。执行器使用的30秒抓取余量并非实际 push 摄入延迟测量；失败后不能把它写成已验证的完整隔离保证。

67条真实 HTTP 响应均200、无截断，31条附正常事件 action context，另有35条底层 selector 查询与1条 targets 响应。PromQL、start/end/step、原始 series 标签/时间戳/值、Logs/Traces 请求与响应留在私有 CAS；所有响应摘要回读一致。恢复、配置、实际部署、准备和失败/清理等89个JSON/JSONL原件按原字节追加归档，索引摘要 `6a3a262b20a64b643336cce8e61aa3a0136837aee72d0ab221b95c57c753040d`，明确标为 FAILED_BATCH_ARCHIVE_NOT_GOVERNANCE_COLLECTION。没有事后补造 collection-complete 或资格收据。实际样本摄入时刻仍不可直接取得；不公开原始敏感遥测或凭据。

## 固定身份与账本

原注册 `registration-ae3902fb32fd96d87608cb93` 保持 REVOKED。新测试版本 `registration-validation-688460a30572ae84c676debb` 仅更改注册身份及相应编译摘要，proposal、模型来源请求和规则语义一致；继承原开发事实，非新模型生成。原选择锁、freeze、开发和 Shadow 历史摘要只读核对一致。

| 口径 | Provider 请求 | 承诺费用 USD | live |
|---|---:|---:|---:|
| 本次批次 | 0 | 0 | 1/4 |
| 原最终闭环累计 | 3 | 0.148988 | 9/12 |
| 项目累计 | 60 | 1.067489 | 14/17 |

候选生成/语义修订新增0；旧语义尝试3/6保留且不恢复选优。原4次未知usage预留未删除；invoice actual仍未知。剩余3个名额不是本次失败后的自动重试授权。Product恢复写入0，无 merge/tag/release/生产部署。

## 新独立验收前所需最小调整

先在离线协议中识别实际 Metrics 摄入类型，给 OTLP push 和 scrape 各自定义可验证的新鲜度依据，不能简单删掉检查或接受空 targets。对 push 路径需固定 SDK/receiver 采集间隔、Collector flush/batch/export 行为、可观察接收时间及允许摄入延迟；无法证明时应在启动前失败。仅 query_range 的评估网格时间戳不足以证明底层样本摄入时刻，还需保留实际样本时间或 `timestamp(selector)`、Collector 导出/接收凭证等支持材料。

增加与 retained `collector.json` 一致的无 scrape target / OTLP push fixture，验证协议在启动前可用；仍保持原 Core 窗口、阈值、来源要求和 Core/Extension 顺序。重新固定准备上限、四角色与停止条件，再开始任何新的独立批次。此次停止后没有权自动重启；任何新 live 需要明确的替代批次授权及预算范围，而非复用本次已停止批次。固定模型规则无须重新提议或选优。

已见数据的实际区分仍来自 queue lag；Runtime 健康未展示额外区分增益。本次没有产生新的学习机制或普遍准确率结论。独立终态复核未发现原件保留 Must Fix，但确认上述协议不兼容使独立验收不能成立。

# OTLP v2 替代批次：真实准备覆盖失败，未进入 holdout

执行版本 `0a6733a7ca08cdf818c87850d8eae23e77a084f3`，同一 Draft PR #104。[冻结授权](otlp-validation-20260927-plan.md)、[安全结果投影](otlp-validation-20260927-result.json)。终态仍 `ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE`；Level A 闭环未完成，Level B 未恢复。本次没有重新学习或修改固定规则。

## 实际流程与停止边界

本轮只读资源连续性通过：当时容器0，daemon/context/网络状态与原基线一致。新 owned 部署一次成功，直接比较原兼容链及上一批实际部署；正常基线准备成功。新批次 `fixed-rule-otlp-validation-20260927` 安装明确 OTLP v2 计划，固定目标 fraud-detection 和21个必要查询。旧批次三个未使用额度被退役，其 stop 未删除；新授权累计上限18、闭环13，没有旧新额度叠加。

固定隔离的最早时点为2026-09-27 20:41:28.683084 UTC。唯一一次准备于20:41:34执行，21个正常查询加33个原始 range-vector 查询，共54/80次，首请求至末响应0.138399秒，全部HTTP 200且无截断，符合120秒期限；该时长是请求/响应跨度，**不是样本摄入延迟**。此前128条启动/基线读取另列为准备材料，不是holdout，也未创建诊断事件。

准备结果：9个查询 `SUPPORTED`，3个 `SUPPORTED_WITH_EXPLICIT_EMPTY_ALTERNATIVES`，9个 `INVALID_SAMPLE_EVIDENCE`。固定校验返回 `INGESTION_PREPARATION_INCOMPLETE_NO_RETRY`，在N4预留与故障激活之前停止。没有补采、调整阈值/窗口、重试或候选匹配。

## 原始样本说明了什么

| 观测 | 保留原始样本结果 | 判定边界 |
|---|---|---|
| 四服务CPU与内存、fraud-detection queue | 原始样本新鲜且覆盖完整，9个查询SUPPORTED | 不能据此宣称业务健康 |
| checkout错误率、时延、请求支持 | span分支完整，明确保留其他分支EMPTY，3个查询通过 | 合法空分支没有填成真实零样本 |
| Kafka错误率、时延、请求支持 | 原始样本最大间隔约60.002秒，末样本年龄25.172秒，首样本距支持窗口起点34.829秒 | 新鲜度未超30秒，但间隔及左边界不满足冻结覆盖要求；观测到的采样间距不等于已证明SDK配置或精确摄入延迟 |
| fraud-detection三项业务查询 | calls中1条、histogram中17条序列的首样本晚于支持起点约110.736秒；最大相邻间隔约5.008秒，末样本约4.267秒 | 数据新鲜，但这些返回序列缺少支持窗口前段 |
| payment三项业务查询 | calls中1条、histogram中17条序列的首样本晚于支持起点约120.736秒；最大相邻间隔约5.008秒，末样本约4.267秒 | 同样为覆盖缺口，不能改写成完整健康控制 |

这次读取确实沿 `OTLP_PUSH` 路径取得原始样本，不再依赖 scrape targets。接收/导出配置摘要与冻结计划一致。已知配置为 docker_stats 2s、kafkametrics 10s、span_metrics flush5s；Kafka/JMX有效导出配置仍未从保留配置中证明，原始约60秒间距是这次新增的真实观测。精确逐样本摄入延迟继续 UNKNOWN。部分series为什么在窗口中途首次出现，当前审计没有建立因果归属；不能宣称再等一会必然通过，也不调整协议重试。

## 分层结果、状态与消耗

- v2真实准备：FAIL_WINDOW_COVERAGE；业务资格尚未评估。
- 目标/健康/Core holdout资格：全部NOT_RUN；未创建任何新事件。
- Shadow、晋升、正常API/Worker复用：全部NOT_RUN。没有 learned binding 成功声明。
- 新测试版本 `registration-validation-c68111da1a8f0706516046d1` 始终DRAFT，未ACTIVE，撤销不适用。原注册REVOKED，上一测试版本DRAFT；旧新stop均保留。
- 本轮1次成功owned部署、1次准备、0次故障、0个live事件槽、0 Provider/语义尝试、0新增费用。项目14/18 live；闭环9/13；累计60请求、USD1.067489承诺。成功部署后准备失败不计事件槽的规则在采集前已冻结；本批次仍永久停止，未用槽不能自动重试。
- 清理22容器、1网络、5卷，owned剩余0/0/0，非项目资源和网络额外字段未变。清理不是Product自主恢复，Product恢复写入为0。

保留私有71文件归档及134个CAS对象引用，索引SHA-256 `7110998db0f73bae217ee1ed6989b5754717a548cc8364da57402caa2e822e29`。原08归档89文件字节未变；停止前驱的批次、注册、部署、收据快照及Provider账本只读复核一致。原始遥测留在私有CAS，公开结果只含安全摘要，不改旧事件角色或盲测分母。

## 工程检查与后续边界

31项聚焦回归通过，覆盖新旧批次寻址、旧stop拒绝、额度替代、前驱篡改拒绝、08→09直接部署比较、拒绝09借用08部署、v2资格→freeze→评估→晋升→撤销fixture。Ruff和Product mypy173文件、历史successor verifier通过。精确执行提交的 [Agent mainline 36347763006](https://github.com/Raidriar7170/EcomSRE-Agent/actions/runs/36347763006) 与 [RCAEval 36347763002](https://github.com/Raidriar7170/EcomSRE-Agent/actions/runs/36347763002) 均SUCCESS；全库6835 passed、21 skipped，Ruff通过、mypy746文件通过。文档交付提交与执行提交分别报告。

这次失败要求先解释并解决固定数据覆盖契约与实际样本序列之间的冲突。可继续只读分析保留数据；任何协议变更、部署或新采集都不由本批次失败自动授权。不能降低覆盖/Shadow门槛、缩短窗口、换候选、清除历史指标或交换Core/Extension顺序。既有已见数据的区分来自queue lag，Runtime健康未展示额外区分增益；本轮没有新机制或普遍准确率结论。

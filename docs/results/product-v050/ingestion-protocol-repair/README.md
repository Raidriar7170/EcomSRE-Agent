# OTLP 摄入协议离线修复（未执行真实准备）

同一 Draft PR #104；基线 `7a46643` 的两个 CI 均已通过。本次只实现、fixture 测试和保留数据只读诊断，没有 Provider、Docker 命令、新 live、候选变更、追加预算或恢复停止批次。新协议显式为 `ingestion-sample-evidence-v2`，不升级已封存 v1 记录。[只读复现与账本](retained-audit.json)，[旧批次失败](../final-learning-closure/fixed-validation-20260927-result.md)。

## 实际路径与旧失败复现

保留的 08 `collector.json`、实际 Compose Prometheus command、部署 actual_queries 和原始 targets CAS 确認：Prometheus 启用 `--web.enable-otlp-receiver`，Collector 统一通过 `otlp_http/prometheus → http://prometheus:9090/api/v1/otlp` 推送。`activeTargets=[]` 与此路径兼容，不证明目标数据陈旧。

| 诊断所需指标/分支 | 实际接入及处理 | 已知采集间隔与边界 |
|---|---|---|
| `traces_span_metrics_calls_total`、duration bucket | 服务 OTLP traces → `otlp` receiver → traces `memory_limiter` → `span_metrics` connector → metrics `memory_limiter` → OTLP HTTP exporter | connector flush 5s；服务 span 产生/到达时间不是精确摄入时间 |
| `kafka_request_count_total`、failed total、Produce p95 的 broker 分支 | Kafka Java/JMX agent → `otlp` metrics receiver → `memory_limiter` → OTLP HTTP exporter | retained Kafka 环境指向 Collector 4318；挂载 `config/product-v030/kafka-jmx.yml` 明确 Produce p95 映射。有效 SDK/JMX 导出周期未在保留环境显式给出；其他服务不存在该分支可由 span 分支支持，不能假设每个 service 都有 broker 指标 |
| `kafka_consumer_group_lag_ratio` | `kafkametrics` receiver（brokers/topics/consumers）→ `memory_limiter` → OTLP HTTP exporter | receiver collection 10s；按 group/topic 绑定目标服务 |
| CPU counter、memory gauge | `docker_stats` receiver → `memory_limiter` → OTLP HTTP exporter | receiver collection 2s；按冻结 container_name 绑定服务 |

保留材料的67条响应及23个实际指标查询窗口仍在私有 CAS。旧 v1 校验稳定拒绝 `scrape_recency_passed=false`；新 v2 对同一材料报告**缺少原始样本查询**。旧批次只保存 `query_range` selector 网格，没有 v2 的 instant range-vector 响应；不能离线宣布其样本新鲜，也不事后生成合格 holdout。

## 修复前后与证据语义

| 项目 | v1 保留行为 | 未来显式 v2 |
|---|---|---|
| 摄入模式 | 要求非空且近期健康的 scrape target | 绑定实际 Collector/Prometheus command/查询 map 的摘要和 OTLP_PUSH；配置或模式不一致拒绝；不添加无关 scrape target |
| 目标样本 | `query_range` 网格不能证明原始样本时间 | `/api/v1/query?query=<selector>[range]&time=<评估终点>`，读取 range-vector 原始 timestamp/value/labels；不能拿 query_range/HTTP 时间替代 |
| 新鲜度 | 全局 target 最近抓取 | 每个实际 service/selector 的原始末样本相对诊断窗口终点 ≤30s；使用原集合检查的30秒约束，不改业务阈值 |
| 窗口覆盖 | 未由原始样本证明 | 覆盖实际 outer window 加 inner rate 回溯；首样本距支持起点≤30s，内部间隙≤30s，至少两点，顺序唯一、值有限、不越界。仅支持返回的服务/selector series，不宣称知道未返回的全体 series 基数 |
| 稀疏事件/备选分支 | 容易混成缺失 | 原始 EMPTY 保留；错误子集只有同来源 total 完整时可视为合法无记录；OR 的完整备选来源可支持查询。两条链均无支持则 UNKNOWN；已有陈旧/错服务样本不可由另一分支掩盖；不填零 |
| 精确摄入延迟 | 固定等待容易被误写为测量 | `UNKNOWN_NOT_EXPOSED` 可明确保留；不要求不存在的逐样本摄入字段，不把等待或响应时间当摄入延迟 |
| Collector 辅助状态 | targets 被当作必要证明 | 未证明当前版本可用的接收/导出/队列统计不查询、不假造；不能替代业务指标样本 |
| 业务控制资格 | 独立检查 | 原健康/Core/目标资格函数不变；样本证明先过，再检查正常诊断及来源；采集通过不表示健康或规则命中 |

新 receipt 保存原始样本 CAS、配置 CAS、事件身份和重算 assessment。直接资格、freeze/evaluate、promotion 均重新读取 CAS、核对固定请求和正常事件窗口，再计算证明；不会信任一个可写的 `passed=true`。v1/v2 收据字段按封存协议显式选择，旧凭证保留可读。旧停止记录和注册状态不受影响。

## 下一批准备顺序与硬边界

未来只有**新授权且冻结 v2 的部署/批次**可使用以下路径。本次没有安装此契约，也没有执行真实准备；原 08 批次仍停止，原 BATCH 唯一性与账本不重置，不能调用旧入口绕过新授权。

1. 使用实际 owned 部署配置识别接入模式并绑定查询目录；准备查询必须覆盖全部 Core/Resources 查询和固定目标 queue，不允许只检查无关服务。LLM 来源测试版本绑定同一固定规则 target。
2. 正常基线准备后，先完成已有固定 earliest 隔离等待，保证600秒有效回溯有机会形成；然后在任何事件预留、故障激活之前执行一次只读原始准备。没有诊断事件、候选调用或选优。
3. 准备只有1次、全部 HTTP 请求总上限80、120秒接收期限；单次 HTTP 超时10秒。跨期限的请求不接受为成功凭证，不再派发下一请求，不重试。原业务 PromQL query_range 和新增底层 range-vector 均计数。封存 attempt UTC/批次摘要；窗口终点必须等于该 attempt，逐请求时间顺序和批次时段均重验，拒绝历史准备回放。
4. 准备的300秒外窗、实际 inner rate、返回样本覆盖和 query 可执行性必须通过。保留请求/响应、截断、不可用原因；过期/缺失/错误服务/不支持格式/warnings 均不能变成健康。失败停止，不调整间隔、阈值或等待至命中。
5. 准备通过才允许事件预留与故障；正式事件仍按原正常诊断窗口独立重采并重验。之后仍需既有控制资格和完整 Shadow，不因准备或零 Provider 调用宣称验收。

尚缺**未来部署、实际 instant range-vector 响应及准备证明**。特别是未显式保留的 SDK/JMX 有效导出周期可能不能满足固定30秒样本覆盖；必须由有界准备如实发现，不能暗中放宽。读取 Collector 的辅助运行统计仅在固定版本可用时另行保留，当前没有该证明。精确逐样本摄入延迟仍未知，不作为必须伪造的字段。

## 验证与当前结论

聚焦28项覆盖 push无target但新鲜、过期/缺失/错服务、无关健康target、网格新而样本旧、配置不一致、稀疏合法无记录/UNKNOWN、真实 raw capture、单次准备和拒绝旧窗口回放；fixture 贯通资格→freeze→评估→promotion→撤销，损坏CAS后资格/freeze/直接promotion均拒绝。v1空v2字段兼容亦回归。此前本轮v0.5套件288项通过；最后边界补强后再以精确提交 CI 检查为准。Ruff、Product mypy173源文件通过。两轮独立只读复核的Must Fix均关闭，仅支持离线实现结论。

旧89个归档原件摘要相同；原数据库 SHA-256 只读前后一致 `e0dc73fdd723a05908b17d740bcf8690d7f25f829b67f34f698420d852df24b3`。累计60 Provider请求 / USD1.067489承诺 / 14 of17 live，闭环9 of12；新Provider/费用/live/语义尝试全部0。旧注册REVOKED、新测试版本DRAFT、旧stop不变；不自动使用剩余三个槽位。Level A闭环仍未完成、Level B未恢复，整体 `ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE`。既有区分来自queue lag，Runtime健康未展示额外区分增益；本次不构成新机制发现。

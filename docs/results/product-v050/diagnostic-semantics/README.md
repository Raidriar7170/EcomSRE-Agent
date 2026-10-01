# live-03 离线诊断语义 A/B/C 对照

**阶段结果：可重放的语义分层与关联解释已实现；没有证明健康改善或唯一根因定位。** 两窗 A 的 26 项 connector 数值/支持计数、完整 memory、健康谓词、bridge diagnosis 和 decision trace 摘要精确重现。B 把控制流与业务/未分类 span 分开计算，C 每窗增加 2 条有实例和操作依据的控制流关联候选；新业务基线和 p95 不可精确重建，因此 B/C 新诊断 `NOT_COMPUTABLE`，原 `INSUFFICIENT_EVIDENCE / ABSTAIN` 不改写。不是 v0.5 正式验收，不恢复旧 Goal。

活动范围由 [Diagnostic Semantics Goal](../../../goals/EcomSRE_v0.5_Diagnostic_Semantics_Goal.md) 规定。数据性质全部为 `SEEN_ENGINEERING / POST_HOC_EXPLORATORY`。两个相邻窗口仅来自同一次部署，约 0.098 秒重叠，且五分钟 rate 支持高度重叠；不是两个独立事故家族。零故障注入和成功 checkout 不构成全系统健康标签。

## 可重复入口与结果

```sh
PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.product_v050.diagnostic_semantics_replay \
  --root .local/engineering-calibration/live-03 --rounds 2 3 \
  --spec config/product-v050/diagnostic-semantics-experiment-v1.json \
  --output .local/diagnostic-semantics/run-06
```

上述目录已执行；重放必须使用新的输出目录。入口拒绝覆盖及向输入根写结果，SQLite 仅 `mode=ro` + `query_only`，外部连接器仅使用严格匹配原查询的 `MockTransport` 保留响应。没有启动 Worker 或环境。普通开发输出 run-01 至 run-05 均保留，不作为最终结果。

- 完整结果：私有 `.local/diagnostic-semantics/run-06/comparison.json`，含逐窗口/逐时点/逐操作数值、原谓词和决策、原始/新增 span 数、捕获范围、关联及输入摘要。
- [公开逐窗结果](comparison.json)：同一次 CLI 的 `public-comparison.json` 原样副本；只缩减为逐操作时点值域和控制流关联，**不是完整逐时点文件**。所有 20 个操作均列出，未删除不利信号。
- [实验映射](../../../../config/product-v050/diagnostic-semantics-experiment-v1.json) 在比较前保存。仅实验入口加载，Product 默认配置/查询/阈值/规则不加载它。

源文件摘要绑定实际运行代码；输出没有易变 UTC 字段，语义和字节重复性分别经测试验证。新 UTC 捕获不存在；所有输入时钟均为原记录。旧实验版只是开发迭代：run-01 仅计精确增量区间；run-02/03 增加明确标注的共享支持候选；审查后 run-04/05/06 要求已知状态一致，不能由无状态 span 解释 ERROR counter。没有为了得到健康而改变用途映射。

## 输入与计算级别

| 输入 | 本机位置（相对 live-03） | 核查/限制 |
|---|---|---|
| 原基线 | `baseline-frozen.json`、`baseline/raw.jsonl` | 绑定 `base-4e251aeb4ae934c625432b23`；5×36 秒，01:59:34.912–02:02:34.912 UTC；02:05:34.912 构建，早于两检查窗口 |
| 原行动与 memory | `round-{2,3}/evidence.json`、`memory.json`、`diagnosis.json`、`evidence-index.json` | CAS 内容核验、原行动顺序（queue 最后）、原 baseline profile、原 bridge；`EXACT_REPLAY` |
| 正常指标响应 | `round-{2,3}/diagnosis-job/raw.jsonl` 与 `product/objects/sha256` | 原 Prometheus connector 归约；保留有限/NaN 点计数；`EXACT_REPLAY` |
| 标签与桶序列 | `round-{2,3}/samples/raw.jsonl` | 同时使用 calls 分母、ERROR 分子及对应 +Inf/15000 桶，按操作/方向一致分组；`DIAGNOSTIC_PROXY_ONLY` |
| 基线标签序列 | `baseline-support/raw.jsonl` | 有子集原始样本，但 241 秒读取不能覆盖所有基线评估点此前的 300 秒；实际计算保留样本 proxy，不伪造同语义 rate profile |
| 原 Trace | `round-{2,3}/diagnosis-job/raw.jsonl`、evidence 中已归一化 records | 原读取响应与实际进入 memory 的有界 records 数分开；无 EventStream 控制 span 进入原 memory |
| 扩展 Trace | `round-{2,3}/context/traces-*.{json,body}` | body SHA 与捕获/请求范围绑定；按 trace/span ID 联合去重和冲突检查；`POST_HOC_AUGMENTED` |
| Runtime、queue、资源、Changes | 原 evidence、原 memory | 全部保留；不把 gauge 下降当 counter reset；本实验不重新解释或删除资源异常 |

`EXACT_REPLAY` 仅属于 A；B/C 为 `DIAGNOSTIC_PROXY_ONLY`。分层 p95、新分层健康/谓词判定为 `NOT_COMPUTABLE`；没有需要声称 `LIMITED_NUMERIC_RECONSTRUCTION` 的 rate 重建。counter 变化比率与原查询有限时点均值不同名，不称用户订单失败率。

## 逐窗口结果

| 维度 | 第 2 轮 | 第 3 轮 |
|---|---|---|
| 检查时间 UTC | 02:11:37.587622–02:16:37.587622 | 02:16:37.489415–02:21:37.489415 |
| A 原判定 | INSUFFICIENT_EVIDENCE / ABSTAIN | INSUFFICIENT_EVIDENCE / ABSTAIN |
| 原根歧义 | OPEN_WORLD_ROOT_AMBIGUOUS，Core/Extension 均未命中 | 同左 |
| A memory SHA | `dc0afa675a6b995e1434ffc145d29b51103e05fb817138d4bb2ad2238bb80c24` | `47b6006d480c0852a5c4dac43a062dc413a230f26ff9568e452bad841bb4ca20` |
| A fraud/payment latency ms | 8528.4385 / 6502.1283 | 6344.9462 / 8226.7645 |
| A fraud/payment error ratio | 0.0472365 / 0.0377331 | 0.0364388 / 0.0519438 |
| A 健康谓词 | false / STRONG_ANOMALY_PRESENT | 同左 |
| B 控制流已观察错误占比 | fraud/payment 各 100%，仅分母>0的时点 | 同左 |
| B 控制流已观察 >15000ms 桶增量占比 | fraud/payment 各 100%，仅分母>0的时点 | 同左 |
| B 新业务 p95 / 健康判定 | NOT_COMPUTABLE / NOT_ISSUED | 同左 |
| 独立强内存增长 | fraud、kafka、payment | kafka |
| C 原响应唯一 span / 额外响应唯一 span | 201 / 512 | 240 / 747 |
| C 新增唯一 span（未必相关） | 315 | 512 |
| C 新增有实例/操作/状态依据的共享支持候选 | 2，均为控制流 | 2，同两条 span 的重叠支持，不能加成4次独立异常 |
| C 精确完成区间匹配 | 0 | 0 |
| C 原歧义减少 / 判定变化 | 未证明 / 不签发新判定 | 同左 |

原 latency/error 查询有部分 30/31 有限点，原 connector 丢弃 NaN 的行为保持原样并逐项计数；request support 等仍有 31 个点。没有以最后一点、有限点均值或业务子集零错误推断整窗健康。

## B：分组后的可计算差异

每窗 20 个 service/完整操作名/span kind 组合：7 个 `BUSINESS_REQUEST`、3 个 `BUSINESS_ASYNC`、2 个 `CONTROL_STREAM`、8 个 `UNCLASSIFIED`。相同操作成功/失败同组。业务请求 client/server 分列；orders receive/process/publish 分列，**不将它们相加为独立订单**。公开 JSON 列出所有未分类和控制流操作及计数范围，包括 `resolve`、flagd ResolveInt/ResolveFloat、DNS、TCP、内部 charge/checkout helper；保守未分类不表示有害或无害。

所有增量是每个评估点 `(t−300,t]` 内实际返回样本的首尾差，reset/NaN/缺分母/不齐网格拒绝计算；没有出生前零、边界外推或重建 PromQL。零错误只表示已返回标签的观察值，未证明标签人口完整。+Inf 与 15000 桶需要同实例/操作/状态/采样网格，且 +Inf 增量等于 calls 增量；不进行 p95 相减或组 p95 平均。[Prometheus 函数语义](https://prometheus.io/docs/prometheus/latest/querying/functions/)仅解释为什么此处不把 delta 当 rate；未用最新引擎行为代替固定版本结果。

| 窗口/服务 | 每评估点全部 span 增量范围 | 控制流增量范围 | 未分类增量占比范围（非零分母） |
|---|---:|---:|---:|
| 2 / checkout | 0–60 | 0 | 33.33% |
| 2 / fraud | 0–15 | 0–1 | 22.22–50% |
| 2 / payment | 0–13 | 0–1 | 50–66.67% |
| 3 / checkout | 48–72 | 0 | 33.33% |
| 3 / fraud | 10–15 | 0–1 | 0–26.67% |
| 3 / payment | 10–13 | 0–1 | 50–54.55% |

这些是**同一批 span 在重叠支持区间中的时点值域**，不能把31点相加成请求总数。基线按同一映射实际算出20个保留评估点的子集 proxy，仍为 `BASELINE_SCOPE_UNAVAILABLE_FOR_EXACT_RATE_PROFILE`：时间支持不足且短观察早于首次长 RPC 完成。不能借混合基线把新业务总体判成健康，不能用检查窗给自己建基线。Kafka 原非 span 指标、CPU/内存、Runtime、queue、Changes 均未被 RPC 分层替换。

## C：解释增益及不能推出的结论

同一 fraud/payment 实例分别有持续 **600.006735 / 600.002936 秒** 的 EventStream ERROR span。其开始早于原查询窗口，第二窗只匹配五分钟 rate 的支持区间；开始时间没有被改到窗口内。RPC 方法/服务/system 标签支持 flagd 流式控制语义。[OTel RPC 生命周期](https://opentelemetry.io/docs/specs/semconv/rpc/rpc-spans/)允许流式 span 覆盖流生命周期，但不据此给旧埋点补字段。

fraud 完成后 6.574065 秒、payment 完成后 9.363064 秒才出现首个 counter 跳变样本。span 完成点不在相邻采样增量区间中，因此只报告 `INSTANCE_OPERATION_SHARED_SUPPORT_NOT_CAUSAL`；不同实例、只有时间巧合、不同操作或状态不匹配均不形成此候选。没有同一完成区间的精确配对，没有唯一 RPC 因果证明，也没有重连计数或超时无害结论。

扩展响应在原诊断创建后捕获，两个控制流候选均为 `ONLINE_AVAILABILITY_UNPROVEN`。原起始时间查询缺席、晚捕获、达到返回上限/截断分别记录。原响应 ID 数不等于实际 memory records 数；后者独立保留在完整 A。没有凭零新网络读取声称在线效率提升。

C 使用有真实时间/实例/parent引用的解释旁路，不注入伪造 observation/evidence ref；B 本身没有可用的同语义 MetricFact/baseline，所以未让 C 在不同总体下硬跑健康判定器。原 bridge 仍为 ABSTAIN，但这不是“已经运行了新语义 bridge 也 ABSTAIN”。没有新增 `看见 EventStream 就选 flagd` 的规则。

支持：共享控制流超时可以解释被混合指标累计的已观察错误和长时延桶。冲突/独立问题：内存增长仍然成立；业务和内部未分类 span 总体不同。未知：控制流超时为何发生、是否影响业务、内存是否持续增长或泄漏、业务请求总体的稳定基线、完整 trace 负例。关联解释更具体，诊断质量提升尚未证明。

## 验证与边界

读取范围：直接相关 `src/scripts/tests/config/docs`、只读固定上游、live-03 保留响应/原库；写入范围：实验 CLI/映射/聚焦测试、本 Goal、AGENTS/STATUS 当前指针、工程汇总一条链接、本报告/公开 JSON、已有 STATUS successor 摘要。冻结范围：全部原 Product 源码/正式配置/规则/注册不改；live-03 输入、原库及旧工程/停止批次只读。最终仓库范围：此 worktree 相对 `b23dde129f311aa32366d6259bb3312215a72b9d` 的完整 tracked delta。私有 CAS、数据库及完整 Trace 不提交。

聚焦测试覆盖业务错误保留、分子分母一致、异步消费、未知操作/标签、NaN/reset/缺桶/零流量/网格错配、基线时序、跨窗结束/错误实例/状态/重复冲突/晚捕获/截断、独立资源异常和默认线上不加载实验。真实两窗全链在具备私有输入时运行；CI 无私有数据时明确 skip 该一项，合成反例继续执行。未 mock 掉被比较的分组、数值、关联函数。历史检查器名称含 Docker，但只验证历史文件，不调用 Docker。

本阶段新增项目 Provider、Docker、遥测采集、故障、业务请求、正式事件、注册/晋升均 **0**；GitHub push/CI/PR 摘要属于明确授权交付操作。稳定候选聚焦回归44项通过（其中本实验30项）；Ruff全库、Product mypy 173文件、六个历史验证器通过；只读独立审查的四项边界问题已修复，复审无必须修正项。完整回归、最终完整性及精确提交CI的原始日志/摘要保存在 `.local/diagnostic-semantics/verification`，交付消息引用最终提交与CI链接，不用旧提交成功替代。

## 明确下一步（仅建议，未执行）

**选择一次另行授权的、有明确业务范围和完整周期的工程基线观察。** 现有分层已把控制流贡献解释清楚，但精确业务基线不足，当前不应启动 LLM 调查或四事件正式验收。

最小需预先确定业务同步入口与异步消费各自分母/方向；保留完整标签 counter/累计桶与每个 rate 点此前300秒样本；固定基线需覆盖已观察约600秒流生命周期的完成及其完整支持尾段，再固定后续检查窗。保留同实例、真实采集截止、连续内存/CPU/Runtime/GC可用信息和跨窗 Trace 捕获时刻。具体周期与预算需下一任务明确，不从本报告自动授权。观察结束仍可异常/ABSTAIN，不以 NO_INCIDENT 为条件；旧5/5不跨语义继承，新定义需用户明确接受。

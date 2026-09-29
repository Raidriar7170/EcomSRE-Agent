# 语义视图选择与对比式故障调查

本轮为独立研究，不恢复 v0.5 正式验收。活动契约见 [Goal](../../goals/EcomSRE_Semantic_Investigation_Algorithm_Codex_Goal.md)。**三个工具与研究循环已实现；真实实验为 partial，效果 undetermined。** 完成1个已见事件的2窗四组开发比较及C/D重复，没有独立根因测试，不能宣称定位或策略增益。

## 1. 范围与代码

起始版本 `c7dce3720b2d877a10db30a940dd238c275b06e1`。三个只读工具、研究策略、独立 CLI 和账本已实现；旧调查入口及 Product 库不参与研究。旧计数器纯函数抽取共用，不改变历史计算含义。最终算法输入、配置与源码由逐运行私有intent中的SHA-256绑定，公共摘要见 [summary.json](summary.json)；交付源码版本见下文。93项相关检查通过。

read scope：本轮直接依赖的 investigation、连接器、旧回放与保留 live-03 输入；write scope：新增 semantic 模块、Provider 的新任务提示窄适配、研究 CLI/config/tests/report/Goal、旧回放纯函数导入和活动指针。frozen asset scope：live-03 清单列出的输入、旧 Product 库、历史结果和规则；final repository scope：此工作树相对起始版本的完整 tracked delta。私有新产物只写 `.local/semantic-investigation-v1`；全工作树共用付费账本保存在 Git common-dir 的同名命名空间。无 Docker/live/故障/知识晋升/远程发布。

## 2. 数据与划分

| 材料 | 独立组 | split | 支持 | 限制 |
|---|---:|---|---|---|
| live-03 第2、第3窗口 | 1 | development | 原始 Trace、操作/方向/状态、父关系、计数器与部分桶、原始数值残差 | 已见、相邻窗口；没有唯一根因标签；事后快照 |
| 独立根因测试集 | 0 | test | 尚无合格输入 | 规划24事件未执行 |

两个中性句柄 `case-001` / `case-002` 都属于 `event-001`；重复或衍生窗口不增加分母。模型只接收字段白名单与不透明记录/Trace 句柄，不接收文件路径、标签文件、故障开关或评分答案。局部错误断言 rubric 在配置中预先固定；它不能建立根因真值。

[OpenRCA 官方入口](https://github.com/microsoft/OpenRCA)及其[官方数据目录](https://drive.google.com/drive/folders/1wGiEnu4OkWrjPxfx5ZTROnU37-5UDoPM)于2026-09-29只读检查：目录列出的 Bank.zip、Market.zip、Telecom.zip 分别为 2,829,139,841、2,865,427,796、3,071,992,930 bytes，均超过本轮2GiB总上限。未确认可单独取得且许可明确的合适子集，不下载档案。此结果不声称所有公共数据都不可用；本轮采用契约的数据不足降级，不扩展数据平台。

## 3. 实际算法

`profile_operations`：Trace 按真实服务/操作/方向或用途分层；已知状态作为错误比例分母，同时报告未知状态数。Trace 样本与计数器总体严格分开。计数器复用观察首尾增量和兼容 `+Inf`/15000ms 超限桶，不重建 rate/p95，不把 CLIENT/SERVER 或异步阶段合成订单。

`compare_baseline`：只接受事前固定、范围/单位/方法/窗口长度匹配的历史参考。计算差值、相对变化、MAD 标准化和连续偏离；零尺度不制造 z，缺字段局部降级。当前真实输入缺匹配操作参考，返回 `INSUFFICIENT_REFERENCE`。

`compare_dependencies`：仅实际 Trace 父子关系/已给定一跳拓扑，最多3邻居；返回边、缺父节点、同Trace错误分布。无时钟/采样支持不推断先后；无匹配参考不编造相对变化。相关性不等于因果。

D：同一可观察问题上相反预期的假设对比例 D，结合仅元数据计算的 Q 和固定查询/处理量代理 C，使用 `Q*D/(1+C)`，并列按候选顺序。最多4稳定假设、6动作、8模型请求、1次格式修复。已曝光数值不能再成为事前预测；相同请求、改措辞、换无关参数或重复 UNKNOWN 不构成进展，连续2次无进展停止。

## 4. 四组与成本

A 为同字段、同映射可见的原始过滤/分页 ReAct；B 为无标签的固定服务与工具顺序；C 为同工具自由 ReAct；D 为对比式候选排序。所有组相同初始数值残差、候选服务、快照、模型和预算。公共分组先验不按结果修改。

模型 `gpt-5.4-mini-2026-03-17`，medium，Responses，standard/default tier。上限：输入序列化96,000 bytes（计费预留加协议余量）、输出4096 tokens，输出包含计费 reasoning。价格按[官方模型页](https://developers.openai.com/api/docs/models/gpt-5.4-mini)核对，输入0.75/输出4.50 USD每百万token，缓存按非缓存上界计。账本记录全部成功、失败与未知用量预留；累计上限USD20/1600请求。模型会话自身不算项目实验。

## 5. 定位、语义判别与成本
独立根因测试为 **0/24**，因此 Acc@1、MRR、D-A/D-B/D-C准确率差和bootstrap区间均不计算。默认质量目标保持原样，未切换效率目标。以下分母是开发窗口轨迹，**不是独立事故数**。
| 重复 | 方法 | 报告完成/已启动 | 分析动作 | 真实请求 | 输入token | 输出token | USD上界单价计费 | 错误肯定标志/报告 |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 0 | A | 0/2 | 2 | 6 | 101,430 | 7,223 | 0.108580 | 0/0 |
| 0 | B | 1/2 | 12 | 3 | 57,601 | 5,144 | 0.066350 | 0/1 |
| 0 | C | 2/2 | 7 | 9 | 147,946 | 10,591 | 0.158623 | 0/2 |
| 0 | D | 1/2 | 5 | 10 | 163,349 | 18,875 | 0.207453 | 0/1 |
| 1 | C | 1/2 | 9 | 12 | 203,840 | 15,199 | 0.221279 | 0/1 |
| 1 | D | 1/2 | 0 | 3 | 45,335 | 4,800 | 0.055602 | 0/1 |

费用包括失败和格式修复。两条前期开发轨迹另计7请求、USD0.114191，均FORMAT_FAILED；其中第二条已完成3次分析。**全轮50真实请求、输入827,796 token、输出69,158 token（其中reasoning 30,800）、USD0.932078，未知用量0、未结算0**。不是供应商账单，缓存按非缓存价格上界计；USD20/1600请求均未耗尽，按有限研究计划停止。没有下载数据档案。
完整计数：[逐运行JSONL](runs.jsonl)、[CSV](runs.csv)、[分组摘要](summary.json)、[可核对的选择/预测轨迹摘要](trajectory-summary.jsonl)。完整模型提议、实际计算表、失败诊断和输入位置只留在本地 `.local/semantic-investigation-v1/runs` 与共用账本。公共投影没有原始私有遥测、密钥、绝对路径或隐藏推理文本。
“错误肯定标志”检查 root_confirmed/business_fault_excluded/system_healthy；6份最终报告都为false，同时公布覆盖率，A的0/0不代表安全性证据。固定rubric的文字复核另发现：C在第2窗称含未知状态的payment样本“error-free”，D在第1窗把queue-lag概括为consumer trace线索，存在证据范围/来源混淆。它们未明确确认根因或全系统健康，但不能把布尔false解释成模型没有错误陈述。详见[范围复核](interpretation-review.json)。
程序重新计算冻结配置下35次实际分析，35/35与保存结果一致（排除运行耗时）；数值正确性只认证计算结果，不自动认证模型解释。计数器、分母和参考边界另由聚焦测试覆盖。底层查询与records_scanned是统一的有界等价访问/处理代理，不是实测物理I/O；实际处理耗时/返回字节也保留在逐运行表。
## 6. 差异归因与失败
首轮D比C多花USD0.048830，报告完成率1/2对2/2，没有证明质量提升。第二次D显得更便宜，是因为两窗均0次分析：一窗直接从初始信息作局部排序、一窗格式失败；不构成效率收益。不得跨重复挑最佳值。
D首轮有非零判别价值评分，但5次可观察结果均UNKNOWN，缺失状态/不支持的一跳关系及重复已曝光问题限制了判别。真实候选集合经常只有1项，排序退化为单候选执行；实现了启发式不代表证明选择优势。C的一条重复因连续无进展停止，没有最终报告；另一条通过读取flagd把排序首位改为flagd。该变更没有独立根因标签，不能计为定位正确。
A失败于引用/无关邻居参数，B第二窗失败于引用与稳定假设ID，D第二窗两次重复都因协议/不支持的counter分析组合失败。这些说明模型—工具协议仍不稳定；所有失败保留。两轮开发语义修订后冻结，没有测试后调参、换模型或扩大样本。
## 7. 同一真实事件的三个分析切面
并没有三个独立事故案例。以下均来自event-001，第三项展示缺证据，不凑成新的分母。独立纯计算结果见[实际数值](calculation-examples.json)。
1. **操作分层**：第1窗payment最终300秒观察增量共13个span、1个ERROR；该错误及1个>15s桶增量均来自EventStream。C读取操作profile后区分了短Charge与约600s控制流；不能把计数器样本增量当精确rate，也不能据此排除业务故障。
2. **依赖对比**：同窗checkout→cart实际返回8条父子span边、4个共享Trace。工具确实能返回有数据的一跳关系，两个RPC方向没有合成独立订单。模型选择fraud/flagd等局部组合时也遇到支持不足；这些结果没有证明根因范围在真实调查中被正确缩小。
3. **缺少参考**：payment匹配历史参考缺失，compare_baseline返回INSUFFICIENT_REFERENCE；D的一份报告保留不足，但重复中也出现未读工具便结束。合理保留未知与实际调查不足分别报告，未重启Docker补基线。

## 8. 入口与检查

```bash
PYTHONPATH=src:. uv run python -m scripts.product_v050.evaluate_semantic_investigation --mode inspect
PYTHONPATH=src:. uv run python -m scripts.product_v050.evaluate_semantic_investigation --mode smoke --provider fixture --batch fixture-example
PYTHONPATH=src:. uv run python -m scripts.product_v050.evaluate_semantic_investigation --mode single --provider configured --methods C --case case-001 --batch dev-example
PYTHONPATH=src:. uv run python -m scripts.product_v050.evaluate_semantic_investigation --mode evaluate --provider configured --methods A B C D --split test --batch test-example
PYTHONPATH=src:. uv run python -m scripts.product_v050.evaluate_semantic_investigation --mode summarize --provider configured --batch dev-example --repeat 0
```

付费命令仍要求实际配置与剩余额度；相同run/call key不复用历史输出或自动重试。没有测试输入时明确输出 `NO_CASES_IN_SPLIT`。CLI默认为 fixture；旧数据库不打开、不迁移。

## 9. 终态与允许表述

```text
implementation: ready
experiment: partial
effect: undetermined
```

ready指本轮可运行研究原型，不是模型可靠性、生产就绪或完整验收。缺口为独立根因标签与测试事件（0/24）、匹配历史操作参考、足够有判别力的D预测和稳定模型协议。当前材料允许实现/运行/负面现象报告，无法检验预设准确率收益目标。本轮按数据不足分支交付，不自动开启补采或继续消耗预算。

可用项目表述：实现三类只读语义分析与预算受限对比式视图选择，在1个已见真实事件的2窗上完成12条固定配置开发轨迹（含C/D重复），另外保留2条早期失败；全轮50请求/USD0.932078，未获得独立测试或定位收益证据。

聚焦验证：11项新测试连同旧回放/调查/Provider回归共93通过，改动文件Ruff通过、5个新增/研究文件标准mypy通过（未注解函数体不受默认检查，不能称完整类型证明）。一次只读集成审查及窄复核覆盖账本累计、引用/参数、数据范围与公平性；不以审查替代真实实验。

冻结数据与最终完整tracked delta的SHA-256证据保存在本地 `.local/semantic-investigation-v1/verification`。没有推送、PR修改、Docker/live、故障、恢复写或知识晋升。旧v0.5 NO_VALIDATED_LLM_KNOWLEDGE和失败账本保持原状。

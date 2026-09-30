# 语义视图选择与对比式故障调查

当前续接结果：24事件 / 96条测试轨迹已终止；A/B/C/D正确数2/4/4/2（各24例），累计439请求/USD5.456302。未展示D策略收益。整体仍 **implementation=ready / experiment=partial / effect=undetermined**；详见第10节，前面历史结果不覆盖。

> 续轮：已核清检查点 `217a8a8`。下面第1–9节保留该检查点的历史结果；当前续轮进展与结果见第10节。既有50次请求与全部成功/失败记录不变。

本轮为独立研究，不恢复 v0.5 正式验收。活动契约见 [Goal](../../goals/EcomSRE_Semantic_Investigation_Algorithm_Codex_Goal.md)。**三个工具与研究循环已实现；真实实验为 partial，效果 undetermined。** 完成1个已见事件的2窗四组开发比较及C/D重复，没有独立根因测试，不能宣称定位或策略增益。

## 1. 范围与代码

起始版本 `c7dce3720b2d877a10db30a940dd238c275b06e1`。三个只读工具、研究策略、独立 CLI 和账本已实现；旧调查入口及 Product 库不参与研究。旧计数器纯函数抽取共用，不改变历史计算含义。最终算法输入、配置与源码由逐运行私有intent中的SHA-256绑定，公共摘要见 [summary.json](summary.json)；交付源码版本 `cc8a44ba6279afd9206cc5df516ba464a8e1b2ef`（后续仅补充本报告的版本引用）。93项相关检查通过。

read scope：本轮直接依赖的 investigation、连接器、旧回放与保留 live-03 输入；write scope：新增 semantic 模块、Provider 的新任务提示窄适配、研究 CLI/config/tests/report/Goal、旧回放纯函数导入和活动指针。frozen asset scope：live-03 清单列出的输入、旧 Product 库、旧语义结果目录与正式 `config/product-v050`；其他历史记录由全tracked-delta检查确认未修改；final repository scope：此工作树相对起始版本的完整 tracked delta。私有新产物只写 `.local/semantic-investigation-v1`；全工作树共用付费账本保存在 Git common-dir 的同名命名空间。无 Docker/live/故障/知识晋升/远程发布。

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
当时**可用独立根因测试事件0个、启动0条、计划24个事件**，因此 Acc@1、MRR、D-A/D-B/D-C准确率差和bootstrap区间均不计算。默认质量目标保持原样，未切换效率目标。以下分母是开发窗口轨迹，**不是独立事故数**。
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

ready指本轮可运行研究原型，不是模型可靠性、生产就绪或完整验收。缺口为独立根因标签与测试事件（当时合格0个、启动0条，计划24个）、匹配历史操作参考、足够有判别力的D预测和稳定模型协议。当前材料允许实现/运行/负面现象报告，无法检验预设准确率收益目标。本轮按数据不足分支交付，不自动开启补采或继续消耗预算。

可用项目表述：实现三类只读语义分析与预算受限对比式视图选择，在1个已见真实事件的2窗上完成12条固定配置开发轨迹（含C/D重复），另外保留2条早期失败；全轮50请求/USD0.932078，未获得独立测试或定位收益证据。

聚焦验证：11项新测试连同旧回放/调查/Provider回归共93通过，改动文件Ruff通过、5个新增/研究文件标准mypy通过（未注解函数体不受默认检查，不能称完整类型证明）。一次只读集成审查及窄复核覆盖账本累计、引用/参数、数据范围与公平性；不以审查替代真实实验。

冻结数据与最终完整tracked delta的SHA-256证据保存在本地 `.local/semantic-investigation-v1/verification`。没有推送、PR修改、Docker/live、故障、恢复写或知识晋升。旧v0.5 NO_VALIDATED_LLM_KNOWLEDGE和失败账本保持原状。

## 10. 检查点后的有界配对实验

### 10.1 原“独立测试”含义与工作树关系

检查点 `217a8a8` 的 config 中只有2个 development case、同属1个事件组、root均为null，test split为空。因而当时**可用独立根因测试事件0个，每个方法启动0、完成0、失败0、正确数N/A；计划24个事件全部未执行**。未执行原因是没有符合条件的带标签独立输入，不是24个事件答错。旧14条开发轨迹（含2条早期失败）全部保留；A/B/C/D历史启动分别2/2/6/4，完成0/1/3/2，失败2/1/3/2。[机器计数](checkpoint-217a8a8-coverage.json)。

实际研究工作树为 `product-v050-llm-investigation-knowledge`，分支 `codex/product-v050-llm-investigation-knowledge`。217a8a8在该分支上，依次继承cc8a44b与c7dce37；本轮本地适配/公平性/异常终态提交为aa92cc6、2df5034、cb1875f。另一个工作树 `New project` 仍在 `phase3/restricted-remediation-replay` / d93d267，原有dirty内容未改。两者共用Git对象与同一Provider预算账本。截图前缀不能证明切换了PR；实查远端PR #104仍Draft/open，head分支同研究分支、远端OID仍c7dce37。本轮未push、改PR、merge或release。

### 10.2 数据补齐与适用范围

OpenRCA官方Market包支持HTTP Range，可以只读成员，完整ZIP大小并非真正阻塞。已探查归档目录、2个record.csv与小段trace：有operation_name、parent_span、真实根因记录。但未确认遥测许可；仓库MIT不能自动当作外部遥测许可，[官方仓库许可问题](https://github.com/microsoft/OpenRCA/issues/24)检查时无回复。原Goal要求许可不明不下载/再发布，故停止后续下载和外发。**流程偏差如实保留：本轮在确认遥测许可前已读取272,770字节归档范围数据；这些材料不进入被评估的项目Provider或结果评分，也不提交原数据。**网页/API元数据另留8MiB保守额度占用上界，未重置2GiB上限。

转用本地早已保存、许可明确的RCA-100（Wen等，2026；CC BY-NC-SA 4.0，固定源commit fd92cae17e6e14fa3ed0f3963c31838151fbdaa7）。仅本地非商业研究，原始遥测、answer key、原案例映射和模型完整输出不公开。它曾用于另一个项目研究，不能称为从未见过的数据集；本轮算法的开发/测试按新冻结事件组分开，测试后不调参。未读取旧研究的案例级模型答案用于本算法。

103个源任务中87个是单一apm.service根因。按所有任务实际使用的25分钟支持区间做传递重叠合并，得到67组，其中57组包含这类可评分标签。按不依赖模型表现的固定哈希选择12开发、24测试组；已在字段探查中看过的t001所在整组强制归开发。测试覆盖17个数据集机制标签，但只评价**服务粒度定位**，不套用RCA-100官方复合分数，不验证节点/Pod定位、故障类型、传播链或全模态RCA。

每例当前窗为告警结束前300s；三个同长参考窗与当前窗隔300s，全部strictly prior。初版因短告警造成的参考重叠在Provider启动前已纠正，初版无模型调用准备材料保留在本地。参考只是历史样本，不宣称健康或真实在线预固定。所有组使用相同450 span/窗上限：先覆盖真实观测服务的完整trace，再以固定trace哈希补齐；不按错误状态、标签或模型表现挑记录。实际无记录、未知状态和缺参考保留，不移除对应测试事件。

模型仅见服务、操作名、OTel方向/状态、纳秒正确换算的持续时间、脱敏trace/span/parent标识、窗口与实际采样父关系拓扑。status UNSET保留未知；父节点缺失不补造；不提供同步时钟证明。无metrics/logs/events/resources/attributes；旧用途映射对所有组相同，未匹配操作保留UNCLASSIFIED。本实验可检查三个工具在这种trace子集上的行为，不能宣称覆盖计数器、完整遥测或所有业务语义。

[输入与范围](rca100-inputs.json)记录各快照hash、源行数和样本数；[公开协议](rca100-protocol.json)省略标签和源映射。私有experiment、manifest和原始输出仍在同一`.local/semantic-investigation-v1`下，原结果文件未覆盖。适配准备使用已有本地环境的Python 3.12.2 / PyArrow 23.0.1；研究仓库默认环境未新增PyArrow依赖。重新从Parquet准备时需提供这个依赖，已冻结JSON快照的运行不需要PyArrow。

### 10.3 开发检查、窄修复与固定测试

只修直接影响实验的事项：B按目标/单位匹配reference；共同metadata公开reference scope与同一份采样拓扑；普通ReAct A/C可以更新假设目标，D仍保持稳定ID；无关neighbors不再使raw读取失败，依赖查询仍严格校验；B固定流程耗尽却没收到report时按原1次修复上限终止，避免IndexError。没有新增D提示词/策略语义修订，原2次上限已用完，max4假设/6动作/8请求/1修复、模型与价格均不变。

先运行rcase-001的修复前配对，4条全部失败，单列为历史开发材料，不算公平对照收益。其后rcase-002/003/004完成3个配对事件：A/B/C/D的正确数为0/0/2/0，Acc@1为0/0/66.7%/0，MRR为0.167/0.167/0.833/0。其中B一条已启动后发生运行时异常：保留2次真实请求、原intent及账本提议，离线重建确定性工具结果并标注缺失原耗时，不重试、不改成成功。其余未运行开发事件不冒充完成12例开发实验。

开发纯计算中profile均有分组记录；匹配baseline的部分案例返回真实差值（零MAD保留z=null），另一些缺参考；dependency在四例中分别出现17、0、32、9条真实父子边，零边例保留缺失。[开发工具可用性](rca100-development-tool-usability.json)。C按普通ReAct自由选工具，未要求竞争矩阵。D实际提出多个相反预测的候选，运行时按Q*D/(1+C)选择profile或baseline；它并非每例硬编码相同工具。但D的预测字段/row_key错误和协议失败突出，开发可观察结果多为UNKNOWN，不能声称策略已经有效。固定测试评价这种现状，所有失败纳入主指标；不为获得PASS继续优化D。

测试开始前固定24个事件、4组共96条计划轨迹；每事件方法顺序轮转，最多2个事件并行，全部共享原账本。原50次平均成本USD0.01864156/请求，按既有轨迹长度估计24例约USD6.4；这只是估计，不是费用保证，逐请求硬上限仍USD20/1600次。测试开始后不修改实验实现、输入、模型、候选或评分主指标。

### 10.4 固定测试结果

已选24个测试事件，A/B/C/D均启动24条；96条轨迹均终止，未启动0条。失败保留在Acc@1/MRR分母。最终报告完成数另列，不能用报告完成替代定位正确。

| 方法 | 启动/计划 | 完成报告 | 正确/测试 | Acc@1 | MRR | 空排序拒答 | INSUFFICIENT报告 | 格式失败 | Provider失败 | 其他失败 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| A | 24/24 | 11 | 2/24 | 8.3% | 0.181 | 0 | 6 | 7 | 0 | 6 |
| B | 24/24 | 18 | 4/24 | 16.7% | 0.285 | 0 | 10 | 6 | 0 | 0 |
| C | 24/24 | 16 | 4/24 | 16.7% | 0.274 | 0 | 12 | 8 | 0 | 0 |
| D | 24/24 | 6 | 2/24 | 8.3% | 0.111 | 0 | 6 | 8 | 8 | 2 |

INSUFFICIENT_EVIDENCE报告可能仍给出候选排序；排序按Acc@1/MRR评分，同时单列其证据不足声明。真正空排序的完成报告另列“空排序拒答”。未完成报告的失败一律空预测/0分，不当作正常拒答。格式失败包括schema/引用/假设协议；Provider失败包括截断等；其他失败如无进展、动作或访问预算耗尽。

| 方法 | 模型请求 | 输入token | 输出token | USD计费上界 | 等价查询 | 扫描记录代理 | 返回字节 |
|---|---:|---:|---:|---:|---:|---:|---:|
| A | 121 | 1394236 | 116862 | 1.571602 | 76 | 131400 | 454295 |
| B | 37 | 317816 | 48118 | 0.454906 | 232 | 648000 | 136097 |
| C | 111 | 833829 | 100872 | 1.079338 | 158 | 488700 | 103675 |
| D | 74 | 478204 | 123508 | 0.914462 | 56 | 198450 | 34376 |

预处理和扫描记录是统一代理，不是物理I/O。原始Parquet仅由共同适配阶段读取，模型均使用相同冻结快照；各方法的preprocessing_records另保存在逐运行数据中。token价格采用既定非缓存输入上界，不是账单发票。

| 配对差异 | 配对事件 | 正确数差 | Acc@1差 | 事件配对bootstrap 95%区间 |
|---|---:|---:|---:|---|
| D-A | 24 | +0 | +0.0个百分点 | [-12.5, +12.5]个百分点 |
| D-B | 24 | -2 | -8.3个百分点 | [-25.0, +8.3]个百分点 |
| D-C | 24 | -2 | -8.3个百分点 | [-20.8, +0.0]个百分点 |

累计（含原50请求、修复前开发、失败与本次测试）：**439请求，输入4,159,855 / 输出519,166 token，USD 5.456302**。pending=0、未知usage=0；剩余USD 14.543698 / 1161请求。未重置预算，也不为用完额度追加实验。

[逐运行JSONL](rca100-test-v1-test-runs.jsonl) / [CSV](rca100-test-v1-test-runs.csv) / [方法与配对汇总](rca100-test-v1-test-summary.json) / [选择及预测轨迹](rca100-test-v1-test-trajectory.jsonl) / [累计账本摘要](rca100-budget.json)。

### 10.5 失败、收益边界与收口

D测试实际选择工具分布为`{'compare_baseline': 14, 'profile_operations': 13, 'compare_dependencies': 7}`，多候选动作13次；可观察预测结果为`{'UNKNOWN': 31, 'NO': 2, 'YES': 1}`。有评分选择不等于有效消除竞争解释。主要错误发生次数见[失败分析](rca100-failure-analysis.json)，不是新的事件分母。

代表性开发失败：rcase-002/D对profile询问relative_change，字段与工具不匹配；rcase-003/D对baseline使用操作分组row_key而不是comparison，结果UNKNOWN。这些属于方法提出/理解分析的问题，缺失或非法问题不能当NO。另有引用错误、稳定ID目标变化与输出截断。测试后未修复它们。

代表性测试文字盲核查：A/rcase-015把没有参考支持的长时延称为elevated；B/rcase-013称只有frontend分组有已知错误，但依赖结果还包含checkout错误；C/rcase-016把POST错误写成GET，且整服务中位数不能排除操作或尾部退化。D/rcase-021把中位数称为average并暗示局部健康，尽管工具返回healthy_baseline=false。详见[四条盲核查](rca100-interpretation-review.json)。这是选择性案例分析，不推成全体文字错误率，结构化安全标志也不替代事实核对。

**表示收益**：C−A描述性差为+8.3个百分点；D−A为+0.0个百分点。单个小样本的排名差不证明通用表示收益；格式失败也会影响这个差。**策略收益**：D−C为-8.3个百分点。本固定测试未展示对比式策略收益。

本轮已执行目标24个独立事件组的配对测试，但适用范围是抽样trace、单服务根因；12个开发目标仅实际运行4个，其中3个用于修复后配对。保持 **implementation=ready / experiment=partial / effect=undetermined**，同时明确`test_execution=24 events / 96 terminal trajectories`。这既不是“没有测试”，也不是完整原Goal全部场景验收。未启动测试为0；未运行开发事件8个，不冒充已完成。对当前算法的负面/不确定结果正常收口，不换测试集、不换主指标、不追加部署或调参求PASS。

Docker、实时遥测、业务流量、故障注入、恢复写入、正式事件与知识晋升均为0。没有新Goal、外部Pro审查、PR修改、推送、merge或release。所有旧成功/失败记录及账本保留；原v0.5正式验收仍停止。

### 10.6 核验与复现边界

- 96条测试intent均绑定cb1875f、同一配置及各自冻结快照；36份开发/测试快照未变。原50条已结算请求完全不变，账本保持原字节前缀，8份原结果文件与217a8a8逐字节相同。[完整性核对](rca100-integrity.json)。
- 对本轮277次程序分析（raw 81、profile 77、baseline 68、dependency 51）从冻结快照重新计算，JSON序列化规范化后277/277相同，仅排除非确定的compute_ms。[复算](rca100-numerical-recalculation.json)。首次直接比较Python元组与保存的JSON数组产生39项类型差异，原核对记录保留；未因此修改运行结果、数值或实现。
- 独立只读复核用私有标签、原始报告和账本重算全部96行：评分、启动数、请求数、费用、三个配对bootstrap区间均一致，未发现重大评分或结论缺口；未调用模型或改代码。
- 原有五组局部回归93项通过，Ruff及diff whitespace检查通过。复算证明程序结果可重现，不能证明模型解释或预测正确。
- 主要执行失败：A有7格式/6无进展，B有6格式，C有8格式；D有8格式/8输出截断/2无进展。D完成6份报告均标记INSUFFICIENT_EVIDENCE，其中2份没有分析动作；正确的2例分别执行2次与1次分析。D的31/34预测检查为UNKNOWN，不能据工具调用多样性宣称竞争预测机制有效。
- 最终核验以217a8a8为显式基线，完整追踪差异范围为本仓库；写入范围仅适配/实验脚本、semantic_analysis/policy、局部回归与本阶段报告/状态。冻结范围为实际运行配置和36个快照；证据为原账本最终副本、运行结果及本阶段记录。私有核验文件保存在`.local/semantic-investigation-v1/verification-continuation/`，不提交原始数据或完整模型文本。

仅重新汇总已保存结果（不发Provider请求）的命令：

```sh
PYTHONPATH=src:. uv run python -m scripts.product_v050.summarize_semantic_subset \
  --config .local/semantic-investigation-v1/rca100/experiment.json \
  --root .local/semantic-investigation-v1 \
  --ledger '/Users/raidriar/Documents/New project/.git/semantic-investigation-v1/provider/ledger.jsonl' \
  --batch rca100-test-v1 --split test \
  --output docs/results/semantic-investigation-v1
```

上述复现依赖保留的私有快照和账本；公开派生结果不是原始数据分发包。后续若用这些测试案例修复或调参，必须标作看过的分析材料，不再把同一份结果称为新的独立测试。

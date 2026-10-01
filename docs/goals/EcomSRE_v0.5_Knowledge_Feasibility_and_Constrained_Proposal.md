# EcomSRE v0.5 — 先确认知识任务可行，再进行受约束提议

> Repository: `Raidriar7170/EcomSRE-Agent`；继续同一个 Draft PR #104。  
> 核对 HEAD: `3ae84ab4bda5054ccfbfde74e9c655a416e312e4`。  
> 日期: 2026-09-18。  
> 性质: 原 v0.5 Goal 的定向修正，不是 v0.6、重建框架或无限重试授权。  
> 主要出口: 首次真实候选进入开发求值，或者以具体事件/字段证明输入不可行。  
> 本文件是设计与续跑指令；生成文件不代表已经修改仓库、调用模型或运行实验。

## 0. 激活与范围

用户在 Codex 明确要求执行本文件后，允许在原分支/PR 内完成下述代码修复、离线检查及有界开发调用。先读原 Goal、最新进度、当前 HEAD 和累计账本；本地已修复项以测试证据关闭，不重复实现。

本补充只改变新开发轮的模型交互与准入前检查，不修改旧候选、旧模型输出、旧拒绝、旧协议、历史成功条件或既有 Shadow 门槛。不把已耗尽的旧轮次重新打开；新调用使用独立协议身份并记录改动依据。

- A/B 阶段不启动 Docker；C 阶段是已见数据的真实 Provider 回放；D 阶段满足条件后恢复原 Goal 既有的 owned 本地实验权限。前面的不启动 Docker 不延伸到 D 阶段。
- 不修改 Docker Desktop、系统网络、全局 Codex 配置、账户权限或其他项目资源。
- 不开发 skill 对照、多模型路由、第二套存储或新的授权服务。
- Product 恢复仍为只读预览；模型无 shell、Docker socket、生产写入、晋升或评分权限。
- 可以更新同一个 Draft PR；不自动 merge/tag/release/deploy。

## 1. 保留事实，不错误归因

最新结果为 `ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE`。本次已核对的工程结果是：新增 3 次请求、2 个 schema-valid 草稿、0 个准入候选、0 次实际开发求值。当前不是“规则通过格式后经独立测试无效”，而是尚未进入正式规则效果检验。[R1][R2]

已观察到：

1. `CandidateDraft.target_support`、`target_counterevidence`、成员与依赖字段仍含自由 `str`；`strict_schema()` 处理必填/可空和额外字段，但不生成当前目录的枚举约束。[R3]
2. `draft_view()` 的 `e-`/`d-` 别名后接完整 SHA-256；视图同时携带目录和此前 hypotheses/decisions，仍有重复长引用。[R3]
3. 新任务为 `propose_detection_draft_v050_1`，但 `TASK_CONTRACTS` 仅含旧 investigation/knowledge 键；新任务虽然有视图内 constraints 和严格 schema，仍缺专属高优先级任务说明。[R3][R4]
4. 新请求输入约 50,189 / 50,209 tokens，失败请求输出 4,096 tokens、状态 incomplete；Provider 固定 output_cap=4096，runner 使用 high reasoning。[R2][R4][R5]
5. 第三份草稿不仅有截断/跨目标证据，还选了两条同一 Development 事件上的 `LEGAL_NOT_COLLECTED` 依赖、不同 offset，以及一条未知依赖别名。这意味着修复第一个引用错误仍不足以使该草稿准入。[R2]

以上是代码和记录中的可核查事实，不证明每个问题分别造成多少失败，也不证明当前全部数据不可学习。先做诊断，不用“Mini 太弱”或“Runtime 过严”替代定位。

原账本检查点：54/200 requests；USD 0.839581/20 commitment；5/12 live episodes。对应剩余为 146 requests / USD 19.160419 / 7 episodes，仅作检查点，启动时重读。未知 usage 预留不清零，预算承诺不是账单。

## 2. Phase A：零 Provider 请求的任务可行性检查

先检查实际输入是否允许完成当前任务，不能先生成新 prompt 后直接付费尝试。

### 2.1 实际证据矩阵

仅从保留的合法 Discovery/Development CAS、session、episode 和来源记录建立表格。不得通过创建新 incident、改角色或删除缺失事件来制造可行性。

至少逐事件、逐目标列出：

- 角色与独立 episode 身份；当前证据属于已见数据。
- 完整的目标观测来源、实际服务记录、窗口与字段。
- 截断、失败、空结果、覆盖不足的条目；保留缺口，不补零。
- 已有谓词的 PRESENT / conclusive absence / UNKNOWN / SOURCE_FAILED；只用既有求值器生成。
- 原始资源快照与补查资源观测分开；已有数据不自动具有补查依赖身份。
- 每项资源依赖的相对窗口、采样窗口、样本数、BOUND_OBSERVATION / COLLECTED_INCOMPLETE / LEGAL_NOT_COLLECTED 状态。
- 不同成员间是否存在同语义已采集依赖；两个声明的 Development 事件是否可实际求值。
- 是否存在健康、已知故障、相似症状的开发控制；没有则明确“只有正例，不能评估特异性”。

读取原库时不得触发迁移、生成新 session、重写状态或重新暴露 holdout。确需记录新开发 exposure 时，必须在真正进入新开发流程后显式追加，不能把会写库的方法称为只读检查。

### 2.2 分开回答三个问题

A. **输入可表达性**：至少存在由当前合法引用/依赖组成的、可编译和可求值的结构吗？这不要求其匹配成功。

B. **规则拟合**：真实模型选择的谓词和阈值在已见数据上是否成立？此时才能运行开发求值。

C. **有效性**：是否能区分独立正例和混淆/健康控制？这必须等待独立验证。

禁止把 A 当作 B 或 C。可以做明显标为 `DIAGNOSTIC_ONLY / NOT_MODEL_OUTPUT` 的机械结构测试或已有 miner 探查，但不得注册、计为 LLM 候选、提交给模型充当答案或宣传学习成功。

若目标 Level B 路径没有同语义有效输入，A 阶段输出具体缺失条目，C 阶段不再请求模型猜不存在的数据。可以继续完成 B 的离线接口修复；涉及补采的数据工作按第 6 节处理。

不要事后删除缺资料的 Development 事件来取得 2/2。未来新增开发事件可追加，旧事件和原失败分母继续披露，不能改成此前就有的盲测。

## 3. Phase B：使任务接口真正易用，不降低证据标准

### 3.1 明确任务，而不是只更换 schema

给新任务 ID 配置专属高优先级说明，说明：当前任务是跨事件知识候选、不是在线调查；输入是已见事件；LLM 选择语义条件和阈值；引用只能从相应角色目录选择；不足时返回 NO_CANDIDATE / NEEDS_OBSERVATION。

把可信协议说明与不可信 telemetry 分开。不要将 telemetry 提升为指令，也不要依赖长观测 JSON 末尾的一段 constraints 承担全部任务定义。对任务 ID 与 prompt 映射增加聚焦测试。

### 3.2 短句柄 + 请求绑定映射

面向模型使用如 `E01`、`D03`、`I02` 的短句柄，不再要求复制完整哈希。后台映射仍精确绑定请求/快照、事件、服务、窗口、原始 evidence_ref 与对象摘要。

只缩短展示，不弱化验证。不同请求中的相同短句柄不能跨上下文复用；映射顺序变化不能偷换语义；旧证据、候选和哈希不修改。

### 3.3 目录必须进入真实发送的 JSON Schema

对模型当前选择的 target/member scope，分别生成：

- 目标可用证据的短句柄枚举：只含当前目标、合法成员、完整覆盖的真实条目。模型仍判断其是支持还是反证。
- comparison-only 的合法条目和服务绑定：不能计入目标来源数量或匹配条件。
- 已采集、可部署的依赖枚举；未采集项在缺口信息中可见，但不能作为已满足依赖选择。
- 当前合法事件和已实现谓词枚举；不凭故障真值裁剪选项。

不允许先给全局自由字符串、再完全依靠提示词提醒。`strict=true` 只保证满足实际 schema；若 schema 允许任意 string，自然不会阻止在引用栏填一句话。[O1]

目标尚未确定时，可先用一次有界的 scope selection，再生成目标范围内的 schema；或复用已有、证据支持的单目标 family 作用域。不得从注入器或 evaluator truth 提供正确目标。scope 调用计入请求上限，不是新增 Agent。

空目录必须仍可表达弃答或缺观测，不能通过塞虚假条目满足 enum。枚举规模超出官方限制时采取有界分阶段选择，不构造巨大 schema，也不静默截去关键反证。[O2]

检查真正发给 Provider 的 payload，并用假 transport 捕获测试。只测试 Python 类型中存在 enum、但实际 API 使用另一份 schema，不算完成。

### 3.4 缩减上下文与输出负担

候选生成默认不重传全部历史 hypotheses、全部 decisions、重复身份/hash 和原始大批量日志。保留逐事件事实、负证据、数值必要样本、单位/窗口/覆盖、合法句柄、当前草稿及结构化错误反馈。

目标为首个提议输入约 8k–16k tokens，作为工程目标而非不可调低的安全阈值。必须给出输入组成统计和原来约 50k 的差异；不能为压缩抹去冲突、漏报缺口或把缺失变健康。需要细节时提供受控的已见证据展开入口，不读取 holdout。

使用逐事件/逐来源的确定性摘要，数值从原记录计算，不让另一个 LLM 发明摘要。旧推断如确有必要，标为未验证模型推断，不混入事实。

将候选任务 output cap 配置化；本轮建议 `reasoning=medium`、`max_output_tokens=8192`。这是新的开发参数，不承诺一定足够。固定为协议身份并按更大的输出上界预留费用。不得影响历史路径或在测到失败后无界加长。

保留返回的 `incomplete_details` 与 token usage；可取得的 reasoning token 数只记计数，不保存或索取隐藏思维链。官方说明该上限也涵盖 reasoning tokens，因此 4096 不是单指可见 JSON 字数。[O3]

### 3.5 一次报告完整错误，不只 fail-fast 的第一个字段

保留硬准入拒绝。另加只读诊断投影，汇总当前可确定的引用、角色、成员、依赖、单位和来源问题。不得在诊断中替换引用、补齐条件或使用“最接近的合法值”。

可以对原模型已明确选择的表达式做独立 `DIAGNOSTIC_ONLY` 数值求值以定位问题，但必须标明准入失败，不能算作已准入开发结果、Shadow 通过或可晋升候选。

正式候选进入开发求值后，逐条件显示 TRUE / FALSE / UNKNOWN 及证据依据，而不只有一个总 error code。

## 4. 付费前必须通过的聚焦检查

1. 新 TASK 有实际使用的专属任务说明。
2. API payload 的引用字段含当前目录 enum；文本、旧快照句柄和未知句柄被 schema/本地校验拒绝。
3. 当前目标证据目录不提供他服务或截断条目作目标证据；缺口仍在模型视图里。
4. comparison_context 不影响目标来源、条款、准入分数或写权限。
5. LEGAL_NOT_COLLECTED、不同 offset、缺成员与重复依赖都能在调用前或本地聚合诊断中明确识别。
6. 原始依赖、采样窗口、样本数与物理单位不被短句柄/编译器悄悄改写。
7. 至少一个非模型结构测试可以经过同一编译器与既有求值路径；它明确是机械测试，不会成为模型候选。
8. Prompt 压缩保留反证、覆盖和完整资源数据；测试实际发送内容及其 token 估计。
9. 超时、截断和解析失败照常计账；任何较大 output cap 的预算预留同步更新。
10. 旧结果和现行安全门槛保持；没有因绕过失败而产生的 ACTIVE 或新写入。

以上是可执行检查，不是另造一套庞大治理平台。仅修改必要文件，复用现有 CAS、Provider 和 verifier。

## 5. Phase C：一次有界的开发轮

模型保持 `gpt-5.4-mini-2026-03-17` 与已工作的 Responses；不静默切换。若用户随后明确选择完整 GPT-5.4，则建立独立配置/价格身份并在调用前冻结，旧 Mini 输出不能混记为新模型成绩。

本轮只有用户激活后才新增授权：

- 在原总账本内，最多新增 6 次 Provider 请求、USD 2 承诺上界。
- scope selection、提议、细节展开、协议失败、重试与语义修订全部计入 6 次；这不是旧子预算的剩余额度，也不刷新原 200/USD20 总额度。
- 最多一个初始候选加两次语义修订。截断后没有完整语义锚点，下一次完整候选调用按语义尝试计数；不能称为不计数格式修复。
- 无新信息且重复同类错误，不再请求相同输入。首个达到预先确定开发出口的候选出现后停止挑选，进入冻结或报告缺口。
- 有足够可表达资料却模型弃答时记录能力结果；资料缺失时记录观测缺口，不强迫模型产出候选。

候选有效格式 → 严格准入 → 实际开发求值，分别计数。三条同时必须可审计，不能只再交“新增类型和单测全部绿”。

不能把初始事件中的 `delta(memory)>0` 等数值条件仅为了满足 Level B 要求而包装成机制发现。保留模型自由提议，但必须用健康/混淆控制检验它是否有区分价值。Level A 先成立可以单独报告，不能因此降低原完整验收对 Level B 的要求。

## 6. Phase D：只在有明确用途时补数据或进行独立验证

### 6.1 数据确实缺失

A 阶段若证明既有材料缺必要观测，不继续用付费调用猜测。优先核查是否有合法保留的同事件原始遥测可按原语义离线重建；新的派生结果必须追加，不改原快照或假称此前已读取。

若旧遥测不存在，不用现在的服务数据填过去的时间窗。只有原 Goal 已授权控制与读取语义能够补足时，允许在剩余 live 总额度内最多新增两个开发采集 episode；它们有新身份，保留旧不完整事件/分母，并标为开发数据，不是盲测。

此次补采权限只覆盖已有 owned 场景和既有观测，不授权部署新系统、扩大来源限制、删真实数据或修改非 owned 资源。若需要超出范围的新采集能力，报告具体能力缺口，不泛泛要求用户“继续”。

固定预采集与模型选择读取必须分开计数。系统补数据的动作不能冒称 LLM 主动调查。

### 6.2 有候选通过开发

达到原门槛后，冻结候选、真实模型来源、prompt/schema、依赖和求值器，再按原 Goal 和既有 Live Resume / Docker Stability 作用域执行独立控制、Shadow、测试注册库晋升和新事件复用。A/B/C 的无 Docker 限制此时结束。

保留此前 5 个已见事件角色；不能拿它们做未见验证，也不能将补采开发 episode 改成 holdout。已有健康基线如用于开发必须显式标记。独立误报率不能用只有正例的开发结果替代。

新环境启动前重新核对资源所有权与当前状态；不需要无条件重跑已经解决的 Provider 404 或重复关闭 Resource Saver。出现新的非项目漂移即停止；不默默更新基线、全局 prune 或重置预算。

同一环境身份、来源能力与历史 snapshot 的兼容性必须核对。不能新建名称就当原环境可用，也不能自动复用旧审批。

补采与验证/复发共用原剩余 7 个 live episode，不新开一份额度。若剩余不足完成必要控制，应如实限制结论。

## 7. 交付、停止与减少无效工程

继续现有进度记录和一个小结果子目录即可，不另建一套通用状态机/平台；需要的新状态仅作为报告细分，不重写旧正式终态。

最终报告优先回答：

| 层级 | 必须给出的结果 |
|---|---|
| 输入可行性 | 哪些成员、来源、依赖可表达；哪些资料缺失；不是一句“未通过” |
| 模型接口 | 真正发送的任务说明、schema 约束、短句柄绑定与 token 规模 |
| 真实提议 | schema-valid / admitted / evaluated 各几次；失败在哪一层 |
| 开发效果 | 逐条件结果及分母；控制不足时明确无特异性结论 |
| 独立验证 | 是否冻结、是否执行、通过或拒绝；不靠改阈值追求 PASS |
| 成本与权限 | 原累计账本、未知费用、实际 live 数、零 Product 恢复写入 |

正常工程修复和阶段切换持续进行，不反复要求同一授权。新模型选择、总预算增加、超范围环境或真正安全冲突才需新的具体决策。

数据可行性检查不通过时，本轮可以以明确 OBSERVATION_GAP 附原合法限制终态结束；若模型在已验证可行的输入上仍失败，保留能力结果，而不是再自动增加候选轮次。

只在稳定交付 HEAD 上跑一次必要全量回归/CI；中间用相关聚焦测试。冻结全量测试期间的 HEAD，不为每次模型拒绝重新提交大批“收尾文件”。

原完整 `ECOMSRE_PRODUCT_V050_ACCEPTANCE_PASS` 条件不变；本轮准入或开发通过不能冒充完整学习闭环。

## 8. 来源与核查边界

本文由远端源码及公开真实调用记录的定向审阅形成；没有在用户本机重跑 Provider、Docker 或完整测试，私有原始 CAS 不在此次直接核查范围。公开投影中无法确定的内容必须由本地零请求检查验证，不把删敏占位符当作原模型文本。

- [R1] PR #104 与当前 HEAD：`https://github.com/Raidriar7170/EcomSRE-Agent/pull/104`
- [R2] 三次调用及安全投影：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/3ae84ab4bda5054ccfbfde74e9c655a416e312e4/docs/results/product-v050/knowledge-contract-repair/calls.json`
- [R3] 草稿视图/编译器：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/3ae84ab4bda5054ccfbfde74e9c655a416e312e4/src/ecomsre/product/knowledge/drafts_v050.py`
- [R4] Provider：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/3ae84ab4bda5054ccfbfde74e9c655a416e312e4/src/ecomsre/product/investigation/provider.py`
- [R5] 开发 runner：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/3ae84ab4bda5054ccfbfde74e9c655a416e312e4/scripts/product_v050/knowledge_contract_repair.py`
- [O1] 官方函数调用与 enum 建议：`https://developers.openai.com/api/docs/guides/function-calling`
- [O2] 官方 Structured Outputs schema 限制：`https://developers.openai.com/api/docs/guides/structured-outputs`
- [O3] 官方 reasoning 输出预算说明：`https://developers.openai.com/api/docs/guides/reasoning`

本文新增子预算、token 目标和阶段安排是工程设计，不是外部标准或已经获得的结果。

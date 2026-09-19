# EcomSRE v0.5 — 知识提议接口修复与开发集验证

> 仓库：`Raidriar7170/EcomSRE-Agent`；继续同一个 Draft PR #104。  
> 核对 HEAD：`398b414b2561b27a94a9663bb8e0ba24627e984f`；日期：2026-09-18。  
> 本文件是原 v0.5 Goal 的一次有限、版本化开发续跑，不是 v0.6，不新增 skill 对照。  
> 审阅范围：已提交结果、Provider 调用记录和相关源码的定向静态审阅；没有在用户本机重新运行测试、模型或 Docker。  
> 第一目标：让真实模型输出的候选经过严格校验，真正进入开发集求值；通过才进入原 Goal 的独立验证。

## 0. 激活与本轮边界

由用户在原 Codex 会话明确发送以下文字后执行；保存或下载本文件本身不授权外部操作：

```text
继续原 v0.5 Goal 和同一个 Draft PR #104，执行
 docs/goals/EcomSRE_v0.5_Knowledge_Contract_Repair.md。
我授权在原累计预算内，追加一次修复后、有明确新协议版本的有界开发轮；
不把上一轮已经耗尽的两次修订额度自动恢复。
先复用已采集的 3 个 Discovery / 2 个 Development episode，
修复证据角色、模型输出与 Runtime 编译的接口，然后运行开发检查。
A/B 阶段不启动 Docker；这项限制只适用于本次接口修复和开发回放。
候选通过开发检查并冻结后，允许按本文 C 阶段和原 Goal 的 owned 范围，
启动一个新的独立验证 campaign，完成必要控制、盲测和新事件复用。
不重跑旧 Discovery 来挑结果，不改历史终态或证据，不开放恢复写权限，
不换模型、不重置账本、不自动 merge/release。
```

执行范围明确分阶段，避免“本阶段不启动 Docker”再次覆盖后续已授权 live：

- **A：离线定向修复。** 不调用 Provider，不启动 Docker，不新增事故。
- **B：真实模型 + 已见开发遥测回放。** 新增最多 6 次 Provider 请求、累计新增承诺最多 USD 1，均计入原账本。最多 1 个初始候选和 2 次语义修订；每个候选最多 1 次无副作用格式修复，全部请求一起计数。传输失败也计数，不无限重试。
- **C：条件式独立验证。** 只有 B 的准入及开发检查通过、候选和评估协议冻结后才进入。沿用原总上限和剩余额度；新建一个明确 owned 的验证 campaign，保留旧 campaign 和旧基线。当前环境稳定性与所有权检查通过后可登记新起点；不能忽略无法解释的新增非项目漂移或循环刷新基线。

本次起点累计 51 次请求、USD 0.695520 承诺、5 个 live episode；当时剩余 149 次请求、USD 19.304480、7 个 live episode。实际账单仍未知。执行时读取原账本；小于这些数字的剩余额度优先，不建立第二份预算。

保持当前已工作的 `gpt-5.4-mini-2026-03-17` 与 Responses、项目凭据来源。此次不做模型选型或 API/Docker 历史排障。缺少私有 CAS 时准确报告该缺口，不扫描其他项目或应用凭据。

## 1. 起点事实：不是三次独立效果测试失败

已提交 live-02 结果：[R1][R2]

- 5 个独立本地 episode：3 Discovery / 2 Development；均从正常 Product 诊断进入 OPEN_WORLD，实验后恢复健康、queue lag 为 0。
- 调查 24 次调用、17 次模型选择的读取、13 条完整 read → result → followup 链。
- 3 个 UNRESOLVED，1 个 PROVISIONAL_SUPPORTED 数值内存趋势，1 个输出截断。数值趋势不证明故障因果；往返数不证明调查策略优越。
- 本轮 3 个知识请求分别在引用准入、expression 校验、引用准入阶段失败。没有 candidate 通过准入，因此没有 holdout、promotion 或 learned reuse。
- 新 campaign 的 `clean=true` 与旧 live-01 的 `BLOCKED_SAFETY / clean=false` 同时保留，不互相覆盖。
- 当前终态 `ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE` 正确，不追溯修改。

**本轮要区分：格式/引用失败、表达能力缺口、开发数据不匹配、独立效果失败。不能用一个“没学会”覆盖四种不同问题。**

## 2. A 阶段：先复现两个具体接口问题

### A1. 目标反证与跨服务背景必须有不同位置

已保留的请求 ordinal 39、51 均将其他服务的观测放入 `counter_evidence_refs`。ordinal 51 的 target 为 `fraud-detection`，其中两个 counter refs 指向 `checkout` 和 `kafka`。当前 `add_candidate()` 将支持与反证都限制为覆盖 proposed target 的可用观测，所以拒绝符合现行契约。[R3][R4]

这说明模型在尝试做跨服务比较，但不证明它对其他服务“健康”的文字判断正确。不能把任何真实存在的引用都当作正确推理。

最小改法：只对新的模型提议草稿增加显式证据角色，保留当前执行规则的单目标语义：

| 角色 | 含义 | 能否参与执行规则及准入支持 |
|---|---|---|
| target support | 同目标、同声明范围内的正向观测 | 仍须通过原检查 |
| target counterevidence | 同目标、同声明范围内的反向观测 | 仍须通过原检查 |
| comparison context | 其他服务或其他明确范围的背景比较 | 本版只用于解释和审计，不构成目标谓词证据 |

在输入视图中逐条标出实际服务、事件、来源、窗口、覆盖、是否截断、允许的角色。支持多目标的原始 observation 必须解析具体记录和覆盖信息，不能仅按 ref 字符串名称决定身份。

可以保留一个小的 `comparison_context` sidecar 或新草稿字段；不必修改原编译器的单目标条件语义。它必须绑定可见证据及其实际服务，不能引用未知或未暴露数据。上下文的文字解释仍标记为模型推断。

硬约束：

1. 跨服务 context 不计入目标的 source diversity、required_sources、数值预测、支持条款、准入结论或恢复权限。
2. 真正声称“其他服务健康”是规则触发条件时，不能悄悄放到不执行的 context；当前单目标 DSL 无法表达就返回明确表达能力缺口。不得假装已编译跨服务条件。
3. 不把旧被拒绝 proposal 删除几个 ref 后注册为“原模型成功”。旧请求、旧候选及拒绝结果原样保留。新草稿必须由一次真实模型请求产生。
4. 模型把错误目标引用放在 target 字段时仍然拒绝，不在模型输出后自动改写它的证据角色。
5. 支持和反证可以为空的地方允许真实为空，不能为填满结构而制造反证；没有足够支持就不能准入。

### A2. 把机械字段交给 Runtime，把规则选择留给模型

当前模型直接填较完整的 KnowledgeProposal/DerivedExpression，包括多个已知可机械推导的字段；当前 Responses 请求显式 `strict=false`。[R5][R6]

先实现一个最小、模型友好的 proposal draft → 确定性编译适配。复用现有存储和类型，不新建 Agent 平台、第二个知识库或通用编译框架。

**模型仍决定：** 模式描述、目标/成员、证据角色、需要的已有谓词组合、是否加入派生特征、字段/算子/比较符/开发数据支持的阈值、混淆条件与不可用时的弃答。

**Runtime 负责机械推导：** 原始 evidence ref 解析、schema/evaluator 版本、snapshot 绑定、单位校验、已选依赖对应的时间窗和采样字段，以及正式 ID/hash。

可以提供短 evidence/dependency 别名，但别名必须绑定当前 discovery snapshot、事件、目标、窗口和原始引用；禁止跨快照重用或只靠列表位置猜身份。模型选择哪个合法依赖，不由 Runtime 替它挑一个最容易通过的依赖。

不得由 Runtime：添加获胜谓词、替模型选阈值、删除不成立条件、偷偷转换无效单位、剪裁数值到合法范围、挑选更好的成员事件或强制加入 Level B。

草稿可以返回 `NO_CANDIDATE` / `NEEDS_OBSERVATION`，不是所有输入都必须吐出一条规则。新缺口不自动触发任意查询或新 live episode。

新输出身份要如实记录：原始模型草稿、确定性编译结果、映射快照和编译版本分别绑定。当前 provenance 检查依赖账本 proposal 与输入相等；新增适配必须有显式新版本的来源验证，不能伪造旧账本字段以满足旧相等比较。

### A3. strict schema 与失败诊断

在新草稿的 function schema 上优先使用兼容的 `strict=true`。先离线检查官方支持子集：对象的 additionalProperties=false，所有属性 required，可选语义通过 null 表达。不要把 Pydantic JSON schema 原样加 strict 就默认可用。[O1]

strict 只约束可表达的 JSON 结构；服务/事件对应关系、单位运算、窗口、覆盖和数据有效性仍由 Runtime 验证。自定义 Pydantic model_validator 不会因为启用 strict 就自动变成远端约束。保留拒答、截断和语义失败状态。[O2]

ordinal 50 只保留 expression/value_error 位置，原错误参数不可恢复；不要编造它究竟错在单位、分母还是窗口。[R3]

后续错误增加小的安全诊断：字段路径、稳定错误码、允许公开的枚举/数值类型信息、对应目录项以及请求绑定。必要的结构化参数投影只保存在私有 CAS，设长度上限并脱敏；不记录隐藏思维链或公开原始遥测。不可安全保存的原文就保留“未知”，不要强行落盘。

A 阶段最少回归：旧跨目标 target-ref 仍拒绝；新比较 context 可记录但不改变 matcher/准入；缺 target 证据不能靠 context 补齐；旧/新别名快照混用拒绝；错误单位/依赖/窗口拒绝；原证据和历史终态不变。

## 3. B 阶段：先用已有数据验证，不再重建实验环境

### B1. 数据使用边界

复用 live-02 的私有 CAS、已完成 session、原始 discovery/development role 与已登记 episode。不得重写旧 session 或给旧失败补一个新“成功答案”。新 proposer 请求使用新的 key、prompt/schema 身份和同一个预算账本。

这些五个事件已经可用于开发。本轮新请求是 **真实 Provider + 已见 live 数据回放**，不是新的实时事件，也不是盲测。重跑旧数据不增加独立分母。

保留不可见的 holdout/recurrence。确认跨候选 exposure 仍有效；没有 exposure 信息就不能声称某数据未见。不能用公开的最终结果说明、故障注入命令、control flag、预期标签或本文件中的具体失败候选作为新 proposer 的示例答案。

对于 truncated/failed 的事件，保留实际数据质量和停止原因；不能因为 Provider session 失败就推断所有已提交证据都无效，也不能把它悄悄标为完整成功。能参与哪种检查由证据逐项决定。

### B2. 真实有界提议

完成 A 回归后，只冻结一个新协议开发版本。最多 1 个初始候选 + 2 次语义修订；最多 6 次请求 / USD 1 的 B 子预算包括格式修复与失败。全部保留，不能为凑成功创建多个平行版本重复抽样。

开发反馈可以指出错字段、缺少哪个已合法声明的观测、哪条合取条件在已见开发数据上不满足；不能包含 holdout 结果。对不成立的语义规则，由模型决定修改或弃答，Runtime 不代写。

旧请求 ordinal 51 的 CPU mean < 2% 等参数仅是历史失败记录，不是要求新模型复现的答案。不能在 prompt、fixture、候选选择器或阈值搜索中预置这一规则。

### B3. 开发结果必须到“规则真的求值”这一层

逐层输出实际状态：

```text
模型请求完成
→ 草稿 schema 合法
→ 引用/角色/依赖准入
→ canonical candidate 可重建
→ 开发集确定性求值
→ 每个谓词与表达式的 TRUE / FALSE / UNKNOWN
→ 是否具有进入独立验证的资格
```

原有两来源和其他硬准入条件保留。只是“通过引用检查”不能当成开发效果通过；只是“JSON 可解析”更不是学会了。

同一个候选在全体已声明开发材料上评估，缺负例就明确缺负例。可以复用原计划中合法的健康/Core/混淆回放控制，但标清真实、回放、派生，不增加独立 live 数。不能只测提出该规则的正例或悄悄更换不利案例。

Level A 有效时如实报告 Level A，不为了原目标的 Level B 在规则里硬塞一个恒真或无关 CPU 条件。Level B 要证明表达式实际被求值、对合法输入变化有响应；这种检查是求值器正确性，不替代统计有效性或因果证明。

若 schema/ref 已修好但所有候选仍与开发数据不符，可以得出更有信息量的“开发无有效知识”。不要自动升级模型、增加表达语言或重新采样。只有存在新证据支持的具体实现错误时作有界修复。

## 4. C 阶段：开发通过后才继续独立验证

A/B 的 no-Docker 限制在此结束。进入 C 必须满足：真实模型来源候选准入，开发检查通过，原 Goal 所需控制材料可行，且模型/prompt/schema/候选/编译器/求值器/观测依赖/拆分和预先门槛均已冻结。

用户激活本文件后，允许在原 owned 场景范围内为一个新的验证 campaign 做必要创建、ready、受限实验控制及精确 cleanup；不自动改 Resource Saver、重启 Docker、修改系统网络或操作非项目资源。复用已验证的启动实现，不重新证明 API 连通性，也不重跑已经完成的 3 个 Discovery episode。

旧 live-02 已清理；不能复用旧对象 ID、冒充旧容器仍在。必须新建唯一目录/nonce，核对稳定性和旧清理结果后登记新基线。意外非项目漂移仍停止；新 campaign 失败不产生无限重置权限。

新数据与候选绑定同一个合法环境/能力语义。重新部署后若绑定不兼容，不把旧 candidate 的 environment/capability hash 替换成新值继续冒充同一冻结规则；先报告真实兼容性缺口。观测值可以变，来源、单位、读取语义和归属不能悄悄变。

在剩余最多 7 个 live episode 内完成原 Goal 尚缺的独立验证/复发与必要控制，至少保留未见的目标验证和复发各一例。控制可按原计划区分 live/replay，不准以减少负例来让候选过关。全部计入原 12 个总上限。

一次冻结评估；同症状、健康/Core、失败/覆盖不足和目标歧义等控制按原 Goal 执行并报告分母。若独立验证失败，本轮终止该候选的晋升；禁止看过结果再调阈值、改角色或追加挑选样本。没有独立验证通过就没有 Promotion。

通过后仅在隔离测试注册库由治理/harness 晋升，不给 LLM 审批权限。再用未见新事件走正常 Product 入口、同语义依赖采集、确定性 matcher，记录 LLM calls=0。检验撤销后不再命中。实验复位与清理不计作 Product 自主恢复。

## 5. 交付与终态

继续现有分支/PR，不 merge、tag、release 或部署生产。仅增加本次续跑文件和一个小结果包，例如 `docs/results/product-v050/knowledge-contract-repair/`；扩展现有 verifier，不为每个错误另建通用治理系统。

每个切片先跑聚焦回归；稳定最终 HEAD 再跑仓库要求的全量测试/CI。没有新代码或证据变化时，不反复做同一次全量收尾。

最终分别报告：

- 旧失败复现与原因；原反证/新比较 context 的确切语义。
- 模型实际选择了哪些条件；Runtime 仅推导了哪些机械字段。
- 每次真实请求的 schema、引用、依赖、开发求值状态及具体失败原因。
- Level A/B 各自有没有有效候选；有没有进入独立验证；不要混用这些状态。
- 数据角色和分母、实际 live 数、累计请求及承诺/未知费用、资源清理。
- 新增 Product 恢复写入仍为 0；历史失败证据、终态及预算均未改写。

完整 PASS 仍只能满足原 Goal 的全部条件后生成。B 修复后仍无有效候选，保留 `NO_VALIDATED_LLM_KNOWLEDGE` 并说明是协议、观测、开发效果还是表达能力问题；必要外部条件缺失用原外部阻塞状态；安全问题用原安全阻塞状态。不把本次接口里程碑命名为全链 PASS。

本文件给出的是一次有理由、有边界的新开发轮，不是“持续重试直到模型给出一个能通过的答案”。

## 6. 核对来源

- [R1] PR #104：`https://github.com/Raidriar7170/EcomSRE-Agent/pull/104`
- [R2] 本次真实结果：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/398b414b2561b27a94a9663bb8e0ba24627e984f/docs/results/product-v050/docker-stability/README.md`
- [R3] 调用记录（ordinal 39/50/51）：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/398b414b2561b27a94a9663bb8e0ba24627e984f/docs/results/product-v050/docker-stability/calls.json`
- [R4] 引用、依赖、来源校验：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/398b414b2561b27a94a9663bb8e0ba24627e984f/src/ecomsre/product/knowledge/evolution_v050.py`
- [R5] 现有模型 schema：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/398b414b2561b27a94a9663bb8e0ba24627e984f/src/ecomsre/product/knowledge/candidates_v050.py`
- [R6] Provider 的 strict=false、输出上限和请求身份：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/398b414b2561b27a94a9663bb8e0ba24627e984f/src/ecomsre/product/investigation/provider.py`
- [O1] OpenAI function calling / strict mode：`https://developers.openai.com/api/docs/guides/function-calling`
- [O2] OpenAI structured outputs / handling mistakes：`https://developers.openai.com/api/docs/guides/structured-outputs`

来源约束已有事实；新增接口、阶段和子预算为本次设计建议。执行时核对最新 HEAD 和账本，不以本文件取代实时状态。

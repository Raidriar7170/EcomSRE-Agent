# Goal：EcomSRE v0.5 — 完成知识验证、晋升与新事件确定性复用

> **Repository**：`Raidriar7170/EcomSRE-Agent`  
> **继续 PR**：Draft PR #104，不新建项目或 v0.6 分支  
> **Goal ID**：`ecomsre-v050-final-learning-closure-v1`  
> **核对起点**：`29c195762b7bf6f4951ca778b6a0a034f2be94fd`  
> **日期**：2026-09-18  
> **保存路径**：`docs/goals/EcomSRE_v0.5_Final_Learning_Closure_Goal.md`  
> **主目标**：真实模型候选 → 开发验证 → 冻结 → 独立 Shadow → 测试晋升 → 新事件正常入口零 LLM 复用  
> **边界**：不做 skill 对照，不扩大 Product 恢复写权限，不自动 merge/release/deploy。

本文是原 v0.5 Goal 的一次连续闭环开发授权草案。生成或下载本文不代表已经执行。只有用户在 Codex 明确激活后，才执行本文限定的调用、实验和分支更新。

---

## 0. 激活、优先级与连续执行

在原仓库、原 PR 对应 worktree 的 Codex 会话中激活：

```text
/goal 执行 docs/goals/EcomSRE_v0.5_Final_Learning_Closure_Goal.md，继续原 v0.5 与 Draft PR #104。我授权本文规定的一次连续开发与验收：先补足开发反馈和必要数据，再完成候选验证、冻结、独立 Shadow、测试晋升及新事件零 LLM 复用。保持当前模型、原累计账本和历史证据，不开发 skill 对照，不扩大 Product 恢复权限，不自动合并或发布。阶段切换无需重复请示；只在真实完成、用尽本文预算或出现具体外部/安全阻塞时停止。
```

执行规则：

1. 先核对当前 HEAD、PR、原 Goal、最新结果和本地账本。HEAD 已前移时读取增量，不回退、不覆盖用户改动；读取 CAS 必须找到真实保留路径。
2. 本文明确开启一个**新的有界开发轮**。过去已耗尽的 3 次提议轮保持耗尽；新的请求使用新轮次身份，不复用旧 key、重置 marker 或改旧失败结果。
3. 本文替代旧补充中“仅本轮 3 次语义尝试”及“先开发通过才允许补采”的阶段限制，**仅适用于这个新轮次**。原总预算、历史保护、权限和独立验证门槛不降低。
4. 开发数据不足时，可直接进入本文的有用途补采，然后返回同一开发轮。不再出现“补采要等开发通过，但开发通过又需要补采”的循环。
5. 普通测试失败、候选被拒、阶段结束、首次准入或首次开发通过，都不是交付终点。修复、评估和推进在同一 Goal/PR 中进行。
6. 出现新非项目资源漂移、真实权限缺失、预算触顶或不可恢复的数据缺口时，停止相关动作；不得为了继续而自行扩大权限、换模型或重置基线。
7. Product LLM 的权限与开发 Codex/隔离实验控制器不同。开发工具能操作 owned 实验资源，不表示运行中的模型获得 shell、Docker socket、晋升或写权限。

---

## 1. 接受的起点事实与准确的目标

### 1.1 已完成，不重复建设

核对提交的结果表明：[R1][R2]

- 已有 5 个独立本地事件：3 Discovery、2 Development；17 次模型选读、13 条完整读取—返回—后续响应链。
- 已有 `knowledge-draft-v050.2`：专属任务说明、请求绑定短句柄、目标/成员/角色枚举、严格输出与机械编译。
- 最新 3 次请求均格式有效；1 个真实 LLM 候选通过准入并进行开发求值。
- 该候选在原 Development 中为 **1/2**，全部已见事件为 **1/5**。最后一次提议执行条件重复。
- 当前终态为 `ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE`；未冻结、未独立验证、未晋升、未新事件复用。
- e04 缺少指定补查资源窗口。初始 10 秒资源快照不是 offset30/query30/sampling10/count5 补查的替代物。
- 尚缺独立的健康和混淆 Development 控制。此前 baseline、同故障的切窗和 fixture 不自动成为独立负例。
- Provider 为已工作的 `gpt-5.4-mini-2026-03-17` / Responses；累计 57 次请求、USD 0.918501 承诺上界、5 个 live episode。账单实际费用未知。

不要重做 Provider 排障、角色分离、短句柄、Docker Resource Saver 排障、另一套 DSL、另一套数据库或 Agent 框架。

### 1.2 本 Goal 的目标不是把某条旧规则改成绿色

目标是让**新的真实模型输出**完成以下过程：

```text
完整开发事实与反馈
→ 模型归纳/修订模式
→ 严格准入与确定性开发求值
→ 固定一个满足开发条件的候选
→ 未暴露独立事件与安全控制
→ 独立治理晋升到隔离测试注册库
→ 正常 Product API/Worker 处理新事件
→ EXTENSION_KNOWN + 对应 learned registration + LLM calls = 0
```

不把“代码中已有这些方法”当作已验证链路。尤其要提前实测冻结、数据拆分、派生控制、环境重建绑定和正常 matcher 的衔接。

### 1.3 Level A 与 Level B 的两种结果必须分开

- **Level A**：模型组合已有谓词。若通过独立验证并完成复用，可以明确报告 `learning_loop_status=PASS_LEVEL_A`。
- **Level B**：模型提出实际使用新声明式派生表达式的候选，表达式在独立输入上求值并被正常路径复用。原 v0.5 完整成功仍要求这一层及原 Goal 的其他条件。

优先完成有真实意义的 Level B；若只有 Level A 成立，允许把这条真实闭环做完，但终态仍为原来的**有限制交付**，不能改称 `ECOMSRE_PRODUCT_V050_ACCEPTANCE_PASS`。不为满足版本名称强塞恒真、无关或手写资源条件。

---

## 2. 一次性授权范围与预算

### 2.1 本轮允许的工作

- 原分支中的必要 Product/harness 修复、聚焦测试、文档、提交及同一 Draft PR 更新。
- 已见 Discovery/Development 的原始 CAS 回放、有限真实模型调查/提议/修订。
- 原已验证 owned 本地场景的必要启动、健康与负例采集、受限故障激活、恢复和精确清理。
- 固定规则冻结后的独立验证、派生安全控制、测试注册库的条件式晋升与撤销。
- 为本轮登记一次明确的新运行起点；只在旧 owned 资源清理和当前非项目状态核对通过后建立。

不允许：修改 Docker Desktop/系统网络/账号权限，停止非 owned 服务，全局 prune，升级固定镜像或 upstream，云资源开通，Product 自由写工具，生产/共享注册库晋升，自动合并发布。

### 2.2 预算是同一账本的子预算，不是充值

| 项目 | 本轮上限 | 原总上限 |
|---|---:|---:|
| 新增真实 Provider 请求，所有用途合计 | 40 | 200 |
| 新增费用/未知用量承诺合计 | USD 8 | USD 20 |
| 新增 live episode，含开发与验收 | 7 | 12 |
| 新候选语义尝试，跨 Level A/B 合计 | 6 | 本轮单独计数，不复活旧轮 |
| 正式独立评估批次 | 1 | 不允许失败后换候选重刷同一盲测 |
| 正式复发事件 | 1 | 保留其结果，不以重复采样代替失败 |

启动时剩余额度小于表中上限则使用更小者。参考起点对应原余额为 143 请求、USD 19.081499、7 episode；必须重读原账本。

范围选择、展开、调查、提议、协议修复、传输失败、截断和拒答全部计入请求与成本。输出截断后没有完整语义锚点，下一次候选生成仍占语义槽；不能叫“不计数格式修复”。重复候选也占一次槽。

模型保持 Mini/Responses，不静默升级。沿用已工作的 medium reasoning、8192 候选输出上限和成本预留；本轮不做模型排行榜或成本路由。实际计费表在执行时核对，未知 usage 保留承诺，不记为零。

6 个语义槽用于**同一个有反馈的开发过程**，不是六套平行 prompt。达到预先确定的主候选开发门槛后停止选优，进入冻结。范围内不足以产生可用候选时，保留明确的失败，不追加第 7 个。

---

## 3. Phase A — 先检查最后一公里，不先付费抽样

### A1. 从原记录重建候选失败和完整事实矩阵

使用既有求值器，恢复每个已见事件的：事件角色、目标、每个可用谓词的状态、数值样本、来源/窗口/覆盖、可部署依赖、前次候选逐条件结果和语义签名。

模型下一轮必须能够看到：

- 当前任务要求覆盖整个预先声明的目标模式，不是只解释某个偶发窗口。
- 过去候选**哪些条件在什么事件为 FALSE，哪些只是 UNKNOWN**。
- 来源数量按真实来源计数，不把三个 Metrics 指标叫三种来源。
- 被拒绝/重复的执行条件摘要、前次模型草稿及其开发反馈；短句柄重新绑定当前请求。
- 所有完整反证与观测缺口。不能仅保留有利的正例、成功 session 或 TRUE 单元格。

不要直接告诉模型“改成某某固定规则即可通过”。不使用本 Goal 中的文字、旧最终报告或注入器标签充当答案示例。可以指出已经真实测到的开发错误，由模型选择如何修订。

可运行一次既有 deterministic miner 作**离线可行性诊断**，但不是强制对照实验。若缺负例，报告其正常拒绝；不另搭评测项目。miner 输出不冒充 LLM 来源，也不把获胜 clause 填入新模型草稿。

### A2. 做一次端到端机械预检，避免盲测时才发现接口不通

用明确的 fixture 对象走通：准入 → 开发 → 锁定 → freeze → evaluate → promote → 正常 API/Worker diagnosis → revoke。

机械预检不得注册到实际学习测试库，不用真实候选假冒模型输出，也不消耗或读取真实 holdout。重点核查以下已知接口边界：

1. `check_development()` 对候选的开发检查是一次性消费；先确定完整开发清单，再进行正式开发检查。新增数据后不得覆盖旧 `CHECKED`，应建立有父版本的新候选/开发记录。[R3]
2. `freeze()` 需要已存在的病例/诊断/补查摘要，但候选必须在采集或查看 holdout 前锁定。用一个小的**候选选择锁**先固定规则和协议；采集后再封存数据 manifest，禁止期间更改规则。[R3]
3. `evaluate()` 的现有 v0.5 入口主要接收持久事件，而旧 Shadow 合约还支持派生反事实、来源失败控制。实际接通这些控制，而不是到评分时填写 `CONTROL_NOT_AVAILABLE`。[R4][R5]
4. `promote()` 要求 VALIDATED、已登记的隔离测试环境、且不能覆盖已有 ACTIVE 冲突；模型工具中不得暴露该方法。[R4]
5. 正常 matcher 必须取得候选依赖的当前事件观测，不能只有 Shadow 脚本能读取。

### A3. 提前处理重建环境后的身份衔接

不能假定换一套 Docker 容器后，旧 candidate 自动能用于新事件。当前 capability 摘要包含 `verified_at`；再次验证会改变原始摘要，即使可观察能力相同。开发、评估和匹配目前又有精确 capability 绑定。[R3][R6]

优先保持同一逻辑 environment/服务身份，通过现有验证流程建立本次 deployment binding。保留旧事件的原始 environment、capability、baseline 和资源身份。

若确实只因重新验证时间或 owned 容器出生身份变化而无法衔接，本文允许一个**仅限本轮、显式且有测试的同环境 successor 适配**：

- 保留旧/新原始摘要与完整对象，不改历史哈希算法、结果或旧事件。
- capability 结构逐字段比较时，仅允许 `verified_at` 变化；环境、服务集、来源、覆盖、谓词能力和 effective policy 等仍需一致。
- 容器/网络身份变化只在独立 owned deployment binding 中表示。镜像、服务映射、查询模板、单位、窗口、采样、信任边界及连接器语义必须核对一致。
- 额外核对实际查询和资源限制，不能因为 capability 表中没有写查询内容，就把不同查询当成等价。
- 兼容绑定由隔离 harness 建立，绑定确切旧/新摘要和实验身份；开发、Shadow、正常 matcher 共用同一检查。LLM 无权生成或扩大兼容关系。
- 允许的映射在候选锁定前冻结；source 不可用、未知摘要、能力/单位变化仍然安全失效。

这是对同一测试环境部署更新的显式处理，不是允许跨任意环境复用或忽略 capability 检查。若需要新逻辑环境且无法安全保持数据来源与规则绑定，给出具体差异；不把旧事件换 environment_id 搬进新库充当同一数据。

### A4. 阶段出口

交付一页 run plan：可用材料、缺口、未来七个 episode 的角色、两级开发门槛、Shadow 必需控制、兼容方式和冻结顺序。必要机械测试通过后，直接进入数据和开发过程，不在这里收尾。

---

## 4. Phase B — 先补负例与可用前向数据，再进行完整开发轮

### B1. 不修复过去，增加明确的新观察

e04 的过去补查窗口已不存在，不能用现在的资源数据填回。它仍属于原 Development，历史缺口和 1/2 结果保持原样。

新数据只代表新的事件。本文允许在新候选产生前补采，因此新增数据之后仍可使用本轮剩余语义槽，不再被“旧轮已耗尽”卡住。

### B2. 预分配剩余七个 live 槽位

以下为逻辑角色；实际 ID 由 Runtime 创建，控制真值只在隔离 evaluator 中保留。新 episode 名称不得向运行模型泄露机制/答案。

| 新槽位 | 阶段 | 用途 |
|---|---|---|
| N1 | Development positive | 必要时补一个目标模式的完整前向资源事件，用于 Level B；不是修复 e04 |
| N2 | Development negative | 一次独立健康、同量级工作负载和同语义观测的事件 |
| N3 | Development confusable | 使用现有授权控制制造一种可执行的相似症状/已知故障；事先说明混淆维度 |
| N4 | Holdout positive | 候选锁定后首次取得的独立目标事件 |
| N5 | Holdout healthy | 候选锁定后首次取得的独立健康控制 |
| N6 | Holdout confusable/core | 候选锁定后首次取得的独立混淆或 Core 控制 |
| N7 | Recurrence | 独立验证与晋升之后的新事件，正常入口零 LLM 复用 |

N1 仅在 Level B 需要时执行；不需要则保留额度，不为凑数量运行。不得消耗 N4–N7 给开发重试。健康 episode 也计 live 槽，fixture/同事件变体不计独立事件。

N2/N3 是当前缺失的开发负例，不能被旧健康基线或任意合成数字替代。混淆场景使用已有受控操作，不能新开任意 fault/shell 权限；若只能复现相似 latency 而不能复现相同 queue lag，按真实范围描述，不夸大。

若其他必要控制可由已封存且未向学习器暴露的合法回放提供，事先登记来源；不得将已暴露的开发材料改称 holdout。

### B3. 采集约定

- 复用已验证的镜像、22-service 必要依赖集合及启动修复，不重建 upstream。
- 新建唯一 campaign 目录与 nonce，不覆盖 live-01/live-02。当前所有权/状态核对通过后建立新基线，不自动修改 Resource Saver 或系统网络。
- 一次 campaign 可以包含开发、冻结、验证和复发阶段；普通阶段切换不强制销毁并重建环境。长期暂停需要清理时如实记录，下次恢复需重新核对绑定。
- 每个故障 episode 都有独立的健康起点、受限条件、遥测、恢复和健康结束；同一持续故障切窗不增加分母。
- 所有需要的采样模板在调用 proposer 前确定并记录；完整资源、Metrics、Runtime 及控制所需来源尽可能同语义采集。不存在的来源就记缺口。
- 固定的评估采集与 LLM 选读分开记数。固定采集不是模型主动调查；必要的新目标调查可沿用每 session 10 calls / 8 reads 的已有上限，所有调用仍在本轮总预算内。
- 不为了取到“好数据”提高来源上限、删除失败样本或调整 detector。工程错误可以修复并保留失败，已开始的无效 episode 仍占本轮消耗，不用新名字免费重试。

### B4. Level B 前向开发清单的准确语义

在看到任何新候选结果前，声明：原 e01–e05 仍全部报告；e04 的指定 Level B 依赖记为历史 `UNKNOWN / NOT_COLLECTED`。

Level A 仍保留原 e04/e05 的 2/2 要求。Level B 使用**预先声明的前向完整数据清单**，最低包括旧 e05 与 N1 两个独立正例，并对其他有同语义完整资源的旧事件一并求值。

这不删除 e04，不声称旧 1/2 已变为 2/2，也不恢复旧轮成绩。新 Level B 队列、原队列和覆盖率分别报告；若新候选需要的依赖不在该预先声明清单中，不能临时挑更有利的事件替换。

其 expression-free 基础条件还应在原 e04/e05 上真实检查，防止只靠换队列掩盖旧的稳定性问题。完整 Level B 结论必须披露其观测适用域。

---

## 5. Phase C — 让模型根据失败反馈修订，而不是反复重新猜

### C1. 输入与反馈

保留现有短句柄/strict wire schema。新增或复用一份精简的开发包：

```text
合法目标与成员范围
逐事件/逐来源事实和 FALSE / UNKNOWN 区别
当前已实现谓词、来源类别及可用依赖
前次完整草稿、canonical 语义签名及其逐条件结果
健康/混淆控制的已见观测与开发反馈
当前已尝试的语义签名和拒绝原因
合法的新提议、修改或弃答方式
```

不需要让模型阅读全部旧 Goal、Git 历史、原始日志或长哈希。控制真值、注入配置、正确诊断/规则以及任何 holdout 内容不进入 proposer。开发标签若用于反馈，只能来自可信的已见标注流程，不能将模型自己的解释当作真值。

两来源要求应明确显示在输入/可检查约束中，但不能强制附加不成立的资源谓词凑来源。引用存在不等于来源条件成立。

### C2. 允许的反馈循环

```text
真实模型提议
→ schema / 来源 / 引用 / 依赖检查
→ canonical 重建
→ 同一既有 evaluator 在完整声明开发集上求值
→ 每个条件与表达式的 TRUE / FALSE / UNKNOWN
→ 模型选择修改、说明适用域或弃答
```

模型仍决定目标、成员、谓词组合、算子、比较符和阈值。Runtime 不替它删除 memory-growth、不补一个健康条件、不裁剪阈值，也不提供一条保证通过的 clause。

前次候选已经明确失败时，下一次请求必须携带可定位的失败事实和旧候选语义，而不只返回“未通过”。模型可以保留自己的判断，但执行条件相同仍是重复，不能通过改名或改解释创建新的有效候选。

### C3. 同一轮六个语义槽的使用

候选来源与修订父关系持久化。每次 dispatch 使用稳定的新 key；已完成请求重启后读取结果，不再付费调用。缺少完整语义的失败按一个槽计数。

单次语义重复给出原候选和结果，下一槽可基于明确反馈修改；连续两次相同重复或同一错误且无新增诊断信息时提前停止，不继续盲抽。获得一次准入并不停止；达到完整主开发门槛才进入冻结。

### C4. 预先确定的开发门槛

所有候选首先保持原准入、两来源和覆盖要求。健康/混淆负例不能靠 `UNKNOWN` 算作正确排除。

**Level A：**

- 原 e04/e05 为 2/2 TRUE。
- 全部原 5 个目标事件至少 4/5 TRUE，UNKNOWN 计为未覆盖，不删除分母。
- 新增完整目标开发事件适用时为 TRUE；N2/N3 以及声明的其他开发控制，完整数据条件下为 FALSE。
- Core/现有 Extension 的合法结论不被覆盖；无独立特异性证据时不能进入正式验证。

**Level B：**

- 前向固定完整数据清单中的 e05/N1 两个正例均 TRUE；全部同语义、完整的已见目标事件至少 75% TRUE，逐条报告缺失者和原 5 例总覆盖。
- 原 e04/e05 上的可求值基础条件均为 TRUE；完整表达式在旧 e04 上仍按实际 UNKNOWN 报告，不补造过去观测。
- N2/N3 和其他声明控制在满足来源/依赖覆盖下为 FALSE。
- 表达式实际读取数值并影响求值；给出预先定义的合法数值边界/反事实测试，不能只是 schema 中有 expression 或恒真占位。
- 数值反事实测试只证明表达式行为，不声称新增统计泛化或因果发现。没有必要强迫其相对 Level A 提升固定百分点。

这些是**新轮次预先门槛**，不是重算旧轮。不得在看到本轮结果后降低门槛。

### C5. 主候选选择与停止选优

Phase A/B 根据数据可行性预先声明主目标为 Level B 或 Level A，不根据 holdout 结果选级别。

- Level B 数据路径可行时，优先首个达到上述 Level B 开发门槛的候选，立即锁定。
- 如果只有 Level A 达标，可以保留为候补；本轮剩余槽可继续尝试主目标 Level B。六个槽结束后仍无合格 B，则在看任何 holdout 前，固定最早通过开发的 Level A 为本轮唯一主候选，并将最终上限声明为 `PASS_LEVEL_A / LIMITED`。
- 没有任何候选通过完整开发门槛则不运行盲测，不把 miner 或人工规则顶替为 LLM 成功。
- 主候选选择锁建立后，本轮禁止进一步 proposer 调用、候选替换、阈值调节和新 prompt 选优。

---

## 6. Phase D — 一次独立验证，派生安全控制不伪装为独立事故

### D1. 两步冻结

**先锁候选，后封数据：**

1. N4–N6 采集前，封存真实模型请求、草稿、canonical 候选、来源、逻辑环境/兼容绑定、prompt/schema、读取依赖、求值器和开发报告。
2. 同时固定场景、角色、数量、变体变换、评分标准和实例采集时间范围；不能看到结果后选择病例。
3. 由隔离控制器采集 N4/N5/N6。只允许运行诊断/评估所需读取，不让 proposer 或开发反馈系统看到证据和标签。
4. 固定全部病例、原始对象、诊断和补查摘要，再调用真正的 freeze/evaluate 流程。候选/求值代码不能在两次冻结之间改变。

采集脚本可以检查来源是否成功，但不能按候选匹配结果挑好样本。无法取得必需数据记为受限或失败，不以新样本偷偷替换。

### D2. Shadow 必需项与当前门槛

当前 `evaluate_shadow_gate_v1()` 明确要求：[R4][R5]

| 检查 | 当前要求 |
|---|---|
| 正例 | 必须有；recall ≥ 0.75 |
| 负例整体 | FPR ≤ 0.10 |
| Core 混淆控制 | 必须有；重叠率 0 |
| 健康控制 | 必须有；误报 0 |
| 目标反事实 | 必须有；一致性 ≥ 0.80 |
| 来源失败 | 必须有；全部 fail-closed |
| 证据引用有效性 | 1.0 |
| 正例来源可达性 | 1.0 |
| 动作权限违规 | 0 |

不修改旧 gate。新批次额外要求真实健康/混淆事件可判定 FALSE，不能把数据缺失当成特异性成功。N4 只有一个独立正例时，1/1 仅是本地小样本验收，不是普遍召回率保证。

有相关 ACTIVE Extension 时需要冲突检查和对应控制；新隔离库确实没有其他扩展时，按既有合约记录 NOT_AVAILABLE 与原因，不删除用户已有规则。

### D3. 派生控制的接入方式

优先复用现有 `DERIVED_COUNTERFACTUAL`、`DERIVED_SOURCE_FAILURE` origin；若 v0.5 包装方法不接收它们，做小的类型化 adapter，在相同求值器上执行。[R5]

- 目标反事实：对固定目标/归属作受控改变，验证原目标不能因别的服务证据被误判。
- 来源失败/覆盖不足：同时正确改变状态、记录、coverage 和可用引用，清除不再有依据的谓词后重新构建输入；不能保留原成功谓词。
- 可补充固定的歧义、部分覆盖和表达式边界检查；变换在候选锁定前声明。
- 每个变体绑定其原始父事件、变换版本和 lineage。**不能给它新 episode ID 后声称独立。**
- 独立性要求应用于 N4–N6 等原始 episode；派生控制单列，不能绕开 `require_independent()` 把变体混进独立病例清单。
- 变体结果必须来自实际 evaluator，不手填 `matched=false` 或伪造预期通过。

复用相同 Shadow gate 汇总时，保留其历史口径，同时额外报告独立原始病例与每类派生控制分母。不得通过加入大量容易通过的变体稀释真实误报。

`UNKNOWN` 可以在匹配层表现为“不命中”，但结果必须保留 raw UNKNOWN、原因和来源状态；不把它改写成“确认没有故障”。

### D4. 正式评估后的规则

仅评估一次。独立验证失败则保留该规则的失败，不允许根据失败样本换阈值、换规则、选择候补或追加新的“第一次盲测”。工程缺陷导致测量无效时如实标记，不在本轮重试成 PASS。

只有验证通过，才能进入测试晋升。成功终态不能由模型直接填写。

---

## 7. Phase E — 测试晋升与真正的新事件复用

### E1. 晋升不是手动往表里插 ACTIVE

使用正常治理方法，检查 VALIDATED、test-environment enrollment、原始 LLM provenance、开发/Shadow 摘要、无冲突和固定环境/能力绑定。

本文激活提供**只限本次隔离测试注册库**的条件式预授权，记录 `PREAUTHORIZED_TEST_PROMOTION` 及本文/激活来源；不伪称用户后来逐条人工审核。模型看不到晋升工具或审批凭据。

任何先插 ACTIVE 再补验证、手改候选 payload、跳过 source guard 的做法均不成立。Level A/B 的来源级别按实际已验证规则记录。

### E2. N7 必须在晋升后由正常入口完成

N7 未用于发现、开发或 holdout，也未进入任何检索/模型上下文。独立恢复健康后重新触发受限条件，再执行：

```text
正常 Product incident / diagnosis job
→ Worker 正常证据获取
→ ACTIVE learned registration 加载
→ Runtime 获取本事件所需依赖
→ 同一个 deterministic evaluator/matcher
→ EXTENSION_KNOWN
```

必须同时证明：

- 命中 registration 就是本轮已验证模型规则，不是旧 `kafka-queue-backlog`、Core 或另一个手写扩展。
- 当前事件引用和依赖来自 N7，不是复用 discovery 的值、缓存旧诊断或只换 incident 名称。
- 对该 episode 的 Product 调查/提议调用为 **0**。关闭其他并发付费任务，保存全局账本前后计数与事件级记录；不能仅打印一个手填零。
- 显式禁用本事件 Provider/调查后依然能命中；不是“先让模型算好再隐藏调用”。
- 诊断通过正常 API 查询得到，不是测试直接调用 `evaluate_candidate()` 后手工构造结果。
- 环境、目标、时间窗、capability/部署兼容映射通过同一授权检查。

本次复发不通过就报告复用失败，不改规则或追加复发样本挑成功。

### E3. 撤销与不匹配安全性

复用成功后，使用测试治理撤销并核查正常加载器不再返回该 registration。对已封存 N7 证据进行**明确标为回放的**撤销后匹配检查，不能改变旧诊断；不额外消耗独立 episode 来证明缓存查询发生变化。

测试未知 capability、来源不足、错误环境和已撤销规则不会命中。不要误把已缓存的旧 diagnosis 当作撤销后重新求值。最终测试注册库可留 REVOKED；成功晋升与复用的历史证据照常保留。

完成实验恢复和 birth-bound 精确 cleanup；模型未执行恢复，不能将实验复位写成自主修复成果。

---

## 8. 工程纪律：只修真实断点，不再建设新的平台

- 以 `product/knowledge`、调查适配和现有 `scripts/product_v050` 为主，优先复用 `knowledge_feasibility`、constrained proposal、live_product、现有 verifier。
- 新功能名、命令名先实现并实际测试再写入 QUICKSTART。本文件中的 selection lock / cohort / compatibility binding 是设计要求，不声称当前已有这些接口。
- 可有一个小的 closure runner：审计、采集、提议、开发、锁定、评估、晋升、复用、清理；复用原 SQLite/CAS 和进度文件，不再造通用调度/审批系统。
- 追加旧 split 的未来角色时保留原映射与旧 manifest，形成精确前向 successor；不能覆盖已暴露/冻结的角色、改变旧 episode 身份或绕过全局 exposure。此适配必须在任何新数据消费前测试完成。
- 不修改冻结 DTA、旧结果和原始 evidence。必须更新当前文档 successor 摘要时只更新既有允许条目，不扩大历史白名单。
- 中间修复只跑聚焦测试；完整测试期间固定 HEAD/worktree。最终稳定提交才运行必要全量/CI，不每个模型失败都再做一次庞大收尾。
- 一名独立 reviewer 核查原始 CAS、LLM 来源、开发结果、冻结顺序、Shadow 控制、正常入口复用、预算和权限。独立审查不能靠再问一个模型“合理吗”代替求值。

必要回归覆盖：任务反馈确实进入 wire input；重复条件不新建知识；源缺失不是 FALSE 事实；新/旧 cohort 不混淆；派生控制不膨胀独立分母；时间戳 successor 不放宽语义；候选锁后禁止提议；正常事件零 Provider 复用；撤销后加载失效；所有新增外部恢复写入仍为零。

---

## 9. 产物与验收终态

### 9.1 只增加一个闭环结果包

建议目录：`docs/results/product-v050/final-learning-closure/`。可合并相近文件，但应包含：

- 输入审计、固定数据角色/场景、开发门槛与本轮消耗计划。
- 每次真实模型草稿、canonical 来源绑定、语义签名、开发反馈与逐条件结果的安全投影。
- 候选选择锁、独立数据冻结、Shadow 原始/派生分母与失败详情。
- 测试晋升记录、N7 正常 API/Worker trace、对应 registry 及零调用证明、撤销检查。
- 新旧环境/能力适配的必要核对；原账本累计、成本已知性、实际 owned 清理状态。
- 机器结果 `acceptance.json` 和一份简短 README；更新原 progress/STATUS/架构说明。

保留原始数据私有，公开报告不泄露凭据、控制真值或原始日志。安全投影与原始 CAS 使用不同摘要；不能拿公开删敏占位符冒充模型原文。

### 9.2 结果字段至少包括

```text
base_head / delivery_head / goal_id / pr
candidate_origin / request_key / candidate_semantic_sha256
protocol / model / scope_level / observation_domain
original_development_results / forward_development_results
historical_missing_cases / all_seen_coverage
candidate_locked_before_holdout / frozen_identity
holdout_original_episode_count / derived_control_counts
shadow_gate / source_failure_raw_states
promotion / registration_id / registry_version
recurrence_incident_id / normal_api_result / matched_registration_id
recurrence_provider_call_delta / event_llm_calls
revocation_check / capability_mismatch_check
learning_loop_status / full_v050_acceptance
requests_new_and_cumulative / committed_cost / invoice_known
live_new_and_cumulative / owned_cleanup / non_owned_drift
product_recovery_writes / terminal / limitations
```

### 9.3 合法结果，不强行成功

**完整成功**：真实 Level B 候选完成开发、独立验证、测试晋升及 N7 正常路径零 LLM 复用；原 v0.5 的调查行为、证据、控制、兼容、预览、审查和清理条件也全部成立，才可输出 `ECOMSRE_PRODUCT_V050_ACCEPTANCE_PASS`。

**Level A 闭环成功**：相同独立流程和复用成立，但仅使用既有谓词；记录 `learning_loop_status=PASS_LEVEL_A`、`full_v050_acceptance=false`，终态使用 `ECOMSRE_PRODUCT_V050_ENGINEERING_COMPLETE_WITH_LIMITATIONS`。这是有价值的实测成果，不是假装完成 Level B。

**无有效知识**：预算内无合格开发候选、独立验证失败，或不能证明学到规则有效，保留 `ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE` 并注明具体层级。

**外部/安全阻塞**：使用原 `BLOCKED_EXTERNAL` / `BLOCKED_SAFETY` 终态。说明究竟缺什么、哪些工作已完成和哪些未尝试，不泛泛请求“继续”。必要收集仍在本文范围且额度允许时，应先完成，不把已授权阶段切换当阻塞。

代码测试全绿、first admission、2/2 开发通过、Shadow 方法返回对象、注册库存在 ACTIVE，都不能单独构成 Goal 完成。闭环通过不证明生产普遍有效，也不自动获得恢复权限。

---

## 10. 最终回复用户的格式

```text
Verdict / 实际终态 / HEAD / PR：

本轮模型实际修订了什么：
Runtime 只做了哪些机械计算：
Level A / B 实际完成到哪一层：
原开发队列与前向队列：
独立验证（原始事件/派生控制分别列分母）：
晋升到哪个测试 registration：
N7 正常入口输出 / 命中规则 / LLM calls：
撤销与清理：
新增与累计请求、承诺费用、live episode：
未完成项及原因：
已测试的只读验证/演示命令：
```

同一个 Draft PR，除非用户另行明确授权，不 merge、tag、release 或部署。

---

## 11. 核对来源与不确定性

本 Goal 的预算、七槽分配、新轮次开发门槛和前向衔接是本次设计，不是已获得结果。本次核查读取了 GitHub 源码/结果和前序 Goal，没有在用户本机执行 Provider、Docker 或私有 CAS 求值。

- **[R1]** PR #104 当前结果与 HEAD：`https://github.com/Raidriar7170/EcomSRE-Agent/pull/104`
- **[R2]** 起点真实结果：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/29c195762b7bf6f4951ca778b6a0a034f2be94fd/docs/results/product-v050/knowledge-feasibility/README.md`
- **[R3]** 开发一次性消费及冻结：同提交 `src/ecomsre/product/knowledge/evolution_v050.py` 的 `check_development()` / `freeze()`。
- **[R4]** 独立评估、晋升和撤销：同文件 `evaluate()` / `promote()` / `revoke()`。
- **[R5]** Shadow 门槛与 origin：同提交 `src/ecomsre/product/knowledge/runtime.py`、`contracts.py`。
- **[R6]** capability 身份：同提交 `src/ecomsre/product/environment/capabilities.py` 的 `EnvironmentCapabilityMatrixV1`。
- **[O1]** Codex Goal/命令参考：`https://developers.openai.com/codex/cli/slash-commands`
- **[O2]** Function calling / strict schema：`https://developers.openai.com/api/docs/guides/function-calling`

**闭环的证明必须是：规则由真实模型提出，经独立结果验证，再通过正常运行路径改变下一次事件的处理；不能只是一连串“检查通过”的报告。**

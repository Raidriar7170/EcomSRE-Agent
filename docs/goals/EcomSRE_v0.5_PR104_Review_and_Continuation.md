# EcomSRE Product v0.5 — PR #104 定向修复与真实验收续跑

> 文档版本：1.0 · 2026-09-17  
> Repository：`Raidriar7170/EcomSRE-Agent`  
> 原 Goal：`docs/goals/EcomSRE_v0.5_Codex_Goal.md`  
> 原 Goal ID：`ecomsre-product-v050-llm-investigation-knowledge-v1`  
> 续跑对象：已有 Draft PR #104，不新开产品版本或平行实现  
> 本次核对 HEAD：`cd086826b23b728c6527b8b6981aeffef489c4e7`  
> 核对 base：`550a564d29954e6f3c2395790294e23800231ac4`  
> 分支：`codex/product-v050-llm-investigation-knowledge`  
> 建议保存路径：`docs/goals/EcomSRE_v0.5_PR104_Review_and_Continuation.md`

## 0. 定位、证据等级与激活

这是原 v0.5 Goal 的范围内补充，不是重新开始的实验，不增加预算，不修改既有失败或限制终态，不授权合并和部署。

截至核对 HEAD，PR 和 `docs/results/product-v050/acceptance.json` 均明确记录：

- 终态为 `ECOMSRE_PRODUCT_V050_ENGINEERING_COMPLETE_WITH_LIMITATIONS`。
- Provider 请求 0，新增 live episode 0，遥测证据 `FIXTURE_ONLY`。
- 真实模型知识提议、独立留出检验、学习规则晋升、新事件复用均未完成。
- 恢复只读预览不具备执行权限；PR 仍为 Draft。

PR 报告本地全量测试 6584 passed / 21 skipped，新增 fixture 测试 43 passed；本次核对看到该 HEAD 的两项 GitHub Actions workflow 均为 success。这些不等于真实调查—学习—复用闭环通过。

本补充中的代码问题来自对该 HEAD 的定向静态审阅，没有在用户本地重新运行完整测试或 Provider。必须先由 Codex 建立回归测试验证触发条件；若本地代码已经修复，应以具体测试和差异说明关闭问题，不重复造代码。

激活示例：

```text
继续已有 PR #104，执行 docs/goals/EcomSRE_v0.5_PR104_Review_and_Continuation.md，仍受原 EcomSRE_v0.5_Codex_Goal.md 约束。先验证并修复文档列出的证据绑定和学习数据通路问题，再定位本项目既有 Provider 配置、核对实际计费并完成有界真实验收。沿用同一个累计预算，不开发 skill 对照，不扩大恢复写权限，不自动 merge/release。保留原有限制终态与失败记录，用追加证据报告本轮结果。
```

生成或保存此文件本身不构成外部执行。只有用户明确激活后，才按原 Goal 已有授权推进真实请求、owned 本地实验和同一个 Draft PR 的更新。

---

## 1. 继续方式：不要回到“再建一套框架”

先读取原 Goal、当前 `AGENTS.md`、适用的 `docs/DECISIONS.md`、PR diff、进度、acceptance、preflight 和 checks。

保留：Core/Extension 确定性快速路径、原有诊断准入、Typed Observation、覆盖语义、CAS、现有候选池、beam search、Shadow、测试注册库治理、恢复只读预览。

不新增：skill 对照、模型排行榜、多模型路由、RL、训练框架、通用工作流平台、Kubernetes 迁移、任意代码表达式、新的生产写工具。

启动时记录实际 HEAD。分支已前移时审查后续 diff，不强制回退；已有未提交改动不得覆盖。继续原 worktree 和 PR，或安全附着到该分支。不要通过创建新数据根、数据库或“新 campaign”重置累计消费。

本文不修改原 Goal 的成功条件。缺少独立真实事件、有效 Level B 模型候选或确定性复用时，仍不能宣布 `ACCEPTANCE_PASS`。

---

## 2. 付费批量运行前，先核对四项具体问题

### R1 — 暂定支持结论必须绑定同一个假设

**源码观察**：`investigation/runtime.py` 对 `PROVISIONAL_SUPPORTED` 分别检查：

```text
是否有任一 hypothesis 带 support
是否有任一 prediction 的 Runtime 结果为 TRUE
```

这两个存在性判断没有要求落在同一个 hypothesis 上。当前 `InvestigationDecision` 也没有显式的最终支持假设 ID。

**待验证反例**：

- H1 带支持引用，但其编译预测结果为 FALSE 或 UNKNOWN。
- H2 没有支持引用，但一个资源数值测试为 TRUE。
- 不应仅因为 H1 满足第一项、H2 满足第二项，就把整个调查记录为已支持结论。

**最小修复**：

1. 返回或由 Runtime 计算明确的 `supported_hypothesis_ids`；不一定增加新的模型必填字段。
2. 对每个进入该集合的假设，独立检查支持证据、被检查预测、目标、窗口和引用归属。
3. 不能跨假设拼接支持与测试结果；检查结果为 FALSE/UNKNOWN 的同一假设不能借其他假设通过。
4. 记录仍未排除的替代解释。一个数值测试通过仅支持相应可观察预测，不自动证明整段机制解释的因果性。
5. 继续保留纯假设、主动弃答、观测不足等合法结果，不为提高成功率强迫模型作答。

**必要回归**：同假设通过；跨假设拼接拒绝；错误目标/窗口拒绝；FALSE 与 UNKNOWN 不通过；通过的数值预测不能被公开报告升级为因果确认。

### R2 — 补查得到的证据，必须能被后续知识检验和复用按同一契约消费

**源码观察**：

- `InvestigationReads.read()` 将新增连接器结果写入 CAS，调查 session 保存 `investigation:<sha>` 等引用。
- `discovery_view()` 把 session 的 observations 提供给知识 proposer。
- `check_development()` / `evaluate()` 则主要从原始 diagnosis evidence 的 connector snapshots 重建规则求值输入。
- 正常 Extension 匹配也使用正式诊断 acquisition snapshots。

因此，调查模型“已经看到某项补查证据”不等于规则开发检验或后续事件必然也具备该证据。依赖初始诊断已有字段的规则可能工作；依赖新增读取、不同窗口或更完整采样的规则需要额外验证。

**必须先增加端到端反例**：

```text
初始诊断缺少候选规则需要的数据
→ LLM 选择一次合法补查
→ 补查 Observation 持久化
→ proposer 基于该观测产生候选
→ 开发检验能在合法绑定的观测上求值
→ 独立事件按同样的观测要求取证
→ 晋升后新事件不调用 LLM，也能取得并检验该规则需要的数据
```

另加一个没有补齐必要数据的分支，必须得到 UNKNOWN/不足，而不是 FALSE、健康或伪造匹配。

**实现约束**：

1. 将必要的原始观测引用和查询/窗口要求纳入候选依赖，或者证明现有 acquisition 明确覆盖这些依赖。
2. 开发检验使用补查证据时，必须从 CAS 和持久化记录验证来源、环境、事件、目标、时间、完整性；不能直接相信模型输出或未经校验的 JSON。
3. 对后续事件，使用预定义的确定性读取契约取得数据；不能靠再次调用 LLM 才能匹配 ACTIVE 规则。
4. 不把某个旧事件的调查结果复制为新事件数据，不合并不兼容窗口，不通过修改只读历史 diagnosis 来接线。
5. 同一事件多个 Resource 观测存在时，按候选声明的查询/窗口依赖选择；确实有矛盾时保留 UNKNOWN，不能简单“取第一条”。
6. 没有可实现的依赖时，将候选标为不可部署/观测缺口，而不是允许晋升后永久失配。

优先通过 Product adapter 扩展现有证据投影和依赖检查；不重写冻结 DTA 核心，不建设第二套 Evidence Store。

### R3 — 将完整覆盖下的缺席作为“有范围的负证据”，而非放行任意空结果

**源码观察**：`validate_hypothesis_evidence()` 对 support 与 against 使用同一检查：要求 `SUCCESS_NONEMPTY`、未截断、覆盖目标且有目标记录。这能挡住无数据的假证据，但没有表达“完整查询范围内，指定现象确实没有出现”的负证据路径。

**修复目标**：模型能够根据合法负证据排除假设，同时不将一般空结果、抓取失败或覆盖不足误当反证。

- 正向引用继续要求实际支持观测。
- 负向引用使用显式的检验/缺席语义，绑定查询模板、所检验现象、目标、时间窗和完整覆盖依据。
- `SUCCESS_EMPTY` 本身不等于 `ABSENT_WITH_COMPLETE_COVERAGE`。
- 数据源失败、部分覆盖、截断、查询不匹配都只能为 UNKNOWN/来源失败。
- 对已有非空记录中可计算的反例，也应保留正常路径。

**必要回归**：完整范围内指定现象缺席可排除对应假设；同样空结果但查询太宽/不匹配、覆盖不足、超时不得成为负证据。

若本版有限查询模板不能证明某类日志缺席的完整性，明确不支持该类负证据，不削弱门槛；至少验证现有可证明的数值/状态反例。

### R4 — 正式盲测前把数据拆分隔离落实到整个学习过程

**源码观察**：`discovery_view()` 排除已经出现在 freeze 记录中的事件；`freeze()` 检查当前候选的 discovery/development 与 holdout 是否相交。该检查顺序还需要验证跨候选、跨修订的先暴露后冻结情况。

**必要反例**：事件 X 曾进入候选 A 的模型上下文，后来候选 B 的 discovery 列表不含 X；不能因此把 X 当作 B 的“从未暴露的独立 holdout”。

在真实 proposer 调用前冻结本轮拆分。复用现有 manifest/SQLite 记录已暴露事件和 episode 归属，不新增复杂平台。

要求：

- 隔离在候选之间及其修订之间一致生效。
- 同一持续故障切窗、换 incident ID、复制文件，不变成独立事故。
- 原始 holdout 内容与真值不进入 proposer、修复提示或检索记忆。
- 在线 Investigator 在执行该测试事件时可按受控工具获得观测，但看不到真值；该事件随后仍不能回灌知识 proposer 后继续当作盲测。
- 看过结果后改候选，旧 holdout 必须记为已见，不通过换名字恢复盲测身份。

---

## 3. Provider 解阻：优先定位已存在的项目配置

### 3.1 已知历史位置，不是对本机文件存在性的保证

此前项目使用过：

```text
~/.config/ecomsre/provider.env
```

此前使用的环境变量为：

```text
ECOMSRE_LLM_BASE_URL
ECOMSRE_LLM_API_KEY
ECOMSRE_LLM_MODEL
```

这些是本项目历史配置线索。此次 Codex 需要在当前主机和当前运行进程中确认，不能把历史路径当成已经加载。

只检查当前进程和这个明确的项目配置位置；不得扫描整个 home、浏览器、钥匙串、Codex 登录文件或其他项目寻找密钥。`phase5b-v1-ground-truth.env` 不是 Provider 配置。

### 3.2 缺少的是凭据、环境加载，还是新增价格配置，要分开诊断

当前 `configured_provider()` 还要求：

```text
ECOMSRE_PRODUCT_PROVIDER_PRICE_FILE
```

预检查需分别报告：

```text
项目配置文件是否存在/可读：只报告布尔值
三个 Provider 变量是否已加载：只报告布尔值
请求模型标识：允许报告非秘密模型名
价格文件是否存在/解析成功/与模型一致
API 和 Worker 的运行环境是否一致
功能开关是否启用
账本是否已存在、累计消费和剩余预算
```

不要把缺价格表笼统称为缺 API key，也不要让用户把 key 发到聊天或提交到 Git。

若读取 dotenv，使用限定的 KEY=VALUE 解析，不执行命令替换、任意 shell 或不明脚本。不要回显整个 env 文件。Worker 服务可能未继承交互 shell 的变量，必须验证实际启动方式。

### 3.3 模型选择

- 保持原 Goal：默认建议 `gpt-5.6-sol`，已有显式模型选择则不静默替换。
- 历史记录出现过 `gpt-5.4-mini-2026-03-17`；它不是对本次请求身份、当前可用性或价格的保证，更不能直接按完整 GPT-5.4 的价格计费。
- 当前配置缺失模型时采用原 Goal 明确的默认值并记录；账号或网关不支持时报告具体错误，不自行切换到另一模型。
- Codex 写代码用的模型与 Product API 调用模型分离，不修改 `~/.codex/config.toml`。

### 3.4 可核对价格，不把公开资料问题都转交用户

当前 `PriceSchedule` 需要：

```text
provider_profile
model
accepted_response_models（可为空，但不允许用前缀相似判定等价）
as_of
source
input_usd_per_million
output_usd_per_million
```

官方直连时，由 Codex 查询执行当日官方 API 模型/价格页，记录日期、具体模型和服务档位。2026-09-17 核对时，GPT-5.6 Sol 官方模型页列标准短上下文输入 $4 / 输出 $20 每百万 tokens，并单列缓存及 cache-write 价格；这些仅是文档当日参考，不替代执行时核对。

第三方兼容网关可能有不同费率、模型映射和附加收费，不能自动套用官方报价。只有该网关资料无法确认时，才请求最少的缺失信息。

预算预留必须覆盖实际使用的计费方式、缓存写入、服务档位和附加费。保守上界和账单实际成本分开报告；仅凭 token 数和上限费率计算的数字不要冒称账单精确值。未知 usage 保留预留，不记零。

不要为了通过 preflight 填 0、编造价格来源或放宽模型身份检查。

### 3.5 最小真实 smoke

配置与安全回归通过后，先执行有限真实 smoke，不直接跑整组实验：

1. 真实结构化决策；保存请求/返回模型、schema/prompt 版本、usage 和时延。
2. 经 Product API/Worker 执行至少一次模型所选合法读取，将实际结果返回模型。
3. 由真实模型输出结构化知识候选；即使候选被编译器拒绝，也保留正确的拒绝与调用证据。

smoke 阶段可采用回放遥测验证协议，但标为 `LIVE_PROVIDER_REPLAY_TELEMETRY`，不能替代后续 live 事件要求。

格式错误只用原有有界修复；没有脚本答案 fallback；错误计费不能记为免费。

`preflight.py` 只检查配置，不发请求。命令返回“已配置”不等于上述真实 smoke 已完成。

### 3.6 失败后的继续语义

当前调查读取已终结 session 时会直接返回旧结果。不能为继续实验覆盖 `PROVIDER_FAILED`、重写旧 session 或删除旧 Provider 账本。

优先用新的合法测试 incident/attempt 做后续实验；若需要同事件重试，先设计范围很小、追加式的显式新尝试语义，并证明不会重复已完成付费请求。不要把重试或新 session 当成新的独立事故。

---

## 4. 真实实验范围：先选现有观测能表达的模式

当前新表达式和 PredictionTest 的数值字段仅为 `cpu_percent`、`memory_bytes`；算子为 mean/max/delta/rate，并支持受限同单位比值。新增调查目录也不是任意查询语言。

这是一版合理的最小能力范围，但不能写成已经支持 Kafka partition skew、consumer throughput 或任意日志规律学习。

在启动 live 前冻结一页场景说明：

```text
目标模式：按实际选择，不预写成功标签
为什么现有 Core/Extension 不能充分解释它
可用观测字段、来源、窗口、采样单位
与一个同症状混淆模式的区分依据
Level B 表达式可用算子，不预先规定模型必须产出的规则
真实可执行的故障/负载控制及资源所有权
发现、开发、盲测和复发的独立 episode 边界
哪些控制只能回放，哪些是真实 live
```

优先复用能提供上述观测的既有 owned 环境。不能为了制造 Open World 删除用户 ACTIVE 规则、隐藏已知 Core 标签、放宽已知故障准入或偷偷关闭匹配。使用原 Goal 允许的新测试注册库，并说明“未知于哪一个知识快照”。

若现有环境不能支持有意义的 Level B live 模式，先完成真实 Provider 回放并给出具体观测缺口；保留限制终态。不新建大型服务平台来凑成功。

---

## 5. 累计预算与验收顺序

沿用原 Goal 总上限，已有更小授权优先：

```text
Provider requests ≤ 200
累计成本/保守承诺上界 ≤ USD 20
本地 live episode ≤ 12
```

这不是本补充的新额度。截图时为零不代表续跑时仍为零；启动要查现有账本。smoke、修复、学习、预览、重试全部计入同一账本。跨进程和多测试环境不能各自获得一份 20 美元额度。

执行顺序：

```text
读取现状与原 Goal
→ R1/R2 核心回归；R3/R4 语义和隔离检查
→ 定位项目 Provider 与价格信息
→ 最小真实 Provider + 真实工具往返
→ 冻结场景及拆分
→ 多独立 episode 调查与候选提议
→ 开发反馈；范围内有限修订
→ 冻结候选/代码/配置/评估协议
→ 独立 Shadow
→ 条件式测试晋升
→ 新事件正常入口确定性复用，LLM calls = 0
→ 只读恢复预览与兼容回归
→ owned 精确 cleanup
→ 同 PR 审阅、CI 和追加结果
```

完整成功仍需要原 Goal 所规定的发现/归纳至少 3 个独立本地 episode，以及冻结后验证/复用至少 2 个独立本地 episode。健康、Core、混淆、缺失与歧义控制按原 manifest 披露；逻辑案例数、派生案例数与 live episode 数分开计数。

不要求 LLM 候选一定胜出。规则无效就拒绝；不得按 holdout 结果反复改规则或追加样本挑选 PASS。

---

## 6. 交付与停止

### 6.1 沿用已有文件，少量追加

- 更新原 `product-v050-progress.md`，增加 continuation 记录。
- 在结果目录保存独立的续跑子目录，例如 `docs/results/product-v050/continuation-01/`。
- 旧 acceptance 保持可定位且语义不可改写；当前摘要可以链接旧工程结果和新增实验结果，但不能将旧 0-call 阶段改成曾使用 LLM。
- 不公开 key、完整私密请求、内部网关地址、原始私有真值；提供可审查的脱敏 trace 和 verifier。
- 修复 tests、相关 verifiers 和文档；不能为了新代码通过而删旧失败证据或降低旧成功条件。
- 更新同一个 Draft PR。没有用户新的明确指令，不 merge/tag/release/deploy。

### 6.2 完成报告必须回答

```text
本轮实际 HEAD 与 PR：
R1/R2/R3/R4：已修复、已证伪或仍阻塞；各对应测试
Provider：实际配置身份、真实请求数、计费依据和剩余预算
调查：哪次读取由模型决定，新证据如何改变其判断/下一步
知识：哪些候选确由模型提出；哪些拒绝，哪些通过
Level B：表达式的真实输入来源、字段、窗口、独立求值证据
盲测：哪些独立事件、分母和隔离证明
晋升：是否仅限测试注册库，批准来源如何记录
复用：新事件经正常入口命中的规则与零 LLM 计数
恢复：仍为只读预览，新增 Product 外部写入 0
清理、回归、独立审查、CI：各自实际状态
终态：原 Goal 定义的真实终态，不强行 PASS
```

普通代码错误、阶段结束、一次测试完成不要求暂停；可在权限与预算内修复就继续。

只有配置确实不可取得、成本上界不可建立、必要环境能力缺失、预算触顶、安全边界受阻或到达原 Goal 合法终态时停止。需要用户信息时只提出那个具体缺口，不能笼统要求重新提供全部配置。

---

## 7. 核对来源

以下仓库引用全部固定到审阅 HEAD，不代表后续分支没有变化：

- PR #104：`https://github.com/Raidriar7170/EcomSRE-Agent/pull/104`
- 结果：`docs/results/product-v050/acceptance.json`
- 配置检查：`scripts/product_v050/preflight.py`
- 调查：`src/ecomsre/product/investigation/runtime.py`
- 读取：`src/ecomsre/product/investigation/reads.py`
- 类型和费率：`src/ecomsre/product/investigation/contracts.py`
- Provider：`src/ecomsre/product/investigation/provider.py`
- 学习与冻结：`src/ecomsre/product/knowledge/evolution_v050.py`
- 候选求值：`src/ecomsre/product/knowledge/candidates_v050.py`
- 派生表达式：`src/ecomsre/product/knowledge/expressions.py`
- 正常匹配：`src/ecomsre/product/incidents/extensions.py`
- 原 Goal：本会话提供的 `EcomSRE_v0.5_Codex_Goal.md`，执行时与 PR 内版本核对。
- 官方模型/价格参考：`https://developers.openai.com/api/docs/models/gpt-5.6-sol`
- 官方 GPT-5.4 参考：`https://developers.openai.com/api/docs/models/gpt-5.4`

**交付重点：不是再增加模块，而是让真实模型的调查证据，经过同一套可追溯的检验，最终改变下一次事件的确定性处理。**

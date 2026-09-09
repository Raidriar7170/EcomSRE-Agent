# Goal: EcomSRE-Agent Product v0.4 — Minimal Payment Live Acceptance

> **Repository**: `Raidriar7170/EcomSRE-Agent`  
> **Goal ID**: `ecomsre-product-v040-minimal-payment-live-acceptance-v1`  
> **Starting `main`**: `cc941b51cbff9287b876be49652cd0ad83030474`  
> **Pinned OTel Demo**: `1755859a9de82c2e5e225be68abc401a5ebf2b4f`  
> **Required branch**: `codex/product-v040-minimal-payment-live-acceptance`  
> **Required PR base**: `main`  
> **Execution style**: continuous Goal mode; no intermediate user approval  
> **Target result**: one real local Payment configuration fault is diagnosed,
> restored by the Product v0.4 bounded-remediation path, and verified through two
> recovery windows  
> **Updated**: 2026-09-09

---

## 中文摘要

本 Goal 不再尝试完整启动和形式化验证全部 28 个 OTel Demo 服务，而是从当前
已经包含 Product v0.4 A–D 的干净 `main` 出发，搭建一个最小但真实的 Payment
验收环境。

目标链路：

```text
最小真实业务环境健康
→ 建立健康 Baseline
→ 注入 paymentFailure 配置故障
→ Product 诊断为 payment / CONFIGURATION_ERROR
→ 生成唯一 ROLLBACK_CONFIGURATION Candidate
→ 写入 Approval
→ 重新采集 Current State
→ 生成单次 AttemptAuthorization 和 WriteIntent
→ 独立 Executor 执行一次 Baseline 配置恢复
→ 持久化 StepReceipt
→ 两个恢复窗口均通过
→ RECOVERED
→ 精确清理 owned 资源
```

本 Goal 追求的是“跑通真实恢复闭环”，不是继续研究 Docker Inspect 的所有表示差异。
只保存对安全、行为和证据有意义的字段；原始 Inspect 可以完整留档，但不要求每个
非关键默认字段字节级不变。

激活本 Goal 后，Codex 可以自行完成代码修改、测试、Docker 本地运行、故障注入、
恢复执行、证据保存、PR、CI、审查和合并。开发过程中不需要再次向用户申请权限，
也不在普通失败、PR 或审查节点暂停。

---

# 0. Activation and Continuous Execution

## 0.1 Activation

使用下面这句话即可激活：

```text
执行 EcomSRE Product v0.4 Minimal Payment Live Acceptance Goal，
按文档持续完成，不需要中途再次向我申请授权。
```

一次激活覆盖本文范围内的：

```text
- 仓库修改、提交、推送和 PR；
- 本地 owned Docker 环境创建与清理；
- 健康流量和故障流量；
- paymentFailure 故障注入与恢复；
- Product Candidate、Approval、Authorization、WriteIntent；
- 独立 Executor 的一次固定配置恢复；
- Recovery Window 和结果发布；
- 测试、独立审查、CI 和成功后的合并。
```

Codex 不应在内部阶段、开发失败、PR 创建、Review、CI 或安全清理结束后停下来等待
用户。只要仍可在本 Goal 范围内安全修复，就继续执行。

## 0.2 Start procedure

开始时：

1. 拉取远端状态；
2. 确认 `main` 包含 Product v0.4 PR #91–#94；
3. 从当前干净 `main` 创建新分支和 worktree；
4. 保存本 Goal，并记录 Goal SHA-256；
5. 检查本地 Docker context、daemon 和现有资源；
6. 确认历史 PR #95–#101 保持只读，不从它们继承运行权限、attempt ID 或资源；
7. 创建一个 Draft PR，并持续在同一 PR 内推进到最终结果。

如果 `main` 已前移，先阅读差异；只要 Product v0.4 语义兼容，就以最新 `main`
作为新起点继续，不需要用户再次确认。

## 0.3 Final stop conditions

仅在下面三种最终状态之一停止：

```text
goal_complete_checkpoint
safety_blocked_checkpoint
implementation_not_feasible_checkpoint
```

普通测试失败、环境启动失败或诊断不符合预期不是最终状态。保存原因、清理环境、
修复后继续。

---

# 1. Mission

在一个最小化、真实、owned-local 的环境中证明：

```text
真实 Payment 配置错误
→ 当前 Product 诊断
→ 当前 Product Candidate
→ 当前 Product Approval / Authorization
→ 当前独立 Executor
→ 一次真实配置回滚
→ 当前 StepReceipt
→ 两个真实恢复窗口
→ RECOVERED
```

所有关键对象都必须来自当前 Product v0.4 实现，而不是单独写一个脚本绕过 Product。

最终结果至少回答：

```text
1. 故障是否真实改变了 Payment 行为？
2. Product 是否根据真实证据诊断到 payment / CONFIGURATION_ERROR？
3. Candidate 是否由当前 deterministic Candidate Filter 生成？
4. Approval、Current State、Authorization 和 WriteIntent 是否真实持久化？
5. Executor 是否只执行了一次固定的 Baseline restore？
6. StepReceipt 是否持久化且绑定前后状态？
7. 两个 Recovery Window 是否都证明配置和业务恢复？
8. 清理后是否没有 owned 资源残留？
```

---

# 2. Starting Facts

当前 `main` 已实现：

```text
Diagnosis
→ RemediationCandidate
→ OperatorApproval
→ Current-State Snapshot
→ AttemptAuthorization
→ WriteIntent
→ isolated typed Executor
→ StepReceipt
→ two Recovery Windows
→ RECOVERED / ESCALATE_HUMAN
```

当前唯一 Runbook 为：

```text
terminal: CORE_KNOWN
domain: CONFIGURATION
mechanism: CONFIGURATION_ERROR
target: payment
runbook: ROLLBACK_CONFIGURATION
step: RESTORE_BASELINE_CONFIGURATION
maximum forward steps: 1
```

支持的诊断证据 Clause 为：

```text
CHANGE_RECENT_ROLLOUT + METRIC_ERROR_RATE_STRONG
或
CHANGE_RECENT_ROLLOUT + LOG_CONFIGURATION_ERROR
```

因此本 Goal 不需要完整复现所有遥测类型。只要真实环境能够稳定产生并验证其中一个
配置错误 Clause，同时满足 Candidate 与恢复状态绑定，就足以完成本次验收。

历史 PR #95–#101 只作为失败经验和实现参考。它们不合并到本 Goal，也不改变历史结论。

---

# 3. Minimal Environment

## 3.1 Principle

只运行完成 Payment 故障、诊断、恢复和验证真正需要的服务。

Codex 应从固定 OTel Compose、实际调用路径和 Product Connector 需求推导最小依赖闭包，
不要因为原 Demo 默认包含某个服务就自动加入。

## 3.2 Required core

环境至少包含：

```text
payment
flagd
otel-collector
Product API
Product Worker
remediation-control-gateway
remediation-executor
read-only observer / state adapter
```

遥测侧至少提供一个可用的配置错误证据路径：

```text
Prometheus error metric
或
OpenSearch configuration-error log
```

推荐同时运行 Prometheus 和 OpenSearch，以便诊断更稳健。Jaeger 只有在实际 Product
Connector 或验证路径确实需要 Trace 时才加入。

## 3.3 Business traffic choice

优先使用最短、最稳定的真实 Payment 调用作为业务探针。

如果 Payment 服务提供的真实接口可以直接证明：

```text
healthy request succeeds
paymentFailure=100% causes request failure
恢复后同一请求重新成功
```

则直接调用 Payment，不必为了形式完整启动 Frontend、Recommendation、Ad、Kafka
等无关服务。

如果现有 Product/Verifier 只能使用 Checkout 业务 Oracle，则加入 Checkout 的最小
真实依赖闭包。可能包括：

```text
checkout
cart + valkey-cart
currency
email
product-catalog + astronomy-db
shipping + quote
```

最终服务集合以实际依赖和成功请求为准，而不是以上列表本身。

## 3.4 Explicit exclusions

除非真实依赖证明需要，否则不要加入：

```text
frontend
frontend-proxy
ad
recommendation
image-provider
load-generator
flagd-ui
telemetry-docs
Kafka
fraud-detection
accounting
Grafana
OpAMP
```

---

# 4. Minimal Harness Design

创建一个专用入口，例如：

```text
scripts/product/minimal_payment_acceptance_v040/
```

它应负责：

```text
- 生成最小 Compose；
- 启动健康环境；
- 建立 Product Environment 和 Baseline；
- 运行健康业务探针；
- 注入故障；
- 运行诊断与恢复；
- 采集两个恢复窗口；
- 清理并发布结果。
```

推荐配置路径：

```text
config/product-v040/minimal-payment/
docker-compose.product.minimal-payment.yml
```

推荐测试路径：

```text
tests/product_v040/minimal_payment/
```

不要求复用 v4 Full Harness 的全部代码。只移植确实有用、经过验证的部分：

```text
- effective Entrypoint / Cmd resolution；
- pinned image identity；
- birth-bound owned-resource identity；
- exact cleanup receipts；
- raw evidence 与 semantic validation 分离。
```

---

# 5. Practical Docker Validation

## 5.1 What must be checked

对 owned 资源检查：

```text
container / network / volume identity
owned labels
image digest
effective command
mount source and target
network identity
published port
current running / health state
```

## 5.2 What should not block by itself

下面这些 Docker 表示差异不能单独导致验收失败，只要有效语义不变：

```text
- 数组顺序；
- false 与未显式设置的 null；
- image index 与已验证 platform descriptor 的不同表示；
- Docker 自动写出的普通 IPv4/IPv6 默认字段；
- Compose null 与镜像默认 Entrypoint/Cmd 的等价表示。
```

原始值仍应保存，但准入基于规范化后的有效语义。

## 5.3 Safety

仍保留最基本的安全原则：

```text
- 只操作本 Goal 新建且可证明 owned 的资源；
- 不使用 prune；
- 不删除未知资源；
- 不修改 Docker Desktop 或宿主机网络设置；
- 不使用模型生成的 Shell 或任意写参数；
- cleanup 只删除当前 Goal 创建的精确对象。
```

---

# 6. Healthy Baseline

先在 `paymentFailure` 为健康值时启动最小环境。

完成：

```text
- 所需服务 Ready；
- Product API / Worker Ready；
- Connector 验证；
- 健康业务请求；
- Active Baseline；
- 至少一次 NO_INCIDENT 或等价的健康诊断证明。
```

Baseline 必须绑定：

```text
environment
service identity
healthy configuration digest
Payment healthy behavior
available evidence sources
```

无需复刻完整 28 服务实验的 30/30 和五窗口形式，除非当前 Product API 本身要求。
本 Goal 更关注真实故障恢复闭环；健康基线只需足以稳定区分 fault 与 recovered 状态。

---

# 7. Fault Injection

故障固定为：

```text
flag: paymentFailure
healthy state: off / baseline variant
fault state: 100%
target service: payment
```

故障注入由 Harness 的 trusted local controller 完成，不由 LLM 或 Product
Diagnosis 直接执行。

故障注入后必须确认：

```text
- 配置 digest 已改变；
- Payment 仍是目标服务；
- 真实业务请求出现预期失败；
- Metrics 或 Logs 中出现可支持 CONFIGURATION_ERROR 的强证据；
- Changes / Runtime 证据记录最近配置变化。
```

如果故障没有真实影响业务，不得继续伪造诊断成功；应修复流量或观测路径后重新运行。

---

# 8. Diagnosis

使用当前 Product 创建真实 Incident 和 Diagnosis。

成功诊断要求：

```text
terminal = CORE_KNOWN
lane = CORE
root = payment
domain = CONFIGURATION
mechanism = CONFIGURATION_ERROR
matched clause =
  configuration:change-and-error-metric
  或 configuration:change-and-log
```

所有支持证据引用必须能从 Product Evidence Store 中解析。

不能用以下方式替代：

```text
- Harness 直接写入 Diagnosis 表；
- 固定 JSON Fixture；
- 手工构造 completed Diagnosis；
- 从 evaluator truth 复制答案；
- 在 Candidate 阶段绕过 Product Diagnosis。
```

如果当前 Connector 无法形成配置错误 Clause，可以增加一个窄的、真实只读 Adapter，
但不得改写 Core Diagnosis 规则或降低阈值来制造命中。

---

# 9. Candidate and Approval

诊断完成后，通过当前 Product API / Repository 生成：

```text
ROLLBACK_CONFIGURATION
target = payment
parameters = []
maximum forward steps = 1
```

Candidate 必须绑定当前：

```text
Diagnosis
Evidence
Baseline
Identity Map
Capability Matrix
Registry
```

Approval 仍需作为真实 Product 对象持久化，但不需要中途等待用户点击确认。

本 Goal 的激活即是本地验收所需的 prior authorization。Approval 中记录与当前合同一致的
授权来源，并继续保持：

```text
Codex is not self-approving
LLM does not choose the action
Approval cannot broaden target or parameters
```

---

# 10. Current State and Authorization

Approval 后重新采集 Current State。

要求确认：

```text
environment owned
target = payment
healthy baseline digest bound
current fault digest bound
fault still present
no other active remediation
control adapter identity trusted
```

然后使用当前 Product 创建：

```text
AttemptAuthorization
RemediationAttempt
WriteIntent
```

这些对象必须来自现有 v0.4 逻辑，而不是 Harness 自行仿造。

如果状态已经恢复、漂移或与 Candidate 不一致，当前 Attempt 应拒绝；Harness 可以查明
原因、重建健康起点和故障，再重新执行新的完整验收流程。

---

# 11. Real Remediation

由当前独立 Executor 执行：

```text
ROLLBACK_CONFIGURATION
→ RESTORE_BASELINE_CONFIGURATION
```

执行要求：

```text
- target 固定为 payment；
- 无自由参数；
- 无任意命令；
- 只进行一次 Baseline restore；
- Executor 不持有 Docker socket；
- 恢复控制通过当前受限 control gateway；
- 写前状态和 Authorization 仍有效。
```

外部写入后立即持久化当前 Product 的 `StepReceipt`。

如果写入结果不明确，使用现有 `OUTCOME_UNKNOWN / ESCALATE_HUMAN` 语义；不要把
后续观察到的健康状态反推成一张不存在的成功 Receipt。

---

# 12. Recovery Verification

使用当前 Recovery Verifier 采集两个互不重叠的恢复窗口。

两个窗口都检查：

```text
Payment 配置 digest 回到 Baseline
paymentFailure 回到健康值
目标进程 / Endpoint 正常
真实业务请求成功
业务错误率满足当前 RecoveryPolicy
非 owned 资源无变化
```

成功终态：

```text
RECOVERED
```

动作调用成功但任一窗口失败：

```text
VERIFICATION_FAILED / ESCALATE_HUMAN
```

不要自动尝试第二种 Runbook。

---

# 13. Cleanup

无论流程成功或失败，都清理本 Goal 新建的 owned 资源。

允许：

```text
stop exact owned container
remove exact stopped container
remove empty exact owned network
remove unattached exact owned volume
remove attempt-local temporary files
```

不允许：

```text
prune
force-remove unknown container
delete by prefix only
modify non-owned resource
```

最终保存：

```text
owned containers remaining
owned networks remaining
owned volumes remaining
non-owned before / after comparison
baseline flag readback
```

如果安全清理本身出现项目代码问题，在资源身份仍可证明时修复 Cleanup 并继续，无需用户
再次授权。

---

# 14. Development Loop

允许 Codex 在同一 Goal 内迭代：

```text
实现
→ 离线测试
→ 启动最小环境
→ 观察失败
→ 保存证据
→ 清理
→ 修复
→ 再运行
```

不设置固定 attempt 数量。

唯一要求是：

```text
- 不静默重跑完全相同的失败版本；
- 每次重跑前有真实代码、配置或环境原因修复；
- 每次失败都保留简要结果和清理状态；
- 不为了 PASS 删除安全检查或降低诊断标准。
```

无需为每次工程重试创建新的 Goal。

---

# 15. Testing

至少覆盖：

```text
- 最小依赖闭包生成；
- healthy / fault / recovered 三种业务状态；
- real Connector response normalization；
- CONFIGURATION_ERROR Clause；
- Candidate eligibility；
- Approval persistence；
- state drift denial；
- Authorization single-use；
- WriteIntent idempotency；
- fixed Executor dispatch；
- Receipt persistence；
- two-window recovery；
- exact cleanup；
- unknown-resource cleanup denial。
```

运行：

```text
focused tests
full pytest
Ruff
mypy
existing Product v0.4 verifier
new minimal acceptance verifier
```

测试失败后在范围内修复并继续。

---

# 16. Evidence

公开结果至少包含：

```text
docs/results/product-v040-minimal-payment/
  README.md
  live-result.json
  evidence-manifest.json
  HUMAN_BRIEF.md
```

机器结果应记录：

```text
source head
minimal service set and why each service exists
image / Compose commitments
healthy baseline
fault proof
Diagnosis
Candidate
Approval
Current State
Authorization
WriteIntent
StepReceipt
Recovery Window 1
Recovery Window 2
final Product state
cleanup
Provider call count
claim boundary
```

原始遥测、凭证、私有路径和控制 Token 不进入公开仓库。

---

# 17. Review, CI, and Merge

在完整 Live 结果后：

1. 独立 Reviewer 检查代码、证据和 Claim；
2. 修复 Must Fix；
3. 运行完整测试、Ruff、mypy 和 verifier；
4. 运行 exact-head GitHub CI；
5. 若结果为真实 `RECOVERED` 且 Cleanup 干净，将 PR 标记 Ready；
6. squash merge 到 `main`；
7. 验证 merged tree 与 reviewed tree 一致；
8. 发布最终 completion record。

这些操作都包含在本 Goal 的持续授权中，不需要中途询问用户。

如果 Live 结果是可信的失败终态，而继续修复需要扩大到完整 OTel 环境、生产权限或重写
Product 核心语义，则保留 Draft PR 并输出最终阻塞结果。

---

# 18. Definition of Done

正向完成必须满足：

```text
[ ] 使用真实 Payment 服务和真实 flagd 配置
[ ] 使用最小真实依赖环境
[ ] 健康业务状态可验证
[ ] Active Baseline 存在
[ ] paymentFailure=100% 真实导致业务失败
[ ] Product 返回 CORE_KNOWN / payment / CONFIGURATION_ERROR
[ ] 支持证据引用可解析
[ ] Product 生成唯一 ROLLBACK_CONFIGURATION Candidate
[ ] Product 持久化 Approval
[ ] Approval 后重新采集 Current State
[ ] Product 生成 AttemptAuthorization 和 WriteIntent
[ ] Executor 只执行一次真实 Baseline restore
[ ] Product 持久化 StepReceipt
[ ] 两个 Recovery Window 均通过
[ ] 最终状态为 RECOVERED
[ ] Payment 配置和业务行为恢复
[ ] owned 资源清理为零
[ ] 非 owned 资源未被修改
[ ] Provider / LLM 调用为 0
[ ] 独立审查通过
[ ] 完整测试和 exact-head CI 通过
[ ] PR 合并到 main
```

正向终态：

```text
goal_complete_checkpoint
ECOMSRE_PRODUCT_V040_MINIMAL_PAYMENT_LIVE_ACCEPTANCE_COMPLETE
```

---

# 19. Claim Boundary

完成后可以说：

> 在 pinned OTel Payment 本地最小环境中，Product v0.4 对真实
> `paymentFailure` 配置故障完成了证据绑定诊断、确定性 Candidate、Goal 授权映射的
> Approval、状态绑定单次 Authorization、一次固定配置回滚、StepReceipt 和双窗口恢复
> 验证，最终恢复到 Baseline 并清理 owned 资源。

不能说：

```text
- 已实现生产自动自愈；
- 已证明所有故障都能恢复；
- 已证明完整 28 服务环境稳定；
- 已证明跨环境泛化；
- 已证明 exactly-once 外部副作用；
- 无需人工治理即可用于生产。
```

---

# 20. Copyable Activation Prompt

```text
Read and execute:

docs/goals/EcomSRE_Product_v0.4_Minimal_Payment_Live_Acceptance_Goal.md

Treat it as the active Goal Contract.

从当前干净 main 开始，搭建最小真实 Payment 环境，完成：

健康 Baseline
→ paymentFailure 配置故障
→ Product CORE_KNOWN / payment / CONFIGURATION_ERROR
→ ROLLBACK_CONFIGURATION Candidate
→ Approval
→ fresh Current State
→ AttemptAuthorization
→ WriteIntent
→ 一次真实 Baseline restore
→ StepReceipt
→ 两个 Recovery Window
→ RECOVERED
→ Cleanup

本次激活是对文档范围内代码修改、提交、推送、PR、测试、独立审查、CI、本地 owned
Docker、loopback 请求、故障注入、Product Approval / Authorization、受限 Executor
执行、恢复验证和精确清理的持续授权。

不要在普通失败、阶段结束、PR、Review、CI 或清理后向我再次申请授权。只要问题能够在
当前 Goal 范围内安全修复，就保存失败证据、完成清理、修复并继续。

不设固定工程尝试预算，也不要把每次失败拆成新的 Goal。

禁止生产或远程环境、未知资源修改、全局 Docker cleanup、任意 Shell、其他 Runbook
和 Product 核心诊断规则降级。

持续执行，直到：
- 成功完成并合并；或
- 出现无法安全清理的真实安全阻塞；或
- 证明最小环境无法在不改变 Product 核心语义的情况下完成验收。
```

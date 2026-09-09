# Goal: EcomSRE-Agent Product v0.4.1 — Presentation and Live Safety Evaluation Closeout

> **Repository**: `Raidriar7170/EcomSRE-Agent`  
> **Goal ID**: `ecomsre-product-v041-presentation-live-safety-closeout-v1`  
> **Starting `main`**: `85675083f64e2bf77f8b2564bfdbaca9dd526b26`  
> **Starting tree**: `dc628159e60f271fe1bc51a234daf1ed7194cec5`  
> **Pinned OTel Demo**: `1755859a9de82c2e5e225be68abc401a5ebf2b4f`  
> **Required branch**: `codex/product-v041-presentation-live-safety-closeout`  
> **Required PR base**: `main`  
> **Execution style**: continuous Goal mode, one closeout PR, no intermediate user approval  
> **Product scope**: public presentation, live safety evaluation, observed timing metrics, repository hygiene  
> **Provider / LLM calls required**: `0`  
> **Target terminal**: `ECOMSRE_PRODUCT_V041_PRESENTATION_AND_LIVE_SAFETY_CLOSEOUT_COMPLETE`  
> **Updated**: 2026-09-09

---

## 中文摘要

Product v0.4 已经在真实的本地最小 Payment 环境中完成：

```text
paymentFailure=100%
→ CORE_KNOWN / payment / CONFIGURATION_ERROR
→ ROLLBACK_CONFIGURATION Candidate
→ Approval
→ fresh Current State
→ AttemptAuthorization
→ WriteIntent
→ 独立 Executor 一次固定恢复
→ APPLIED StepReceipt
→ 两个恢复窗口
→ RECOVERED
→ owned 资源零残留
```

但仓库公开入口仍主要停留在 v0.3 的“只读 Product”叙事，而且当前只有一个真实
成功恢复场景，缺少一组集中展示“什么情况下系统必须拒绝写入”的 Live Safety 结果。

本 Goal 作为 v0.4.1 收尾，只做四件事：

```text
1. 更新 README、STATUS、ARCHITECTURE、LIMITATIONS、QUICKSTART、
   API、OPERATIONS、面试讲法和离线 HTML 手册；
2. 在已合并的 Minimal Payment 环境上运行一组真实安全矩阵；
3. 从真实时间戳输出诊断、授权、执行和验证的时间分解；
4. 关闭被 PR #102 替代的历史 Draft PR，但完整保留分支与失败证据。
```

v0.4.1 不增加新的故障机制、Runbook、模型、聚类算法或生产部署能力。
它的目标是让项目的公开叙事、真实安全证据和仓库状态与已经完成的 v0.4 保持一致，
然后收口。

---

# 0. Activation and Continuous Goal Mode

## 0.1 Activation

使用下面这句话即可激活：

```text
执行 EcomSRE Product v0.4.1 Presentation and Live Safety Evaluation Goal，
按文档持续完成，不需要中途再次向我申请授权。
```

一次激活覆盖本文明确列出的：

```text
- 仓库文件修改；
- 提交、推送、创建和更新一个 PR；
- 本地 owned Docker 最小 Payment 环境；
- 真实健康流量和 paymentFailure 故障；
- Product Candidate / Approval / Revocation / Attempt 流程；
- 状态漂移测试；
- 一次受限恢复与重复 dispatch 测试；
- 一次恢复验证失败测试；
- 精确 cleanup；
- 测试、独立审查、CI 和成功后的 squash merge；
- 对指定历史 Draft PR 添加 superseded 说明并关闭但不合并。
```

Codex 不应在普通测试失败、单个安全案例失败、PR 创建、Review、CI、一次清理完成或
内部阶段结束后停下来等待用户。只要仍能在本文范围内安全修复，就保留失败证据、
清理环境、修复并继续。

## 0.2 Start procedure

开始时：

1. `git fetch origin --prune`；
2. 验证当前 `main` 包含 PR #102 的合并提交；
3. 验证起始树和固定 OTel Demo 子模块；
4. 若 `main` 已前移，阅读差异；只要 v0.4 语义兼容，就从最新 `main` 开始；
5. 创建新分支和干净 worktree；
6. 将本文件保存到：
   `docs/goals/EcomSRE_Product_v0.4.1_Presentation_and_Live_Safety_Evaluation_Goal.md`；
7. 持久化 Goal SHA-256、起始 commit/tree、子模块和激活时间；
8. 新建一个 Draft PR，并在同一个 PR 中持续推进；
9. 确认本地没有遗留的 EcomSRE owned 容器、网络或卷；
10. 读取 PR #102、v0.3 结果和当前公开文档，先建立 claim-to-evidence 清单。

## 0.3 Final stop conditions

仅在以下最终状态之一停止：

```text
goal_complete_checkpoint
safety_blocked_checkpoint
closeout_not_feasible_checkpoint
```

普通工程失败不是最终状态。

---

# 1. Mission

完成一个面向公开展示和安全可信性的项目收尾版本。

最终仓库应让访问者在不阅读全部历史 PR 的情况下，快速理解：

```text
- 系统解决什么问题；
- 为什么不是让 LLM 自由调用工具和直接执行；
- 证据 Runtime、确定性诊断、知识演化和受限恢复如何连接；
- v0.2.4、v0.3、v0.4 分别真实证明了什么；
- v0.4 在什么条件下允许一次写入；
- 什么情况下系统会拒绝写入；
- 实际恢复耗时如何分解；
- 当前仍不能外推到哪些生产结论。
```

v0.4.1 的完成链路：

```text
公开事实审计
→ Live Safety Matrix
→ Timing Summary
→ README / Product Docs / Interview Handbook 更新
→ 历史 Draft PR 收口
→ 独立审查
→ 完整测试与 CI
→ merge
```

---

# 2. Accepted Starting Truth

## 2.1 Product v0.3

已合并且必须继续保留的实测结果：

```text
健康系统：
30/30 checkout 事务
Metrics / Resources / Traces / Logs / Runtime 有证据
NO_INCIDENT
能力限制 0

知识演化：
P1 / P2 / P3 → 一个 Fault Family
Runtime 规则挖掘
Shadow recall 1.0 / FPR 0.0
H1 → EXTENSION_KNOWN / kafka-queue-backlog / fraud-detection
```

这些结果属于有界本地 OTel 环境，不是跨环境基准。

## 2.2 Product v0.4

当前 `main` 已包含：

```text
Diagnosis
→ deterministic RemediationCandidate
→ persisted OperatorApproval
→ fresh Current-State Snapshot
→ single-use AttemptAuthorization
→ WriteIntent
→ isolated typed Executor
→ StepReceipt
→ two Recovery Windows
→ RECOVERED / ESCALATE_HUMAN
```

当前唯一受限恢复映射：

```text
CORE_KNOWN
payment
CONFIGURATION
CONFIGURATION_ERROR
→ ROLLBACK_CONFIGURATION
→ RESTORE_BASELINE_CONFIGURATION
maximum forward steps = 1
```

## 2.3 Merged live result

PR #102 已真实观测：

```text
minimal services: 10
healthy direct Payment requests: 194
fault: paymentFailure=100%
fault confirmation: final 30/30 requests failed as expected
diagnosis: CORE_KNOWN / payment / CONFIGURATION_ERROR
gateway restore consumptions: 1
recovery window 1: 39 requests / 0 errors
recovery window 2: 39 requests / 0 errors
Product terminal: RECOVERED
Provider / LLM calls: 0
cleanup: 10 containers / 3 networks / 2 volumes removed
remaining owned resources: 0 / 0 / 0
```

健康 Diagnosis 在该最小环境中为 `INSUFFICIENT_EVIDENCE`，因为 Logs、Runtime 和
Traces 未提供给健康诊断；公开材料不得将它改写为 `NO_INCIDENT`。

## 2.4 Current public-document mismatch

当前公开材料仍主要使用：

```text
v0.3
只读 Product
action/remediation authority = NONE
```

v0.4.1 必须改成更准确的表述：

> Diagnosis 本身始终只读且没有写权限；Product v0.4 额外提供一个默认关闭、
> 独立批准、状态绑定、单次执行并由外部窗口验证的受限恢复通道。

---

# 3. Non-Goals

本 Goal 不做：

```text
- 新故障类型；
- 新 Runbook；
- Kafka backlog 自动恢复；
- Recommendation restart；
- Email memory-leak 两步恢复；
- 聚类算法升级；
- Rule Mining 阈值修改；
- Core Diagnosis 规则或阈值降低；
- 完整 28 服务 Harness 复跑；
- 新模型或 Provider；
- Multi-Agent 恢复；
- Kubernetes、云端或生产部署；
- 多租户、HA 或 PostgreSQL 改造；
- 任意 Shell 或模型生成写参数；
- exactly-once 外部副作用声明；
- 删除失败证据；
- 合并历史失败 Draft PR；
- 删除历史分支。
```

---

# 4. Workstream A — Public Presentation Closeout

## 4.1 Root README

更新 `README.md`，使前 20 秒内容反映 v0.4.1。

推荐中文定位：

```text
面向微服务故障定位与受限恢复的可验证 SRE Agent：
由 Runtime 管理服务身份、证据缺口、诊断准入和动作权限；
未知故障经人引导知识演化，真实恢复仅通过独立批准和状态绑定通道执行。
```

推荐英文定位：

```text
A verifiable SRE Agent that turns typed telemetry into evidence-backed
diagnoses, evolves environment-specific knowledge through human-gated
evaluation, and executes only separately authorized bounded remediation.
```

当前状态应改为：

```text
v0.4.1 收尾完成
单租户本地 Product 原型
Diagnosis 默认只读
一个真实 Payment 受限恢复闭环
```

README 的阶段表扩展为四阶段：

| 阶段 | 核心问题 | 已证明结果 |
| --- | --- | --- |
| 1 · 证据驱动诊断 | 证据缺失与工具选择 | Runtime 维护缺口、观测和准入 |
| 2 · 可部署 Product | 研究代码缺少状态与接入 | FastAPI / Worker / SQLite / CAS / Baseline |
| 3 · 知识演化 | 环境特有未知故障 | Fault Family → Rule Mining → Shadow → H1 |
| 4 · 受限恢复 | 诊断如何安全连接真实动作 | Approval → state-bound Authorization → one write → two windows |

README 的结果表至少包含：

```text
v0.2.4 健康 NO_INCIDENT
v0.3 Kafka knowledge evolution
v0.4 Payment RECOVERED
v0.4.1 Live Safety Matrix
```

## 4.2 STATUS

重写 `docs/product/STATUS.md`，明确区分：

```text
Diagnosis authority
Remediation authority
Current verified live results
Current limitations
Public quickstart
```

状态标题应为 v0.4.1，而不是 v0.3。

## 4.3 ARCHITECTURE

更新 `docs/product/ARCHITECTURE.md` 为四层：

```text
A. Environment and Evidence
B. Deterministic Diagnosis
C. Human-Gated Knowledge Evolution
D. Bounded Remediation
```

D 层必须展示：

```text
DiagnosisResultV1
→ RemediationCandidateV1
→ OperatorApprovalV1
→ CurrentStateSnapshotV1
→ AttemptAuthorizationV1
→ WriteIntentV1
→ isolated Executor
→ StepReceiptV1
→ RecoveryWindowV1 × 2
→ RecoveryEvaluationV1
```

图中明确：

```text
Diagnosis action_authority = NONE
Approval ≠ execution authority
LLM does not select target / action / command
Executor has no Docker socket
```

创建新的：

```text
docs/assets/ecomsre-v041-architecture.mmd
docs/assets/ecomsre-v041-architecture.svg
```

保留 v03 文件作为历史，不覆盖删除。

## 4.4 REMEDIATION

新增：

```text
docs/product/REMEDIATION.md
```

内容包括：

```text
- 为什么 Diagnosis 不直接获得权限；
- Candidate、Approval、Authorization 的区别；
- 当前唯一 Runbook；
- 执行器隔离；
- WriteIntent / Receipt / uncertain outcome；
- 双窗口恢复；
- v0.4 真实 Payment 结果；
- Live Safety Matrix；
- 不能外推的结论。
```

## 4.5 LIMITATIONS

更新 `docs/product/LIMITATIONS.md`，删除“当前只有只读诊断、无真实恢复”这类过时表述。

新的准确边界：

```text
- 默认 Product 仍只读；
- 受限恢复必须显式启用；
- 当前只验证一个本地 Payment 配置故障；
- 当前只有一个 Runbook；
- Minimal 健康诊断没有 NO_INCIDENT；
- 不证明生产 self-healing；
- 不证明跨环境泛化或 exactly-once；
- 单租户 SQLite；
- 没有 HA、Kubernetes 和长期 SLO；
- 聚类仍是小样本 deterministic baseline。
```

## 4.6 QUICKSTART

更新 `docs/product/QUICKSTART.md`，提供四个清晰入口：

```text
A. 两分钟结果导览
B. Docker-free 知识演化 Demo
C. Docker-free 受限恢复 Fixture Demo
D. 已合并 Live Evidence Verifier
```

Docker-free 恢复入口：

```bash
PYTHONPATH=src:. uv run --frozen --no-sync \
python -m scripts.product.demo_remediation_v040
```

Live 证据校验入口：

```bash
PYTHONPATH=src:. uv run --frozen --no-sync \
python -m scripts.ci.verify_product_v040_minimal_payment
```

不得把 verifier 说成重新运行真实 Docker 实验。

## 4.7 API and OPERATIONS

更新 `docs/product/API.md`：

```text
- 页面标题不再写 current v0.3；
- 增加 remediation candidate、approval、revocation、attempt、
  decision trace、receipt 和 recovery routes；
- 明确 API 没有自由命令执行端点；
- 明确 executor 是独立进程，而不是 HTTP execute 路由。
```

更新 `docs/product/OPERATIONS.md`：

```text
- 默认启动仍不启用 remediation profile；
- 如何理解 control gateway 和 executor isolation；
- 如何查看 Candidate / Approval / Attempt / Receipt / Recovery；
- OUTCOME_UNKNOWN 与 ESCALATE_HUMAN；
- cleanup 与故障恢复的区别；
- Minimal live result 不是生产部署教程。
```

## 4.8 Interview material

更新 `docs/interview/PROJECT_PITCH.md`：

```text
- 20 秒版；
- 90 秒版；
- 四个核心贡献；
- 两条简历表述；
- 受限恢复高频问答；
- 为什么不是自主自愈；
- Safety Matrix；
- MTTD / TTR；
- 代码阅读路线；
- 边界和失败教训。
```

新增自包含 HTML：

```text
docs/interview/ecomsre-agent-v041-handbook.html
```

保留旧的 v03 HTML。

HTML 至少包含：

```text
项目定位
四层架构图
一次真实 Incident 流程
知识演化流程
受限恢复权限链
真实指标
Live Safety Matrix
时间线
失败教训
面试讲稿
高频问答
代码路线
Claim Boundary
```

要求：

```text
- 无外部运行依赖；
- file:// 可打开；
- 1440px 桌面检查；
- 390px 移动检查；
- print CSS 检查；
- 内部链接和 HTML ID 唯一；
- SVG / Mermaid 资产可解析。
```

---

# 5. Workstream B — Live Safety Evaluation

## 5.1 Purpose

Live Safety Evaluation 不再证明“系统能恢复”，而是证明：

> 即使环境、Diagnosis 或 API 请求看起来接近合法路径，缺少任何必要授权或状态条件时，
> Product 仍会保持零写入；重复执行也不会产生第二次外部恢复。

复用已合并的 Minimal Payment 环境和现有 Product v0.4 组件。

每个案例必须使用：

```text
- 真实 pinned Payment；
- 真实 flagd；
- 真实 Product API / SQLite / CAS；
- 真实 Current State Adapter；
- 当前 Candidate / Approval / Attempt 代码；
- 当前 control gateway / executor（仅需要写入的案例）；
- 精确 owned cleanup。
```

不得用纯 Fixture 代替 Live Matrix。

## 5.2 Case isolation

每个案例使用唯一：

```text
case_id
environment/runtime namespace
Product data root or case-isolated database
idempotency-key namespace
evidence root
owned-resource labels
```

可以复用不可变镜像。

可以在同一最小基础设施会话中运行多个案例，但必须证明：

```text
- flag 在案例开始前为 Baseline；
- 无前一案例 active approval / attempt / write intent；
- Product 持久状态不会让案例之间互相授权；
- 所有案例对象和计数可单独归属。
```

若不能证明隔离，则使用新的环境。

---

# 6. Mandatory Safety Cases

## S0 — Healthy Non-Action

### Setup

```text
Payment healthy
Active Baseline available
real healthy business request succeeds
```

创建健康 Incident 并请求 remediation candidates。

当前 Minimal 环境的健康 Diagnosis 可以继续是：

```text
INSUFFICIENT_EVIDENCE
```

不要求为了展示而强行改成 `NO_INCIDENT`。

### Required result

```text
candidate count = 0
approval count = 0
authorization count = 0
write intent count = 0
executor dispatch count = 0
gateway consumption count = 0
StepReceipt count = 0
external Product remediation writes = 0
```

这个案例证明：

> 健康业务状态或证据不足不会产生可执行恢复。

---

## S1 — Revoked Approval Denial

### Setup

```text
paymentFailure=100%
real business failures confirmed
CORE_KNOWN / payment / CONFIGURATION_ERROR
one ROLLBACK_CONFIGURATION Candidate
one active Approval
```

随后通过当前 Product API 创建 Revocation。

### Required result

Approval 状态：

```text
REVOKED
```

尝试创建 RemediationAttempt 时必须得到当前稳定拒绝：

```text
REMEDIATION_APPROVAL_REVOKED
```

或同一 Product 层的等价类型化投影，但内部原因必须明确绑定为
`APPROVAL_REVOKED`。

计数：

```text
AttemptAuthorization = 0
WriteIntent = 0
ExecutorDispatch = 0
GatewayConsumption = 0
StepReceipt = 0
Product remediation write = 0
```

故障配置由独立实验控制器在 cleanup 中恢复，不能冒充 Product Remediation。

---

## S2 — State Drift Before Authorization

### Setup

```text
paymentFailure=100%
CORE_KNOWN diagnosis
Candidate
active Approval
```

在 Product 创建 AttemptAuthorization 之前，由独立受控实验控制器把 Payment 配置恢复到
Baseline。

然后调用当前 Product Attempt 创建流程，使其重新读取 Current State。

### Required result

Current State 必须真实反映：

```text
current configuration = Baseline
fault_still_present = false
```

当前验证顺序预期拒绝为：

```text
CONFIGURATION_DRIFT_NOT_VISIBLE
```

若代码通过另一个更精确的现有 DenialReason（例如 `FAULT_NO_LONGER_PRESENT`）表达同一事实，
可以保留实际结果，但不得通过重排或放宽 Gate 来配合文档。

计数：

```text
AttemptAuthorization = 0
WriteIntent = 0
ExecutorDispatch = 0
GatewayConsumption = 0
StepReceipt = 0
Product remediation write = 0
```

这个案例证明：

> Approval 不是持续写权限；状态已经变化时必须重新拒绝。

---

## S3 — Idempotent Replay and Single External Effect

### Setup

运行一次新的真实 Payment fault 和完整受限恢复。

在同一语义输入上进行：

```text
- Candidate POST 相同 Idempotency-Key 重放；
- Approval POST 相同 Idempotency-Key 重放；
- Attempt POST 相同 Idempotency-Key 重放；
- 两个 executor wake-up 或重复 run_one 调用。
```

可使用受控并发来覆盖竞争。

### Required result

```text
one semantic Candidate
one Approval
one RemediationAttempt
one AttemptAuthorization
one WriteIntent
one ExecutorDispatch
one GatewayConsumption
one APPLIED StepReceipt
two Recovery Windows
final terminal = RECOVERED
external restore count = 1
```

重复调用可以返回原对象、正在处理状态或已完成状态，但不能产生第二次外部写入。

这个案例不建立通用 exactly-once 声明；它只证明当前固定本地路径的幂等和单次消费结果。

---

## S4 — Verification Evidence Failure Does Not Retry

### Setup

运行一次真实 Payment fault、Diagnosis、Candidate、Approval、Authorization 和一次真实
Baseline restore，持久化 `APPLIED` StepReceipt。

Recovery Window Provider 使用真实环境状态，但故意让其中一个窗口缺少最低业务请求数：

```text
business_requests < RecoveryPolicy.minimum_business_requests
```

推荐方式是窗口内不启动业务探针，而不是伪造错误响应或再次破坏 Payment 配置。

### Required result

```text
configuration restored = true
StepReceipt outcome = APPLIED
RecoveryEvaluation terminal = VERIFICATION_FAILED
final disposition = ESCALATE_HUMAN
reason includes BUSINESS_SLI_FAILED
GatewayConsumption = 1
external restore count = 1
second remediation write = 0
alternate Runbook = 0
```

公开材料必须将其描述为：

> 恢复验证证据不足时不宣布 RECOVERED，也不自动再次写入。

不能描述成“配置恢复失败”，因为该案例中配置本身已经回到 Baseline。

---

# 7. Safety Matrix Acceptance

创建：

```text
docs/results/product-v041-live-safety/live-safety-matrix.json
```

至少包含：

| Case | Expected authority result | Expected write count |
| --- | --- | ---: |
| S0 Healthy Non-Action | no Candidate | 0 |
| S1 Revoked Approval | typed denial | 0 |
| S2 State Drift | typed denial | 0 |
| S3 Duplicate Replay | RECOVERED, deduplicated | 1 |
| S4 Verification Failure | VERIFICATION_FAILED / ESCALATE_HUMAN | 1 |

整体必须证明：

```text
unauthorized Product writes = 0
duplicate Product writes = 0
unknown-target writes = 0
alternate Runbook executions = 0
all case cleanup results = CLEAN
final owned resources = 0
non-owned resources unchanged
Provider / LLM calls = 0
```

---

# 8. Workstream C — Observed Timing Metrics

## 8.1 Purpose

将当前恢复结果从“最终成功”进一步分解为可理解的 SRE 时间线。

不使用单次样本宣称通用 Mean Time。

公开命名优先使用：

```text
Observed Time to Detect
Observed Diagnosis Latency
Observed Time to First Recovery
Observed Time to Verified Recovery
Observed End-to-End Recovery
```

若样本数量不足，不写“平均”“稳定 P95”或“SLO”。

## 8.2 Required events

对 S3 成功案例至少记录 UTC 和 monotonic：

```text
fault_write_started
fault_write_acknowledged
first_failed_business_request
incident_created
diagnosis_job_started
diagnosis_completed
candidate_persisted
approval_persisted
current_state_observed
authorization_persisted
write_intent_committed
gateway_restore_consumed
StepReceipt_persisted
first_successful_business_request
recovery_window_1_started
recovery_window_1_ended
recovery_window_2_started
recovery_window_2_ended
RECOVERED_persisted
cleanup_started
cleanup_completed
```

## 8.3 Derived metrics

创建：

```text
docs/results/product-v041-live-safety/timing-summary.json
```

至少计算：

```text
fault_ack_to_first_failure_ms
fault_ack_to_diagnosis_completed_ms
diagnosis_job_latency_ms
diagnosis_to_candidate_ms
approval_to_current_state_ms
approval_to_authorization_ms
authorization_to_write_intent_ms
write_intent_to_gateway_consumption_ms
gateway_consumption_to_receipt_ms
receipt_to_first_success_ms
receipt_to_window1_complete_ms
receipt_to_verified_recovery_ms
fault_ack_to_verified_recovery_ms
cleanup_duration_ms
```

对 S0、S1、S2 额外记录：

```text
time_to_safe_denial_ms
```

对 S4 记录：

```text
receipt_to_verification_failed_ms
```

## 8.4 Timing integrity

要求：

```text
- UTC 用于跨对象排序；
- monotonic 用于同进程耗时；
- 不从文件 mtime 推断关键阶段；
- 不用测试耗时替代运行时延迟；
- 缺失时间戳写 NOT_MEASURED，不插值；
- 记录样本数；
- 任何 median/range 都必须给出分母。
```

---

# 9. Workstream D — Repository Hygiene

## 9.1 Historical Draft PRs

PR #102 已完成并合入 `main` 后，以下历史 Draft PR 已被最终路径替代：

```text
#95
#97
#98
#99
#100
#101
```

在 v0.4.1 正向结果和文档审查完成后，对每个 PR：

1. 添加一条简短 superseded 说明；
2. 链接 PR #102 和 v0.4.1 Closeout PR；
3. 明确该 PR 的失败/清理结果仍是其自身历史事实；
4. 关闭但不合并；
5. 不删除分支；
6. 不重写 PR Body、结果文件或评论。

推荐评论：

```text
Superseded without merge by the merged Product v0.4 minimal Payment result
(PR #102) and the v0.4.1 presentation/safety closeout.

This PR's recorded terminal and evidence remain authoritative for its own
attempt. No result is reclassified or deleted. The branch is retained for
history.
```

## 9.2 Project evolution

更新：

```text
docs/history/PROJECT_EVOLUTION.md
```

用一段简洁时间线说明：

```text
Full Harness negative experiments
→ exact cleanup
→ Minimal Payment recovery success
→ v0.4.1 live safety and presentation closeout
```

不要让 README 承担全部失败历史。

## 9.3 Open PR surface

最终应避免让 GitHub 首页显示大量已被替代的 Draft PR。

关闭历史 PR 是仓库整理，不是删除失败证据。

---

# 10. Documentation Claim Map

创建：

```text
docs/analysis/product-v041-claim-map.json
```

每个公开数字和结论映射到：

```text
claim_id
public_text
source_file
source_path_or_object
evidence_sha256
scope
limitations
```

必须覆盖：

```text
6/16 → 10/16 与 Macro-F1 0 → 0.40（若 README 继续使用）
30/30 NO_INCIDENT
3 个 Open-World 窗口
Shadow recall 1.0 / FPR 0.0
H1 EXTENSION_KNOWN
194 healthy Payment requests
30/30 fault probes
1 gateway restore
2 × 39 requests / 0 errors
cleanup 0/0/0
Live Safety Matrix
timing metrics
6520 tests / 21 skips / mypy 705 files（仅指 PR #102）
```

不同版本的测试数不能相加成一个“总测试量”结论。

---

# 11. Public Result Package

创建：

```text
docs/results/product-v041-live-safety/
  README.md
  live-safety-matrix.json
  timing-summary.json
  case-s0-healthy-non-action.json
  case-s1-revoked-approval.json
  case-s2-state-drift.json
  case-s3-idempotent-recovery.json
  case-s4-verification-failure.json
  evidence-manifest.json
  HUMAN_BRIEF.md
```

公开结果只包含：

```text
typed object projections
bounded counts
safe error codes
timestamps
hash commitments
cleaned resource summaries
```

不包含：

```text
tokens
control credentials
private absolute paths
raw private telemetry
unbounded logs
host-specific secrets
```

---

# 12. Implementation Scope

优先新增或修改：

```text
README.md

docs/product/STATUS.md
docs/product/ARCHITECTURE.md
docs/product/REMEDIATION.md
docs/product/LIMITATIONS.md
docs/product/QUICKSTART.md
docs/product/API.md
docs/product/OPERATIONS.md

docs/interview/PROJECT_PITCH.md
docs/interview/ecomsre-agent-v041-handbook.html

docs/assets/ecomsre-v041-architecture.mmd
docs/assets/ecomsre-v041-architecture.svg

docs/history/PROJECT_EVOLUTION.md

scripts/product/live_safety_v041/**
tests/product_v041/**
scripts/ci/verify_product_v041_closeout.py
.github/workflows/agent-mainline.yml
```

允许对以下路径做小范围兼容修改：

```text
scripts/product/minimal_payment_acceptance_v040/**
src/ecomsre/product/remediation/**
docker-compose.product.yml
Dockerfile.product
```

前提：

```text
- 修改由 Safety Matrix 暴露的真实缺陷驱动；
- 不增加新 Runbook；
- 不改变既有成功证据的解释；
- 不降低 Candidate / Approval / State / Authorization / Verifier 门槛；
- 有回归测试和独立审查。
```

---

# 13. Development and Execution Loop

在同一 Goal 和 PR 内持续：

```text
公开事实审计
→ Safety Harness 实现
→ 离线测试
→ Live Case
→ 结果与 cleanup
→ 修复
→ 下一个 Case
→ Timing
→ Presentation 更新
→ Review / CI
```

不设置固定工程尝试预算。

不得静默原样重跑相同失败；每次重跑前要有真实修复或环境原因。

所有真实案例都应：

```text
- 开始前验证 Baseline；
- 结束后恢复 Baseline；
- 清理 owned 资源；
- 保存案例结果；
- 不覆盖上一轮失败。
```

---

# 14. Testing

## 14.1 Safety-unit tests

至少覆盖：

```text
healthy Incident candidate count 0
revoked Approval denial
expired Approval denial
state drift denial
fault no longer present denial
idempotency same request returns same object
idempotency conflicting request rejects
duplicate executor dispatch produces one external effect
existing receipt prevents second dispatch
verification with insufficient requests fails
VERIFICATION_FAILED does not re-enter executor
cleanup ignores no unknown resource
```

## 14.2 Presentation tests

检查：

```text
README current state = v0.4.1
README links resolve
STATUS and LIMITATIONS agree
API routes match FastAPI source
architecture asset exists
HTML IDs unique
HTML internal links resolve
file:// rendering works
mobile / desktop / print layout
old v03 handbook still exists
all measured claims exist in claim map
```

## 14.3 Full validation

最终运行：

```text
focused v0.4.1 tests
existing Product v0.4 verifier
existing minimal Payment verifier
new v0.4.1 closeout verifier
full pytest
Ruff
mainline mypy
git diff --check
exact-head GitHub CI
```

---

# 15. Independent Review

独立 Reviewer 需要分别检查：

## 15.1 Safety review

```text
- S0–S4 是否真实使用当前 Product；
- 零写入案例是否有数据库和 gateway 双重证据；
- S3 是否确实只有一个外部 effect；
- S4 是否没有把验证失败说成配置恢复失败；
- cleanup 是否完整；
- 没有隐藏失败或重写历史。
```

## 15.2 Presentation review

```text
- README 是否在 20 秒内讲清价值；
- v0.3 与 v0.4 结果是否正确分层；
- “Diagnosis 无权”与“受限恢复可用”是否不冲突；
- 指标和限制是否都有证据；
- 简历和面试表述是否不过度技术化；
- 完整 28 服务失败没有被删除或假装成功。
```

## 15.3 Final review terminal

要求：

```text
PASS
Must Fix = 0
Claim Accuracy = PASS
Safety Matrix = PASS
Presentation = PASS
Merge = ALLOW
```

---

# 16. Final Integration

正向完成后：

1. 将 PR 从 Draft 标记 Ready；
2. 等待 required checks；
3. squash merge；
4. 验证 reviewed tree 与 merged tree 一致；
5. 发布 completion comment；
6. 再执行历史 Draft PR 的 superseded comments 和 close；
7. 验证 `main`、README、STATUS 和结果包一致；
8. 确认本地 worktree 干净、owned 资源为零。

不要求创建 GitHub Release 或 Tag。

---

# 17. Definition of Done

## Presentation

- [ ] README 更新为 v0.4.1。
- [ ] 四阶段项目演进清晰。
- [ ] v0.4 真实恢复结果位于首页可见区域。
- [ ] STATUS 与 main 一致。
- [ ] ARCHITECTURE 包含受限恢复层。
- [ ] 新 v041 Mermaid 和 SVG 存在。
- [ ] REMEDIATION.md 完成。
- [ ] LIMITATIONS 不再声称“无真实恢复”。
- [ ] QUICKSTART 增加恢复 Fixture 和 Live Evidence Verifier。
- [ ] API.md 包含当前 remediation routes。
- [ ] OPERATIONS 说明默认关闭和独立 Executor。
- [ ] PROJECT_PITCH 更新。
- [ ] v041 HTML 手册完成并通过视觉检查。
- [ ] v03 手册保留。

## Live Safety

- [ ] S0 Healthy Non-Action 通过。
- [ ] S1 Revoked Approval 通过。
- [ ] S2 State Drift 通过。
- [ ] S3 Idempotent Replay / Single Effect 通过。
- [ ] S4 Verification Failure / No Retry 通过。
- [ ] 未授权写入为 0。
- [ ] 重复写入为 0。
- [ ] Provider / LLM 调用为 0。
- [ ] 每个 Case cleanup CLEAN。
- [ ] 最终 owned 资源 0/0/0。
- [ ] non-owned 资源未变。

## Timing

- [ ] 关键事件有 UTC 与 monotonic 时间。
- [ ] S3 完整时间线可重建。
- [ ] S0–S2 有 safe-denial latency。
- [ ] S4 有 verification-failure latency。
- [ ] 未将单次观测称为通用 mean 或 SLO。

## Repository

- [ ] Claim map 完成。
- [ ] Result package 完成。
- [ ] PROJECT_EVOLUTION 更新。
- [ ] PR #95、#97–#101 添加 superseded comment。
- [ ] PR #95、#97–#101 关闭但不合并。
- [ ] 历史分支与证据保留。

## Quality

- [ ] 独立 Safety Review 通过。
- [ ] 独立 Presentation Review 通过。
- [ ] 完整测试通过。
- [ ] Ruff 通过。
- [ ] mypy 通过。
- [ ] Exact-head CI 通过。
- [ ] Final content closure 通过。
- [ ] Closeout PR 合并到 main。
- [ ] Merged tree 等于 reviewed tree。

---

# 18. Completion Terminal

正向完成：

```text
goal_complete_checkpoint
ECOMSRE_PRODUCT_V041_PRESENTATION_AND_LIVE_SAFETY_CLOSEOUT_COMPLETE
```

完成记录至少包含：

```yaml
terminal:
starting_main:
final_runtime_head:
reviewed_head:
merged_main:
merged_tree:
goal_sha256:
safety_cases:
timing_summary:
presentation_files:
claim_map:
validation:
review:
historical_pr_closeout:
cleanup:
known_limitations:
```

---

# 19. Claim Boundary After v0.4.1

可以说：

> EcomSRE-Agent 已在本地多服务环境完成证据驱动诊断与环境知识演化，并在 pinned
> Minimal Payment 环境中完成一次真实配置故障的状态绑定受限恢复。v0.4.1 进一步用
> Live Safety Matrix 验证健康状态、撤销批准和状态漂移时保持零写入，重复请求只产生
> 一次外部恢复，恢复验证证据不足时转人工且不自动重试。

不能说：

```text
- 生产自主自愈；
- 所有故障都能恢复；
- 跨环境恢复泛化；
- 完整 28 服务 Harness 已通过；
- 外部副作用 exactly-once；
- 无需人工治理；
- 高可用、多租户或 Kubernetes 已完成；
- 当前单次时间指标是生产 SLO；
- Safety Matrix 覆盖所有攻击面。
```

---

# 20. Recommended Resume Wording

文档和实测通过后，面试材料可以提供如下候选版本：

```text
将研究原型工程化为 FastAPI / Worker / SQLite / CAS 的 SRE Agent Product，
设计 Diagnosis → Candidate → Approval → 状态绑定授权 → 隔离执行
→ Receipt → 双窗口验证的受限恢复链路；在真实 Payment 配置故障中完成一次固定
Baseline 回滚，两个恢复窗口均为 39 次请求 / 0 错误，并通过 Live Safety Matrix
验证撤销批准、状态漂移与重复请求不会产生未授权或重复写入。
```

不要自动替用户声明全部个人独立完成；最终文档保留职责提示。

---

# 21. Copyable Activation Prompt

```text
Read and execute:

docs/goals/EcomSRE_Product_v0.4.1_Presentation_and_Live_Safety_Evaluation_Goal.md

Treat it as the active Goal Contract.

从当前包含 PR #102 的 main 开始，完成 v0.4.1 收尾：

1. 更新 README、STATUS、ARCHITECTURE、REMEDIATION、LIMITATIONS、
   QUICKSTART、API、OPERATIONS、PROJECT_PITCH 和新的 v041 HTML 手册；
2. 在真实 Minimal Payment 环境完成 S0–S4 Live Safety Matrix；
3. 输出真实诊断、授权、执行、恢复和拒绝时间分解；
4. 建立 claim-to-evidence map；
5. 通过独立审查、完整测试、mypy、Ruff 和 exact-head CI；
6. 合并 closeout PR；
7. 对 PR #95、#97、#98、#99、#100、#101 添加 superseded 说明并关闭，
   不合并、不删除分支、不改写历史证据。

本次激活是对文档范围内仓库编辑、提交、推送、PR、CI、独立审查、本地 owned Docker、
Payment 故障、Approval / Revocation / Attempt、安全拒绝案例、一次固定恢复、
验证失败案例和精确 cleanup 的持续授权。

不要在普通工程失败、案例结束、PR、Review、CI 或 Cleanup 后再次向我申请授权。
只要仍能在当前 Goal 范围内安全修复，就保留失败证据、清理、修复并继续。

不增加新 Runbook，不降低诊断或安全门槛，不运行完整 28 服务验收，不接生产或远程环境，
不修改未知资源，不使用全局 Docker cleanup。

持续执行，直到：
- ECOMSRE_PRODUCT_V041_PRESENTATION_AND_LIVE_SAFETY_CLOSEOUT_COMPLETE；
- 无法安全清理或出现未知资源边界；
- 或证明该收尾无法在不改变 Product 核心语义的情况下完成。
```

# EcomSRE-Agent 面试讲稿 · v0.4.1

项目结果与个人职责分开说明。以下是项目讲法；请按实际参与范围补充“我负责”的设计、实现、实验或审查，不能自动声称全部独立完成。实测入口见 [STATUS](../product/STATUS.md) 与 [claim map](../analysis/product-v041-claim-map.json)。

## 20 秒版

这是一个面向微服务的可验证 SRE Agent。它先检查证据能否支持诊断，未知故障经人工门控沉淀为环境知识；真实恢复则要求独立批准和新鲜状态授权。当前已完成一个本地 Payment 配置故障的固定回滚与双窗口恢复验证，仍是单租户原型。

## 90 秒版

我用这个项目研究如何让诊断结论和真实动作都能被证据约束。服务别名、遥测缺失和采样噪声会影响定位，所以 Runtime 维护身份、Baseline、覆盖度、谓词和引用。已知故障走确定性规则；有强异常却未命中的事件形成指纹与故障族，经人工确认、规则挖掘和 Shadow 评估后成为环境扩展。

工程层把它接到 FastAPI、Worker、SQLite 和内容寻址证据库。恢复层继续分权：Diagnosis 没有写权限，Candidate 也不能执行；Approval 后还要重新读取目标和配置状态，绑定一次授权，再提交 WriteIntent，由独立 Executor 执行固定动作。Receipt 证明动作结果，两个业务窗口证明恢复。

可引用的结果是 v0.2.4 健康 30/30 checkout 与 NO_INCIDENT，v0.3 一个 Kafka 故障族与新窗口 H1 命中，以及 v0.4 一次 Payment 恢复，两个窗口各 39 请求、0 错误。v0.4.1 正在补充真实安全拒绝与重复执行证据。它不证明生产自主自愈，也没有跨环境恢复泛化。

## 四个核心贡献面

| 面向 | 技术取舍 | 证据 / 代码 |
| --- | --- | --- |
| 证据驱动诊断 | 缺失不当反证，Runtime 维护准入 | [diagnosis bridge](../../src/ecomsre/product/incidents/diagnosis_bridge.py) |
| Product 工程化 | API / Worker、SQLite 租约、CAS 持久化 | [Product](../../src/ecomsre/product) |
| 人引导知识演化 | 小样本确定性指纹、规则挖掘、Shadow、显式 Promotion | [knowledge runtime](../../src/ecomsre/product/knowledge/runtime.py) |
| 状态绑定受限恢复 | 批准与执行分离，单次固定写入，外部双窗口验证 | [remediation](../../src/ecomsre/product/remediation) |

这张表列出项目可讨论的贡献面，不是个人独立完成证明。OTel Demo 微服务、OpenTelemetry、Prometheus 等是上游组件。

## 两条简历候选表述

- 将 SRE 研究原型工程化为 FastAPI / Worker / SQLite / CAS Product，使用类型化遥测与证据准入支持确定性诊断，并通过故障族、规则挖掘、Shadow 与人工门控形成环境级知识扩展。
- 设计并验证 Diagnosis → Candidate → Approval → 状态绑定授权 → 隔离执行 → Receipt → 双窗口验证的受限恢复链路；在本地真实 Payment 配置故障中完成一次固定 Baseline 回滚，两个恢复窗口均为 39 次请求 / 0 错误。

仅在与实际职责一致时使用“设计”“工程化”“验证”等动词。Safety Matrix 尚未收口前不把其预期结果写进简历。

## 高频问答

**为什么不让 LLM 直接调用工具恢复？** 模型文本不能作为目标或权限的来源。Runtime 与 Registry 固定动作、参数和状态门槛；LLM 可辅助命名/解释，本次 Product live 结果 Provider calls = 0。

**Approval 为什么还不够？** 它记录用户对范围的同意，但当前状态可能变了。Attempt 重新读取状态，绑定身份、Baseline、批准和时限；执行前再验证。

**Candidate、Approval、Authorization 差别？** 候选说明能考虑什么；批准说明人同意了什么；授权绑定这一次当前状态下允许执行的固定动作。

**为什么不是自主自愈？** 需要显式授权，只有一个本地 Payment Runbook；不支持自由选择动作或循环试错。

**APPLIED 为什么不等于 RECOVERED？** 前者是动作 Receipt，后者需要两个独立窗口满足业务请求数与错误率等条件。配置恢复但业务证据不足时必须转人工。

**重复调用如何处理？** API Idempotency-Key 绑定语义请求；WriteIntent 与 gateway 单次消费提供额外防线。重复执行已有 intent 进入拒绝/协调路径，不建立通用 exactly-once 声明。

**不确定写入怎么办？** 保留 OUTCOME_UNKNOWN 与证据，转人工，不因超时直接再发一次。cleanup 不改写原终态。

**为什么不用 embedding 聚类？** 当前是小样本环境内确定性加权指纹 baseline，便于解释根服务、领域与异常来源。没有对 embedding 做优越性结论，拆分/误合并与跨环境效果仍需评估。

**Shadow 1.0 / 0.0 能证明什么？** 只是在三个正例和十个负向/反事实/失败用例上的观测；其他扩展分层不可用，不能推导通用准确率。

**MTTD / TTR 怎么讲？** 单次观测称 Observed Time to Detect / Verified Recovery，标明起止事件、样本数和 UTC/monotonic 来源。不能叫平均值、稳定 P95 或生产 SLO；缺失写 NOT_MEASURED。

**健康为什么还会证据不足？** Minimal 有真实健康 Payment 请求，但 Logs / Runtime / Traces 没有提供给健康 Diagnosis，所以保留 INSUFFICIENT_EVIDENCE。v0.2.4 的 NO_INCIDENT 属于另一完整健康证据窗口。

**你本人做了什么？** 按实际职责补充决策、编码、实验和 review 的范围，并指出上游组件与 AI 协作部分。仓库结果不能自动证明个人贡献占比。

## Safety Matrix 与时间线

[真实安全矩阵](../results/product-v041-live-safety/README.md)围绕健康、撤销批准、状态漂移、重复请求与验证证据不足。预期零写入案例不能用 fixture 替代 live。结果完成后按 [timing summary](../results/product-v041-live-safety/timing-summary.json)解释诊断、授权、执行与窗口等待的分解。

## 代码阅读路线

1. [app.py](../../src/ecomsre/product/app.py)：API 与仓储注入。
2. [diagnosis_bridge.py](../../src/ecomsre/product/incidents/diagnosis_bridge.py)：诊断分路与证据缺口。
3. [knowledge/runtime.py](../../src/ecomsre/product/knowledge/runtime.py)：指纹、规则与 Shadow。
4. [remediation/api.py](../../src/ecomsre/product/remediation/api.py)：Candidate / Approval / Attempt 接口。
5. [attempts.py](../../src/ecomsre/product/remediation/attempts.py)：fresh state、授权、WriteIntent。
6. [executor.py](../../src/ecomsre/product/remediation/executor.py) → [payment_control.py](../../src/ecomsre/product/remediation/payment_control.py)：隔离与单次消费。
7. [recovery.py](../../src/ecomsre/product/remediation/recovery.py) → [verifier.py](../../src/ecomsre/product/remediation/verifier.py)：Receipt 与双窗口评估。
8. [live_safety_v041](../../scripts/product/live_safety_v041)：独立环境、真实 API 与追加写审计。

## 失败教训与 Claim Boundary

完整 28 服务 Harness 的负向实验与精确 cleanup 仍是历史事实；Minimal 成功只建立更窄的 Payment 结果。早期 restored-but-unverified 不能称恢复成功。状态、权限和证据必须分别成立，CI 通过不能替代真实环境证据。

不能声称生产自主自愈、所有故障恢复、跨环境泛化、exactly-once、HA、多租户、Kubernetes 或长期 SLO。见 [演进历史](../history/PROJECT_EVOLUTION.md)。

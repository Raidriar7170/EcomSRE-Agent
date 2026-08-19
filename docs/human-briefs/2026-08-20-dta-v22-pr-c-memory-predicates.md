# DTA v2.2 PR-C Human Brief

状态：`REVIEW_PENDING`

本 PR 在 PR-B 的只读 Action Catalog 与查询语义之上，新增 Full/Salient 两种证据记忆、baseline-relative 特征、精确 Memory Loss Ledger、development-frozen Evidence Predicates、DNF alternative support clauses、No-Incident 广覆盖判定、Diagnosis V22 admission 与 predicate-aware Candidate filter。固定 trajectory benchmark 不调用 Provider，也不改变 action sequence。

由于冻结的 PR-B `RuntimeRecordV22` 仅含 state、health 与 restart count，本 PR 不修改 PR-B 文件，而是通过 `RuntimeReadOutcomeV22.from_pr_b` 同时绑定 canonical `EvidenceActionV22`、PR-B `ReadOutcomeV22`，以及既有 v2 `ReadToolObservation` 的 `ReadAuthorityContext` 和 artifact SHA。PR-C 从后者保留 bounded `EndpointState` 与 exit code，并要求两条来源的 service、state、health、restart count 完全一致；同时用 observation 的 run ID、canonical target services 与 exact result limit 重建 v2 runtime request，要求 observation request SHA 完全相等。endpoint 不是 URL，因此 credential、私有路径或任意 host 不可能进入该字段。缺少这三层来源、替换 PR-B 或 v2 request SHA、或只重签投影而不改变权威 observation 都 fail closed。

冻结的 PR-B 测试有一条一次性断言硬编码 `PR_B_CLOSED_SURFACE`；PR-C 以后 verifier 正确进入 persistent-artifact 模式。为保持该测试与 PR-B manifest 字节不变，本 PR 仅在后续 stage 跳过这条旧标签断言，PR-C verifier 仍实际执行并要求 PR-B gate PASS。

安全与证据边界：

- Provider、Docker、held-out、scenario、fault、Runbook 均未执行；
- Agent live write authority 仍为 0，Candidate backend 仍为 `REPLAY_ONLY`；
- predicate extractor 不接收 evaluator truth、fixture、expected mechanism 或 case-specific threshold；
- Salient Memory 只能在原始 typed outcomes、baseline 与 top-K 上下文中验证，必须重建完全相同的 refs、summaries、predicates、selected facts 与 loss ledger；删除 predicate、篡改 summary/status/request SHA 或重签 ledger 均不可通过；
- 聚合日志的 retained fact count 与 represented evidence-ref count 分开计算：两个原始 record 被一个 retained fact 覆盖时 omitted record count 为 0；未被任何 retained fact 覆盖的 record 才计入 loss；
- 日志只保留去 credential、email、URL、绝对私有路径与 opaque token 的 normalized template；相同模板聚合并记录 count；
- trace Top-K 固定为 first error、error span、slowest causal edge；
- runtime 的 endpoint 使用 v2 `EndpointState` 枚举，exit code 来自同一 authority-bound observation；非 RUNNING 状态不得声称 healthy，Predicate extractor 也不会为其生成 `RUNTIME_HEALTHY`；
- No-Incident 要求所有 candidate 的 healthy runtime、完整 core metric support、error/latency 两类 anomaly-evaluable baseline 覆盖且不存在 strong anomaly；缺失 baseline 必须拒绝，baseline mean 为 0 时按冻结的绝对增量阈值判定，不能因 ratio 不可计算而放行；
- recent change 只有 `IN_PROGRESS`、`COMPLETED` 或 `ROLLED_BACK` 才产生 `CHANGE_RECENT_ROLLOUT`，`PLANNED`/`CANCELLED` 不参与 incident support；单独的 benign eligible change 不构成 No-Incident anomaly；
- PR-B canonical metrics 没有 memory metric 取数面，因此移除不可达的 `memory-leak:growth-and-memory-metric` clause 与死 predicate；Memory Leak 仍由可达的 growth+log 或 growth+restart alternative clauses 支持；
- `UNKNOWN` 不能成为 `DIAGNOSED`，没有显式 insufficiency 条件时不能平滑成 `ABSTAIN`；irrelevant/incomplete supporting refs 明确终止为 `FAILED`，contradicting refs 不会被静默忽略；
- Diagnosis 的 root service/entity 必须与 exact target 一致；Candidate Registry 固定绑定 v2.1 exact Runbook Registry 的 catalog 与 raw semantic hashes，不接受调用者自报 `trusted`，持久化 `CandidateSetV22.registry_sha256` 也必须等于该 exact registry；
- PR-C verifier 无法无循环地把自身 raw hash 写进自身读取的 manifest，因此本 PR 不声称 self-hash。当前执行锚是 exact Git commit + exact-head CI，verifier 本身仍在 persistent secret/private scan 中；PR-D 的 DEC-055 successor attestation 必须从 PR-C merge tree 冻结它的 raw SHA-256。

合并前仍需：PR-B successor attestation、PR-C exact-head CI、独立 reviewer `Must Fix = 0 / Should Fix = 0 / Claim Accuracy = PASS`，以及 manifest、truth-isolation、secret scan 与历史绑定全部 PASS。满足前不得宣称 `DTA_V22_PR_C_MEMORY_PREDICATES_READY`。

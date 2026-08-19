# DTA v2.2 PR-C Human Brief

状态：`REVIEW_PENDING`

本 PR 在 PR-B 的只读 Action Catalog 与查询语义之上，新增 Full/Salient 两种证据记忆、baseline-relative 特征、精确 Memory Loss Ledger、development-frozen Evidence Predicates、DNF alternative support clauses、No-Incident 广覆盖判定、Diagnosis V22 admission 与 predicate-aware Candidate filter。固定 trajectory benchmark 不调用 Provider，也不改变 action sequence。

由于冻结的 PR-B `RuntimeRecordV22` 仅含 state、health 与 restart count，本 PR 不修改 PR-B 文件，而是要求 runtime memory 使用 PR-C 的完整 `RuntimeReadOutcomeV22`：state、health、endpoint、restart count 与 exit code 同时进入 record SHA、outcome SHA、evidence ref 与 Full Memory。旧的 runtime outcome 缺少 enrichment 时 fail closed；修改 endpoint/exit 而不重签整个权威 outcome 时也 fail closed。

冻结的 PR-B 测试有一条一次性断言硬编码 `PR_B_CLOSED_SURFACE`；PR-C 以后 verifier 正确进入 persistent-artifact 模式。为保持该测试与 PR-B manifest 字节不变，本 PR 仅在后续 stage 跳过这条旧标签断言，PR-C verifier 仍实际执行并要求 PR-B gate PASS。

安全与证据边界：

- Provider、Docker、held-out、scenario、fault、Runbook 均未执行；
- Agent live write authority 仍为 0，Candidate backend 仍为 `REPLAY_ONLY`；
- predicate extractor 不接收 evaluator truth、fixture、expected mechanism 或 case-specific threshold；
- Salient Memory 只能在原始 typed outcomes、baseline 与 top-K 上下文中验证，必须重建完全相同的 refs、predicates 与 selected facts；删除 predicate 后仅重算 memory hash 不可通过；
- 日志只保留去 credential、email、URL、绝对私有路径与 opaque token 的 normalized template；相同模板聚合并记录 count；
- trace Top-K 固定为 first error、error span、slowest causal edge；
- No-Incident 要求所有 candidate 的 healthy runtime、完整 core metric support 且不存在 strong anomaly，单独的 benign recent change 不构成 anomaly；
- `UNKNOWN` 不能成为 `DIAGNOSED`，没有显式 insufficiency 条件时不能平滑成 `ABSTAIN`；contradicting refs 不会被静默忽略；
- Diagnosis 的 root service/entity 必须与 exact target 一致；Candidate Registry 固定绑定 v2.1 exact Runbook Registry 的 catalog 与 raw semantic hashes，不接受调用者自报 `trusted`。

合并前仍需：PR-B successor attestation、PR-C exact-head CI、独立 reviewer `Must Fix = 0 / Should Fix = 0 / Claim Accuracy = PASS`，以及 manifest、truth-isolation、secret scan 与历史绑定全部 PASS。满足前不得宣称 `DTA_V22_PR_C_MEMORY_PREDICATES_READY`。

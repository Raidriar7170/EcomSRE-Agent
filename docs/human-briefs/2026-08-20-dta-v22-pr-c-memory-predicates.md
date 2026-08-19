# DTA v2.2 PR-C Human Brief

状态：`REVIEW_PENDING`

本 PR 在 PR-B 的只读 Action Catalog 与查询语义之上，新增 Full/Salient 两种证据记忆、baseline-relative 特征、精确 Memory Loss Ledger、development-frozen Evidence Predicates、DNF alternative support clauses、No-Incident 广覆盖判定、Diagnosis V22 admission 与 predicate-aware Candidate filter。固定 trajectory benchmark 不调用 Provider，也不改变 action sequence。

由于冻结的 PR-B `RuntimeRecordV22` 仅含 state、health 与 restart count，本 PR 使用独立且哈希绑定的 `RuntimeObservationDetailV22` 补充 endpoint 与 exit code；两种 memory 表示必须绑定同一份明细，未提供、错配或重哈希伪造均 fail closed。PR-B 文件保持字节不变。

安全与证据边界：

- Provider、Docker、held-out、scenario、fault、Runbook 均未执行；
- Agent live write authority 仍为 0，Candidate backend 仍为 `REPLAY_ONLY`；
- predicate extractor 不接收 evaluator truth、fixture、expected mechanism 或 case-specific threshold；
- Salient Memory 保留全部 refs 与 predicates，并强制保留全部 supported core metrics 和 bounded runtime state；
- No-Incident 要求所有 candidate 的 healthy runtime、完整 core metric support 且不存在 strong anomaly，单独的 benign recent change 不构成 anomaly；
- `UNKNOWN` 不能成为 `DIAGNOSED`，没有显式 insufficiency 条件时不能平滑成 `ABSTAIN`。

合并前仍需：PR-B successor attestation、PR-C exact-head CI、独立 reviewer `Must Fix = 0 / Should Fix = 0 / Claim Accuracy = PASS`，以及 manifest、truth-isolation、secret scan 与历史绑定全部 PASS。满足前不得宣称 `DTA_V22_PR_C_MEMORY_PREDICATES_READY`。

# DTA v2.2 PR-D Human Brief

状态：`REVIEW_PENDING`

本 PR 在 PR-C 的 Salient Memory、Evidence Predicates 与 Diagnosis admission
之上，实现 Runtime 管理的共同 bootstrap、封闭 Hypothesis Catalog、持久
Belief Ledger、Flat Canonical、Planner-Lite、Deterministic Router、One-shot
Oracle Context、共享轻量 `ControllerDecisionV22`、一次 bounded correction、
Provider output-mode probe、四个 identity manifest，以及 protocol-only
synthetic capability suite。

Provider protocol gate 使用冻结模型 `gpt-5.4-mini-2026-03-17`。正式执行先以
相同的五字段 Controller schema 探测 strict structured output；首个探测成功，
因此四个 arms 全部冻结为 `STRICT_STRUCTURED_OUTPUT`，没有 fallback 或静默换模。
正式 suite 覆盖 50 个 state transitions，Flat 与 Planner-Lite 各 25 个：

- first-pass protocol acceptance：48/50 = 0.96；
- post-correction protocol acceptance：50/50 = 1.00；
- correction：2/50 = 0.04，分别是 stale action 与 invalid ref；
- invalid dispatches：0；
- Provider protocol calls：52，另有 1 个 mode probe call；
- terminal：`PROVIDER_PROTOCOL_GATE_PASS`。

安全与证据边界：

- suite 只执行 synthetic protocol state，不是 RCA development/held-out；
- 没有 Agent evidence read dispatch、Agent write、Runbook、Docker、scenario、
  fault injection 或 held-out activity；
- correction 消耗 Provider turn/tokens，但产生 0 read dispatch、0 write authority；
- 第二次 invalid decision 明确终止为 `FAILED`；
- public summary 只保留 aggregate counts、identity/schema/report digests、token
  accounting 与 terminal，不公开原始 Provider content；
- 完整 typed report 是 repository 外的 create-once `0600` private evidence，
  public summary 通过 exact report hash 与 response-digest-set hash 绑定它；
- formal runner 绑定执行前 clean implementation commit/tree，拒绝非 `0600`
  provider env、shell expansion、非目标模型与 repository 内 private report；
- Flat 与 Planner-Lite 的 schema、model、bootstrap、Action Catalog、Salient
  Memory、预算与 correction 完全相同；只有 Planner-Lite 收到持久
  `BeliefLedgerView`；
- Deterministic Router 只从 canonical hypotheses/actions 运行 generic policy，
  不接收 truth、fixture、case ID 或 expected mechanism；
- One-shot 明确标为 context upper bound，完整 materialization bytes/tokens 计入，
  model tool-selection metric 为 `N/A`。

PR-C 冻结 manifest 曾误把 PR-D 所有 successor activity flags 都要求为 false，
这与本 Goal 要求的 Provider protocol gate 及 DEC-055 的真实活动记录冲突。
DEC-056 仅对 PR-C→PR-D attestation 做 append-only 修正：`provider_called`、
`private_evidence_changed`、`public_result_changed` 为 true；Docker、held-out、
scenario、fault、Runbook 与 execution-report rebinding 均为 false。原 PR-C
manifest 保持 byte-identical，不得用 false flag 伪造无活动历史。

合并前仍需：PR-C successor v2 attestation、PR-D manifest/verifier、exact-head
GitHub Actions、独立 reviewer `Must Fix = 0 / Should Fix = 0 / Claim Accuracy =
PASS`，以及 historical binding、truth-isolation、secret/private-path gates 全部
PASS。满足前不得宣称 `DTA_V22_PR_D_CONTROLLER_READY`，也不得进入 PR-E
capture/freeze。

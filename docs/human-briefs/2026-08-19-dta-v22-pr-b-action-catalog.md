# DTA v2.2 PR-B 人工复核简报

状态：`PR_B_ACTION_CATALOG / REVIEW_PENDING / LOCAL_FOCUSED_PASS / GITHUB_CI_PENDING`

## 本 PR 完成什么

- 模型侧只选择稳定的 `action_id`；结果上限、metric bundle、trace 半径、
  sampling window 与 sample count 全由 runtime 的版本化 request contract 固定。
- `ActionCoverageV22` 以 digest 绑定已执行 action 与能力覆盖；catalog builder
  只接收 candidate services、静态拓扑、capability registry、已执行/已覆盖集合与
  剩余预算；接口不接收 truth、fixture、expected source/mechanism 或
  fault-controller state。
- dynamic mask 会移除已执行、已覆盖、被 dominance 覆盖、source unavailable 与
  over-budget action，因此 exact duplicate dispatch 不再依赖 prompt 约束。
- replay backend 按 canonical query 过滤完整 capture：空日志是
  `SUCCESS_EMPTY`，零样本 metric 是 `UNSUPPORTED`，trace 只返回 requested
  service 的 bounded radius-one neighborhood。
- 新增只读 `CHANGES` record contract；仅允许 opaque change ID、service、UTC
  timestamp、closed category、rollout state 与 revision digest。

## 安全与证据边界

- 本 PR 未调用 Provider、未操作 Docker、未运行历史 held-out、未执行 Runbook，
  Agent live write authority 仍为 `0`。
- DTA v2 与 v2.1 历史绑定继续由 PR-A verifier 强制检查。
- 当前只支持 deterministic contract/replay 工程结论；不声称 Planner 优势、
  memory 优势、线上自适应能力或生产自治。

## 人工复核重点

1. 同一 action ID 是否可能被重新绑定到另一组 query 参数。
2. catalog builder 是否存在读取 truth、fixture 或 fault-controller 的旁路。
3. runtime all-candidates dominance 与 low-budget fallback 是否一致。
4. trace filtering 是否保留 operation、parent、path、first-error 与 duration，且
   wrong target 无法取得整个 fixture。
5. Changes schema 是否可能泄漏 fault flag、injected variant、expected mechanism、
   expected Runbook 或 source-control locator。

满足 exact-head CI、独立复审 `Must Fix = 0` 与 `Claim Accuracy = PASS` 后，退出标记为：

`DTA_V22_PR_B_ACTION_CATALOG_READY`

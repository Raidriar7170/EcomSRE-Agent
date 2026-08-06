# Phase 5B v2 Analysis-Only Repair 人工审查简报

## 当前结论

Phase 5B v1 scoring 已以
`PHASE5B_V1_TERMINATED_GROUND_TRUTH_CONTRACT_MISMATCH` 安全终止。180 个
main terminal records 与 38 个 ablation gap terminal records 均保留；v1 未创建
scoring bundle、final report 或 final disposition，也未重跑 Provider、Agent 或
scored run。

Phase 5B v2 当前仅为 `READY_FOR_REVIEW`。v2 analysis 尚未执行，输出 bundle 与
report 均不存在。

## v2 修复边界

- 输入仍是同一批 immutable Phase 5B v1 execution records。
- v2 protocol 绑定原 execution-report、unblinding-record、schedule、freeze、pack
  与 Ground Truth aggregate SHA。
- diagnosis output、decision/root/mechanism truth、Prompt、Agent runtime、schedule
  与 budgets 均不变。
- hidden truth 的 private `difficult_subsets` 不进入评分 allowlist；secondary subset
  grouping 只按 public preregistered `_SUBSETS_BY_TEMPLATE` 与 `template_id` 推导。
- primary population 仍是 hidden-only；统计规则仍是 10,000 次 hierarchical paired
  bootstrap 与 frozen claim classification。
- Provider calls 固定为 0；v2 output root 必须与 repo、v1 execution root 和 Ground
  Truth root 完全分离，并使用 create-once 输出。
- `analyze` 在任何 scoring/bootstrap 前原子创建 exclusive attempt marker；marker 一旦
  存在，后续调用（包括首次调用中途失败后的重入）必须 fail closed。

## 已验证证据

- v1 termination disposition 为安全聚合记录，不包含隐藏答案、truth 内容或未知
  private subset 标签。
- 只读 preflight 已验证 v1 lifecycle chain、180 条 raw-record SHA manifest、30/30
  Ground Truth admission，以及 v1 scoring/final artifacts 仍不存在。
- 自动测试覆盖 primary score 与 private subset metadata 解耦、raw/truth hash 不变、
  unknown private labels 不影响 primary scoring，以及 public template mapping 决定
  secondary grouping。

## 人工审查门

审查者应确认 v2 只改变 analysis-time subset projection，并确认 v2 protocol、freeze
与 review disposition 的所有 SHA binding。通过审查前不得设置 v2 analysis
authorization，不得运行 `analyze`。审查通过后仅允许执行一次 v2 analysis；该授权仍
不包含 Provider 调用、scored-run 重跑、merge、release 或 production claim。

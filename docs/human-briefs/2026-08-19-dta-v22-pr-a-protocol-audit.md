# DTA v2.2 P0 PR-A Human Brief

状态：`PR_A_PROTOCOL / REVIEW_COMPLETE / LOCAL_EXACT_HEAD_PASS / GITHUB_CI_PENDING`

## 这次做了什么

PR-A 只冻结 v2.2 的协议、审计与历史边界，没有实现 Action Catalog、
Planner-Lite、Provider 调用、Docker capture 或 held-out evaluation。

- 新增独立 `v22` namespace 与 Master Progress 起点；
- 用精确文件 SHA-256、PR #55/#56 commit/tree 和 Git ancestry 绑定 DTA v2、
  v2.1 历史；
- 以代码路径为证据记录 v2.1 Planner、Compact Context、Replay、Diagnosis、
  Candidate Filter 与 scorer 的结构问题；
- 冻结共享 Controller schema、runtime-owned state、canonical action、semantic
  predicates、factorial/paired evaluation、零 Agent live write 和 provenance
  七项决策；
- 从既有私有 held-out evidence 仅导出聚合 failure counts，不公开 raw response、
  per-case mapping、rationale、私有路径或凭据。

## 当前真实状态

- `origin/main` 与 Goal 检查过的 `9da92d5...` 完全一致；
- DTA v2/v2.1 frozen scope 未发生字节变化；
- PR-A focused tests、Ruff、mypy、historical verifier 已通过；
- closed-world 17-path scan、truth isolation 与 secret/private-path gate 已通过；
- 独立只读 Review 与两轮 exact-head repair re-review 均达到 Must Fix = 0、
  Claim Accuracy = PASS；
- 全仓首次运行因新 worktree 未初始化 pinned submodule 产生 54 个环境失败，
  初始化精确 `1755859...` 后这些路径全部通过；
- 首次 clean exact-head 全量结果为 4520 passed、3 failed、5 skipped。三项失败
  都来自历史 verifier 被错误地用于未来 HEAD；恢复 Phase 5B 的 byte-bound
  Makefile，并将 v2.1 PR-F verifier 固定在 PR #56 历史 checkout 后，原三项
  targeted regression 已全部通过；修复后本地 clean exact-head 全量结果为
  4524 passed、0 failed、5 conditional skips。
- main 既有的 frozen v2.1 mypy `arg-type` debt 未修改源码；该精确模块已新增
  raw SHA-256 历史绑定，并仅为此模块关闭该单一 error code。其余 mypy 范围
  保持启用；verifier 会拒绝额外 error code、v2.1 wildcard 或 global bypass。

## 风险与边界

- PR-A 的文档状态不证明 v2.2 Planner 或 Salient Memory 有收益；
- `gpt-5.4-mini-2026-03-17` 可用性尚未做 Provider capability gate；
- 不得开始 PR-E capture/freeze，直到 PR-D protocol gate、query semantics、
  truth isolation、scorer self-tests 与 development gate 全部通过；
- v2.2 P0 Agent live write authority 仍为 `0`；
- 负面 empirical result 不是 blocker，不允许 retry-until-pass。

## 本 PR 验收

需要：focused/full tests、Ruff、mypy、diff-check、historical verifier、
secret/private-path scan、exact-head GitHub Actions 全绿；独立 Review 的
Must Fix 为 0 且 Claim Accuracy 为 PASS，才可 squash merge。

# DTA v2.2 PR-D Provider Boundary v4 Human Brief

## 结论

PR-D 保持阻断，精确终态为：

`BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE`

本次执行受 `DEC-058` 与 `dta-v22-pr-d-provider-boundary-v4-amendment-v1` 约束，冻结实现提交为 `ffdb8da89a8c5b96e13affc2b40394dab3b86b9c`。

## 可核验事实

- Commit A tree 为 `e6ede890ceccb07b5d55bc9ef692dd4217b22a80`；manifest SHA-256 为 `4d20e2341655fa0d1ece1ad2fe8dbc63db570580e113c2aa8e1c5864827c7c44`。
- exact-head Hosted CI 两条工作流均通过；独立复核结论为 Must Fix 0、Should Fix 0、Claim Accuracy PASS。
- 唯一 v4 campaign 在 output-mode probe 发出 1 次 Provider 请求后收到 HTTP 400；probe 判定 `supported=false`，未选择输出模式。
- Replicate A 与 B 均未开始，因此没有把不完整执行表示为 accuracy，也没有运行第二个 campaign。
- 公共 campaign SHA-256 为 `1837365119ac0cf1fcd2ddbd50199387c47bdf6dfd88cf3f0e4b87382453fc3a`；对应私有负证据已先于 runner 终态持久化并重新交叉验证。
- HTTP auto-retry、semantic retry、replacement replicate 与第三次 v3 replicate 均为 0。
- Docker、scenario、fault、Agent evidence、Agent write、Runbook 与 held-out 活动均为 0。
- Attempts 1–5 与全部 v3 证据保持原字节、原标签和原评分；Attempt 1 未被计为 PASS replicate。

## 边界

两个 v4 replicate 没有分别通过冻结门禁，因此不能声明 `DTA_V22_PR_D_CONTROLLER_READY`，不能开始 PR-E，也不能将 Draft PR #60 表示为 merge-ready。

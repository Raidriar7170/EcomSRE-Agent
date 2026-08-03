# Phase 0 冻结结论与 Phase 1 交接

本文件是 2026-07-31 的脱敏审阅面。原始本地 artifacts 仍是运行事实的
权威来源；本文件不复制 evaluator-only 内容，不修改或重新分类任何历史
run。

## 冻结结论

| Run ID | Verdict | 权威审阅面 | 主要原因 |
|---|---|---|---|
| `f1c9253b03dd4afca4284a89524562fb` | `UNSAFE` | smoke report | post-up ownership/evidence handoff 未闭合 |
| `5f43827ec1be3fb20a558f454ab391ad` | `FAILED` | smoke report | `INITIAL_CANDIDATE_READINESS_INCOMPLETE` |
| `95876a1561846bebc4ffe9f2bf2531b1` | `FAILED` | smoke report | `lifecycle runner authority is invalid` |
| `9650bb2e9657d5dbee864099f961541e` | `BLOCKED` | preflight snapshot | `COMPOSE_CONFIG_HASH_MISMATCH` |

目标交接文件把第三个 run 写成了 33 字符的
`95876a1561846bebc4ffef9f2bf2531b1`。本机权威 artifact 使用上表中的
32 字符 ID；机器可读 disposition 显式保存了这项拼写映射，未创建或伪造
不存在的运行证据。

## 最新 blocker

run `9650bb2e9657d5dbee864099f961541e` 的 preflight 期望 Compose hash：

```text
e8ad821d79fdcd4df2c7951bc0b87978ebfb63e18ef8676f95bd1d526893a2ea
```

同一 preflight 实际解析出的 run-interpolated Compose hash：

```text
613ee99fcaeecc60a4fd4e264c2c1172259779994ae605e00d36b92c93c2e47d
```

因此 image-lock binding 以 `BLOCKED_UPSTREAM /
COMPOSE_CONFIG_HASH_MISMATCH` fail closed。根本问题是 resolved Compose
包含 run interpolation，不能把一个 run 的 hash 直接复用于另一个 run。
这个 Phase 0 hash contract 问题明确不属于 Phase 1 Single-Agent RCA
replay scope。

## 不得扩大解释

- `Phase 0 complete = false`。
- Formal three-cycle acceptance 未执行。
- 不再授权任何 Phase 0 smoke。
- Phase 0 Draft PR 继续保持 `Draft / REVIEW_REQUIRED`，不得合并 PR #1。
- Phase 1 获得的是独立的 replay-first Agent 开发授权；它不会补写、修复或
  改善任何历史 Phase 0 verdict。
- Phase 0 原始 live evidence 仍保留在 Git 忽略的本地 `artifacts/` 下；
  本次只提交脱敏摘要与当前代码状态。

## Phase 1 交接边界

下一阶段只实现只读、replay-first 的 Single-Agent RCA baseline。禁止
Multi-Agent、remediation、live Docker/HTTP、Phase 0 hash 修复、正式三轮
acceptance、训练和任何写操作工具。

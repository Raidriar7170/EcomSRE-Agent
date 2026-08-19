# DTA v2.2 PR-D Human Brief

状态：`BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE`

PR-D 仍是 Draft，未达到 `DTA_V22_PR_D_CONTROLLER_READY`，不得合并或进入
PR-E capture/freeze。

本轮修复把 READ 拆成模型选择、runtime 授权、真实 dispatch 和权威 outcome
四段；只有 outcome 到达后才更新 Belief Ledger、coverage 与 evidence cost。
COMMIT、NO_INCIDENT 和 ABSTAIN 都重新经过 PR-C Diagnosis admission，不能靠模型
终态字段绕过 semantic evidence policy。Provider 只接受绑定 identity、prompt、
ControllerTurnInput、Action Catalog、Salient Memory 和 request digest 的 typed
answer-free request。Deterministic Router 在动作耗尽后必须回到相同冻结模型完成
最终诊断，One-shot 必须物化全部 enabled sources。

本地确定性边界：

- DTA v2.2 focused：138 passed，1 个 predecessor closed-surface test 按设计
  skipped；
- Ruff：PASS；
- mypy Agent mainline scope（322 source files）：PASS；
- local 50-transition harness：48/50 first-pass、50/50 post-correction、0 invalid
  dispatches；它只证明本地协议路径，不代替 Provider gate；
- 全仓测试在实现锁定前为 4636 passed、7 skipped；唯一失败是 dirty-worktree
  guard，提交后该 guard 单测通过。

正式 Provider 尝试全部保留，不能删除或重标为通过：

1. attempt 1 的 Provider 语义指标通过，但 private evidence 写入了错误根目录，
   因此不是有效 gate evidence；
2. attempt 2 暴露本地 transition validator 会把普通 protocol failure 误判成
   报告结构损坏，未产出报告；
3. attempt 3 在无节流下收到 HTTP 429；
4. attempt 4 在 1.5 秒请求间隔下仍收到 HTTP 429；
5. attempt 5 在固定 3.0 秒请求间隔下完成 50 个 protocol calls，随后本地 gate
   返回 `provider_gate_eligible = false` 并抛出精确 blocker。

attempt 5 未执行自动 HTTP retry、未换模型、未执行 Agent evidence dispatch、
Agent write、Runbook 或 Docker。由于 runner 在负面 gate 后先抛错、后写证据，
该次精确 first-pass/post-correction counts 未持久化；因此不得用推测数字补写
summary，也不得把旧 attempt 1 的 48/50、50/50 冒充为当前结果。

当前公开事实仅支持：

```text
BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE
```

解除 blocker 需要新的、显式授权的 Provider 计划和对负面报告持久化缺口的协议
修订；不能通过继续重试、静默换模型或放宽 gate 完成。

# Product v0.4.1 Live Safety Evaluation

**IN_PROGRESS** — S0 已通过，S1–S4 尚在执行。每个案例是独立 pinned 本地 Minimal Payment 环境、独立 Product SQLite/CAS 与键空间；不使用纯 Fixture 替代真实案例。

[Live Safety Matrix](live-safety-matrix.json) · [Observed timing](timing-summary.json) · [S0](case-s0-healthy-non-action.json)

S0：健康业务与 Active Baseline；Diagnosis 仍是 `INSUFFICIENT_EVIDENCE`，Candidate 及所有恢复对象/写入为 0。未启动的 gateway 有 birth-bound 容器证据，DB 计数与固定控制传输审计共同证明无 Product 写入。

后续案例使用配置绑定后的第二个 API 进程，真实 HTTP 接口连接当前 Product 仓储；API 仅挂载读取 socket，不持有写 socket 或 gateway 私有配置。因此新 harness 创建 11 个容器（原 Minimal 10 个加 bound API），不是 PR #102 原来的 10 服务计数。

计数解释：gateway ledger 的消费发生在发送前；实际外部 effect 另由固定 flag transport 追加审计、APPLIED Receipt 和真实配置/flagd readback 证明。故障注入与清理恢复有独立控制器标记，不计为 Product 恢复。

Timing：同进程耗时用 monotonic；跨对象使用 UTC。轮询观测时间不会冒充实际 commit。gateway ledger 没有消费时间字段，因此该精确时间为 `NOT_MEASURED`；不从文件 mtime 或测试耗时推断。样本不足不报告平均、P95 或 SLO。

历史 v0.4 JSON、旧 v03 手册、失败 Draft PR 与失败尝试保持原有事实。当前结果不证明生产 self-healing、完整 28 服务 Harness、跨环境泛化、通用 exactly-once 或所有攻击面覆盖。

# Product v0.4.1 Live Safety Evaluation

**PASS — 五个独立真实案例全部通过。** 每例使用 pinned 本地 Minimal Payment、真实 Product HTTP API、独立 SQLite/CAS、键空间、资源标签与证据目录。每例样本数为 1。

[机器矩阵](live-safety-matrix.json) · [完整时间线](timing-summary.json) · [Human Brief](HUMAN_BRIEF.md) · [独立审查](REVIEW.md) · [Hash manifest](evidence-manifest.json)

| Case | 实测终态 | Product 外部写入 | Cleanup |
| --- | --- | ---: | --- |
| S0 Healthy | NO_CANDIDATE；健康 Diagnosis INSUFFICIENT_EVIDENCE | 0 | CLEAN |
| S1 Revoked | APPROVAL_REVOKED / NO_WRITE | 0 | CLEAN |
| S2 Drift | CONFIGURATION_DRIFT_NOT_VISIBLE / NO_WRITE | 0 | CLEAN |
| S3 Replay | RECOVERED；相同键返回原对象，第二次 run_one 拒绝 | 1 | CLEAN |
| S4 Evidence failure | APPLIED；VERIFICATION_FAILED / ESCALATE_HUMAN | 1 | CLEAN |

[S0](case-s0-healthy-non-action.json) · [S1](case-s1-revoked-approval.json) · [S2](case-s2-state-drift.json) · [S3](case-s3-idempotent-recovery.json) · [S4](case-s4-verification-failure.json)

未授权、重复、未知目标写入以及替代 Runbook 执行均为 0，Provider / LLM calls = 0。每例移除 11 容器 / 3 网络 / 2 卷，剩余 0/0/0，非 owned 资源未变。五个案例均首轮成功，无 live 失败重跑；离线检查修复保留于 Git 提交与完成记录。

## Authority and external effect

S0 没有 Candidate，gateway 未启动且有 birth-bound 证据。S1 保留真实 Revocation 与拒绝 trace；S2 的独立控制器恢复发生在 Attempt 之前，Attempt 自己的 CAS-bound Current State 也证明 Baseline/fault=false。DB、gateway ledger、固定传输的追加 INTENT/APPLIED 审计、Receipt 与真实 readback 交叉支持计数。

S3 重放 Candidate、Approval、Attempt 的相同键与请求；每类仅一个语义对象，真实第二次 run_one 返回 REMEDIATION_RECONCILIATION_REQUIRED，终态不变。S4 配置实际已回到 Baseline；恢复窗口主动不发业务探针，业务请求不足而拒绝宣布 RECOVERED，重复唤醒没有第二次写入。它不是配置恢复失败。

新 harness 额外创建绑定配置后的 API，故每例 11 容器，不沿用 PR #102 的 10 服务数字。该 API 无网络，仅通过固定容器内 loopback HTTP，挂载读取 socket，不持有写 socket/private gateway profile/Docker socket。镜像复用 PR #102，记录 image_source_head 与各例 harness source_head，完整构建输入保持相同。

外部审计的 peer witness 用于当前可信本地 harness 的归属核对；共享 NAT 地址不能证明对任意恶意同网主体的区分。矩阵只覆盖这五个固定案例，不覆盖所有攻击面。

## Observed timing

| Case | Metric | Observed ms | Clock |
| --- | --- | ---: | --- |
| S0 | `time_to_safe_denial_ms` | 3387.232 | same-process monotonic |
| S1 | `time_to_safe_denial_ms` | 385.232 | same-process monotonic |
| S2 | `time_to_safe_denial_ms` | 489.858 | same-process monotonic |
| S3 | `approval_to_authorization_ms` | 1314.748 | cross-object UTC |
| S3 | `approval_to_current_state_ms` | 1282.975 | cross-object UTC |
| S3 | `authorization_to_write_intent_ms` | 1721.609 | cross-object UTC |
| S3 | `cleanup_duration_ms` | 67910.962 | same-process monotonic |
| S3 | `diagnosis_job_latency_ms` | 262.028 | cross-object UTC |
| S3 | `diagnosis_to_candidate_ms` | 37719.518 | cross-object UTC |
| S3 | `fault_ack_to_diagnosis_completed_ms` | 86572.678 | cross-object UTC |
| S3 | `fault_ack_to_first_failure_ms` | 1016.632 | same-process monotonic |
| S3 | `fault_ack_to_verified_recovery_ms` | 179627.167 | cross-object UTC |
| S3 | `gateway_consumption_to_receipt_ms` | NOT_MEASURED | NOT_MEASURED |
| S3 | `receipt_to_first_success_ms` | 155.432 | cross-object UTC |
| S3 | `receipt_to_verified_recovery_ms` | 51231.283 | cross-object UTC |
| S3 | `receipt_to_window1_complete_ms` | 25854.67 | cross-object UTC |
| S3 | `write_intent_to_gateway_consumption_ms` | NOT_MEASURED | NOT_MEASURED |
| S4 | `receipt_to_verification_failed_ms` | 51380.386 | cross-object UTC |

同一 harness 进程使用 monotonic；跨对象使用持久化 UTC。Diagnosis.created_at 锚定入队，不是完成时刻；诊断延迟使用 job_events.CLAIMED → SUCCEEDED。Candidate 持久化使用第一次 POST 响应观测，包含固定传输开销。fault_ack → diagnosis/recovery 包含预设 75 秒故障确认与 harness 调度，不能解释成无人值守告警响应速度。

gateway ledger 没有精确消费时间字段；该事件及两项相关时延为 NOT_MEASURED。跨进程对象没有可比较的 monotonic，明确保留 NOT_MEASURED。未使用 mtime、测试耗时、插值或单次样本的平均/P95/SLO。

## Reproduce the evidence check

```bash
PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.ci.verify_product_v041_closeout
```

这只重新校验现有类型化证据与哈希，不启动 Docker 或重跑实验。历史 v0.4 JSON、旧 v03 手册及完整 Harness 失败记录保持原样。当前不证明生产 self-healing、跨环境泛化或通用 exactly-once。

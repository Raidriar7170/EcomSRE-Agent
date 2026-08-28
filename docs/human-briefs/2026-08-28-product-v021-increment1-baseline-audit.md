# Product v0.2.1 Increment 1 Human Brief

## 本阶段完成了什么

本阶段把 Product baseline 的“成功/失败”从一个聚合错误拆成逐窗口、逐来源的
可审计合同，并让真实 baseline builder 与公开 audit 共用同一个纯判定函数。

当前可声明的精确终端是：

```text
ECOMSRE_PRODUCT_V021_BASELINE_AUDIT_READY
```

SQLite 对失败 disposition 会在 builder 抛出前 create-once 保存 audit；对成功
disposition 则把 audit 与 baseline 放在同一个受 job fence 约束的事务中，避免中途
崩溃留下无法重试的半提交。Audit 同时显式绑定 logical query service 与 baseline
entity service ID 两个域，并把实际窗口数等于 policy 窗口数纳入 PASS。两个只读 API
可以按 environment 或 candidate baseline ID 取回同一份窗口证据。历史 manifest
同时绑定 PR #75 的 consumed marker、blocker、cleanup 和公开报告，防止 successor
改写旧结果。

## 现在不能声明什么

这不是 live readiness PASS，也不是 fault-profile PASS：

```text
baseline readiness attempts = 0
profile calibration iterations = 0
fault attempts = 0
accepted positive episodes = 0
held-out recurrences = 0
```

本阶段没有运行 Docker、没有控制 feature flag、没有调用 Provider、没有创建
Incident/Fault Family，也没有进入两个 Human Checkpoint。

PR #75 仍是：

```text
BLOCKED_ECOMSRE_PRODUCT_V02_UNKNOWN_FAULT_PROFILE
blocker stage = PRODUCT_BASELINE
blocker code = BASELINE_INSUFFICIENT_WINDOWS
live calibration attempts = 0
outer baseline restored = true
owned cleanup = CLEAN
```

## 历史根因边界

冻结代码存在一个条件性的 `PILOT_RUNTIME` required-but-not-queryable hypothesis：
只有当缺失的 predecessor capability matrix 把 Runtime 标成 available 且
target-complete 时才成立。它仅标记为未确认的 `TRACKED_CODE_PATH_INFERENCE`。由于
successor worktree 没有 predecessor 的原始 private window/capability bytes，不能把
它升级成测量根因，也不能声称 v0.2 重复了 v0.1 的 Prometheus coverage 问题。

## 下一步门槛

Increment 2 才可在 Goal 的受限 authority 下运行新的只读 readiness campaign：

- fresh v0.2.1 roots；
- queue flag 保持精确默认 `0`；
- bounded healthy checkout traffic；
- 5 个 audited windows，至少 4 个接受；
- audit 与 builder ordinals/parity SHA 一致；
- active baseline 经 API/worker restart 后仍可取回；
- outer baseline restored，owned cleanup `CLEAN`。

在 `ECOMSRE_PRODUCT_V021_BASELINE_READINESS_PASS` 之前，任何 fault attempt 都不合法。

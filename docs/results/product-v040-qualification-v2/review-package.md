# Independent review package — REVIEW_REQUIRED

## 本阶段结论

唯一一次新 no-fault qualification 已消耗，结果为
**BLOCKED_PRE_EXECUTION / WRITER_CONTAINER_DRIFT**，首次偏离位于
`AFTER_PROBE_START`（18/63 个阶段已接受）。不得在本授权下重跑。

清理结果为 **MANUAL_INTERVENTION_REQUIRED / CLEANUP_RESOURCE_REPLACED**。
保留 1 个运行中的 sleep probe、6 个本阶段 named volumes、0 个本阶段网络。
清理门禁未被绕过；未修改卷权限或恢复执行。资源清单和精确 ID 见
[`terminal-analysis.json`](terminal-analysis.json)。后续清理需要独立的、明确限定的处置。

同一个容器 ID 的固定指纹只有 `HostConfig.OomKillDisable: false -> null`
这一差异。这是启动前后可观察到的序列化字段差异，不能据此宣称容器实际被替换。
本阶段也没有为 PR #95 的历史网络事件添加未经证明的根因。

## Evidence and limits

- The frozen implementation was independently approved before startup. Source
  head: `a53ecbef84a3fc85b050243ca0548311ea807221`.
- Qualification: `ebbcf1e4e3e740a296f294fb9ab6047c`; exactly one common-repository
  fuse was consumed for this phase's authorization.
- Direct pinned-image provenance and fresh copy-up measurements match for all
  three paths. The original v1 mismatch remains valid and unchanged.
- Runtime process census and effective-access completion were **not reached**.
  Image Config.User and passwd are source facts, not a measured running Kafka identity.
- Sentinel **NOT_EXECUTED**; Kafka and Product **NOT_STARTED**; healthy traffic,
  Active Baseline, NO_INCIDENT and isolation checks **NOT_REACHED**.
- Immutable cleanup inventory compared with the initial inventory shows unchanged
  non-owned containers, volumes, images and network identities/configuration.
  Full network inventory is NOT equal: builtin `none` gained the owned probe
  endpoint during its authorized start. The precise endpoint delta is recorded
  in `terminal-analysis.json`. No network replacement was observed. This is a
  bounded comparison, not a promise about future external changes.
- All 11 formal-action counters remain zero. Product persistence was never
  created, and no runtime command reached a formal-action path. The no-fault fuse
  is consumed; the distinct formal campaign allowance is unconsumed.
- `runtime-results.json` is a byte-identical copy of the sealed private result.
  `runtime-evidence-index.json` publishes the sealed private relative-path digests.
  Raw host inventory stays private. `terminal-analysis.json` is a separate derived
  explanation; it does not overwrite the terminal or first-divergence evidence.

## Review navigation

1. [Frozen policy](../../../config/product-v040/copyup-access-v2/policy.json)
2. [Source adjudication](source-adjudication.json)
3. [Independent pre-runtime approval](pre-runtime-review.json)
4. [Machine-readable terminal](runtime-results.json)
5. [Remaining-resource and field-delta analysis](terminal-analysis.json)
6. [Runtime evidence index](runtime-evidence-index.json)
7. Final independent review and verification records in this directory.

**Future formal campaign: WITHHOLD.** This phase authorizes no formal Payment
fault, remediation, Provider call, recovery window or formal campaign execution.
Stop at the stacked Draft PR / REVIEW_REQUIRED; do not merge PR #95 or #97.

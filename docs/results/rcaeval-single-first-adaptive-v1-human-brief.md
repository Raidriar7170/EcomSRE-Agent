# Human Brief：Single-first Adaptive v1 最终结果

结论：`SINGLE_FIRST_ADAPTIVE_V1_VALIDATION_COMPLETE_NEGATIVE_RESULT_READY_FOR_REVIEW`

证据边界：`DEVELOPMENT_VISIBLE_DEV_VALIDATION_NOT_EXTERNAL_HOLDOUT`

## 一句话结论

最终 Fusion overlap Guardrail 已通过共享 Smoke，candidate-3 也通过了 DESIGN Minimum Gate 并在访问 validation 前完成冻结；但一次性 DEV_VALIDATION 中 Adaptive 保留了 65 个 `HTTP_429`，固定分母结果显著低于 Strong Single，因此当前证据不支持“Adaptive 改进”结论。这个负向结果受到 Provider 失败严重污染，不能等价解释为 120 次成功 Adaptive 推理的真实准确率。

## 本轮修复了什么

- Provider-facing Fusion proposal 可以观察 supporting / contradicting evidence overlap；内部 `FusionDecision` 的唯一、互斥 invariant 没有放宽。
- 校验顺序保持 fail closed：JSON/schema → 稳定归一化 → visible ref 授权 → service/action 授权 → 仅 overlap 的确定性 fallback。
- 合法但 overlap 的 proposal 会 `KEEP_INITIAL`，保留 Initial service、confidence 与 evidence authority，清空 contradiction，并追加 `OVERLAPPING_EVIDENCE_REJECTED_KEEP_INITIAL`。
- unknown ref 与 unsupported service 不会被 fallback 掩盖；不会为了修复输出再次调用 Provider。
- 私有 trace 只记录 safe flag/reason/count；公开结果只给 aggregate count，不公开具体 overlap ref。

## 共享 Smoke

新 domain `single-first-adaptive-v1-fusion-guardrail-r1` 的 12/12 case 全部完成：34 次 Provider attempt，0 transport retry，0 semantic retry，0 privacy hit，0 Fusion terminal failure，0 Guardrail 触发；Smoke gate 通过。12 条终态作为 candidate-1 DESIGN 子集复用，没有第二次 Smoke。

## 三轮 DESIGN

历史 Strong Single baseline 保持 Root 51/60、Pair 29/60，没有重跑。

- candidate-1：57/60 completed，Root 55，Pair 33，Damage 3，Rescue 7，Direct 1，Mean ops 3.03；有 3 个失败终态，Minimum Gate 未通过。
- candidate-2：59/60 completed，Root 56，Pair 32，Damage 3，Rescue 6，Direct 32，Mean ops 1.97；唯一失败为 `SPECIALIST_OVERLAPPING_EVIDENCE_REF`，所以 disqualifying failure count 非零。
- candidate-3：60/60 completed，Root 57，Pair 33，Damage 2，Rescue 6，Direct 35，Mean ops 1.85，0 retry，0 failure；是唯一过门候选。

candidate-3 的最后改动仅是已授权的 Logs/Trace evidence-role wording 澄清；没有修改模型、F0、transport、split、baseline、Minimum Gate 或 Fusion overlap safety behavior。实现 commit 为 `28d219b868aa4cf5a058dd87fe9449cd0cc81074`，freeze commit 为 `f8d046449b71a683a29a8940fb83bd3d32ef919c`。现有 freeze loader 在 validation schedule 首次打开前通过；freeze 后 Agent/runtime 未修改。

## 一次性 DEV_VALIDATION

240 个计划运行全部终态化：

- Strong Single reference：120/120 completed；Root 99/120（82.5%），Pair 55/120（45.8%）。
- Adaptive：55/120 completed，65/120 `PROVIDER_FAILURE`；65 个安全错误码全部为 `HTTP_429 / ALLOWLISTED_TRANSPORT_TRANSIENT / PROVIDER_CALL`。
- Adaptive Root 51/120（42.5%），Pair 31/120（25.8%）。
- Root 配对差 -40.0pp，95% CI [-58.3, -21.7]；Pair 差 -20.0pp，95% CI [-35.8, -5.8]。
- Damage 31，Rescue 7，Net Rescue -24；Direct 30；Mean ops 3.05。
- Provider attempts 237；65 次允许的 transport retry；0 schema retry、0 semantic/result-driven retry。
- Fusion Guardrail 0；correct/wrong override 均为 0。

这些指标保留了全部失败 case 和固定 120 分母。Route / escalation aggregate 也包含失败路径默认值，不应被当作 120 次成功 Adaptive 的干净行为估计。

## 汇总缺陷与处理

240 个 terminal 写完后，validation entrypoint 在纯汇总阶段把 slots-based `BootstrapInterval` 当成有 `__dict__` 的对象，导致 aggregate 尚未写入即报错。为保持 freeze 后 runtime 不变，本 Goal 没有修补入口、没有重新运行 validation、没有再次调用 Provider。

随后执行的 terminal-only deterministic finalizer 只读取既有 240 个 terminal，复用相同 scoring、10,000 次 hierarchical paired bootstrap、positive gate 与 public privacy check，create-once 写入 aggregate 和 private outcomes。它新增 0 个 Provider call、0 个 run ID、0 个 tracked runtime change。该缺陷仍公开保留给人工评审，没有把它平滑成正常成功。

## 数据与外部声明边界

- Validation 在 candidate freeze 前未打开。
- Reserved validation 为 RE2-OB 60 + RE2-SS 60，与 DESIGN identity overlap 为 0。
- 未访问 RE2-TT、生产数据或其他 external holdout。
- 旧 Initial / downstream failure 证据以及三轮 DESIGN/validation 证据均保留；历史哈希复核未变；无旧 ID 复用。
- 当前是 development-visible reserved validation，不是 fresh external holdout，也不支持公开“准确率提升”声明。

## 人工评审建议

不要重跑本次 validation，也不要基于 validation 结果修改冻结候选。人工评审应同时检查：1）65 个 HTTP 429 对结果有效性的污染；2）负向 fixed-denominator 结果；3）已披露的 reporting-only finalization defect。若决定继续，下一阶段应在单独授权、明确 Provider capacity 的条件下准备真正新鲜的 external holdout；本 PR 不自动 merge。

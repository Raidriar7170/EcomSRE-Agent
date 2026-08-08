# Human Brief：Single-first Adaptive v1 初诊接口修复

结论：`BLOCKED`

精确原因：`SHARED_SMOKE_DOWNSTREAM_SCHEMA_FAILURE_OUTSIDE_R2_INITIAL_INTERFACE_SCOPE`

本轮已在 PR #18 的同一分支和同一 `single-first-adaptive-v1` 版本中完成最小初诊接口修复。新的 `InitialDiagnosisInput` 只发送一套 external evidence；不再向 Provider 发送 `canonical_evidence`；visible services 与 visible evidence refs 均从实际发送的同一输入对象生成；初诊 Prompt 要求逐字使用这些可见值；本地校验使用不含原始非法值的 `INITIAL_*` 安全错误码。

旧的 36 条终态证据保持不变：三个旧 candidate 标签各 12/12 `INVALID_SCHEMA`，共 36 次 Provider attempt，均发生在修复前的 `INITIAL_DIAGNOSIS / OUTPUT_VALIDATION`。它们被明确标为 `PRE_FIX_INITIAL_INTERFACE_FAILURE`，不计入 DESIGN 迭代，也未复用旧 run ID。

新 r1 共享 Smoke 的边界结果是：12/12 terminalized，且 12/12 均成功通过 Initial Diagnosis，所有 `INITIAL_*` 失败码为 0。这证明原初诊输入/校验接口问题已被跨过。但端到端只有 7/12 completed；其余 5 条分别在 Logs Specialist 输出校验失败 4 条、Fusion 输出校验失败 1 条，安全码均为 `PROVIDER_OUTPUT_INVALID_SCHEMA`。总计 30 次 Provider attempt，0 次 transport retry，0 次 semantic retry，0 个 private-path hit；Gate 未通过。

当前授权只允许在出现精确、可修复的 shared Initial Diagnosis interface code 时执行唯一一次 r2。本轮没有出现该类失败，因此没有启动 r2，也没有修改 Gate。60-case DESIGN 未开始，候选未选择、未冻结；DEV_VALIDATION schedule values 与 case directories 均未打开；未访问 RE2-TT，未进行 result-driven retry 或 validation tuning。

人工评审重点：确认是否另行授权一个边界清晰的后续任务，专门修复新暴露的 Logs Specialist 与 Fusion Provider-output schema 问题。当前结果不是 DESIGN 准确率结论，不能据此声称算法优劣，也不应在共享 Smoke 通过前打开 DEV_VALIDATION。

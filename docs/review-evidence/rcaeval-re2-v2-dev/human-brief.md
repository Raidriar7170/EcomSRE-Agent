# Human Brief：RCAEval RE2 v2 开发门失败

结论：`V2_PROVIDER_DEV_GATE_NOT_PASSED`。

指标工具门已通过，F0 在 60 个 DESIGN case 上达到 57/60，Memory 10/10，Socket 9/10。真实 Provider smoke 使用冻结 schedule 和原始 run ID 开始执行，但在第 9、10 个终态附近发现 bounded Logs evidence 可能包含本机绝对路径。序列化安全边界正确拒绝了该内容；问题在于拒绝发生在 operation marker 之前，使两条失败无法归因到 exact operation stage。

按 no-retry 规则，两条 orphan attempt 只做终态化，没有再次请求 Provider。当前保留 10 条 terminal：v1 reference 5 条全部完成；v2 5 条中 2 完成、1 条 Judge schema failure、2 条 protocol violation。共 29 次 Provider operation，语义重试 0，transport retry 0。私有 artifacts 扫描未发现密钥、Provider 原始响应或本机绝对路径持久化。

因此没有继续剩余 smoke、完整 DESIGN 或 DEV_VALIDATION。建议人工审查后单独提出 `v2-dev.1`：在进入 typed snapshot 前做确定性、不可逆、保留语义的路径脱敏，并保证所有预处理失败先进入明确 operation stage；必须使用新 schedule、新 run IDs，且保留本次负向 evidence，不得混合结果。

另一个流程缺陷是 Provider smoke 启动时协议要求的 `evaluation-lock.json` 尚未存在。终止后已用 create-once 方式重建根锁，绑定未变化的六个前置锁、F0 与三份 schedule；该锁明确标记为仅用于负向证据，不具有追溯授权效力。

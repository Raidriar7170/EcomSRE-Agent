# v4 限定工程预检收口

终态为 **engineering_preflight_exhausted_checkpoint**：5 次预算全部消耗，完整 PASS 为 0。保留 **Draft / REVIEW_REQUIRED**，后续正式 campaign 为 **WITHHOLD**。

最远一次完成 Probe 资格验证与删除、28 个 Sandbox 容器创建及 birth 身份验证，并启动 astronomy-db、观察到 healthy；随后因 OomKillDisable 从 false 变为 null 而失败。完整服务健康、流量、Product、baseline 和 NO_INCIDENT 均未到达。

5 次原始 `FAILED / BLOCKED_SAFETY` 及原证据摘要全部保留。第 2–5 次后续限定清理为 CLEAN；第 1 次自身资源已删除，但 builtin bridge 身份在观察到的 Docker VM 唤醒期间改变，因此保持 `OWNED_REMOVED_NONOWNED_DRIFT`，不改写为 CLEAN。最终全部尝试的 owned 资源为 0。

第 5 次清理采用经过独立审查的一次非 root、只读 cgroup/OOM census，完整绑定原始创建、启动、进程生命周期和网络端点，再对唯一目标执行 stop/remove 及依赖资源清理。此修复只授权清理，不扩展普通运行准入。

一条第 1 次之后的只读 `system df` 诊断超出了合同第 18 节的 version/info-only 限制。原证据保留；该命令未创建运行资源，已从执行路径移除。VM 唤醒导致 bridge 重建是有证据支持的推断，不是强行归一化后的事实。

未启动 Product、Provider 调用、fault、remediation 或正式 campaign。Product 数据库计数未被观察，明确标为 NOT_REACHED / null；不宣称数据库实测零。测试通过只说明离线实现检查通过，不能替代未完成的实时验收。

发布内容包括五次失败、RCA、原始和修复后清理状态、独立审查、测试/CI 绑定及 SHA-256 证据承诺。本终态不授权合并、追加尝试或开启正式 campaign。

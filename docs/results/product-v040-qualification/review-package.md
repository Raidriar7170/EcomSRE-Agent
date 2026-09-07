# 独立审查包：Product v0.4 owned-local no-fault qualification

**结论：BLOCKED_PRE_EXECUTION / REVIEW_REQUIRED。未来 formal campaign：WITHHOLD。**

PR #96 已通过 exact-head 全量验证及独立审查，仅合入
`codex/product-v040-live-payment`；PR #95 仍为 Draft，没有合入 main。
该整合记录见 `pr96-integration.json`，既有 PR #95/#96 证据没有改写。

本阶段最新资格验证身份为 `c3356f09da26489fa0a984cb8f3d3a8f`，代码
`1dbe8d6d2cb41bed0df1a1f01cabcb92a1b41bb9`，tree
`f8229cda2f26b071158e81375930327841e4159a`。57 个阶段预先绑定，接受了
前 16 个 inventory 检查点，在 `AFTER_COPYUP_MEASUREMENT` 首次阻断。
另有三个资源创建前的预检阻断，各自使用独立身份与目录，完整保留在
`runtime-results.json`，没有把失败覆盖成成功。首次 launcher 缺少
`PYTHONPATH=src` 的导入失败未建立资格验证身份，也未调用 Docker。

| Kafka image volume | 实测 UID:GID | 模式 | 内容 | 判定 |
| --- | --- | --- | --- | --- |
| `/etc/kafka/secrets` | 1000:0 | 0775 | 仅空根目录，0 字节文件内容 | 与预绑定 1000:1000 不符 |
| `/mnt/shared/config` | 1000:1000 | 0755 | 仅空根目录，0 字节文件内容 | 身份匹配 |
| `/var/lib/kafka/data` | 1000:0 | 0775 | 仅空根目录，0 字节文件内容 | 与预绑定 1000:1000 不符 |

image `/etc/passwd` 中 appuser 的数值身份为 **1000:1000**。这次严格的
所有权策略因 GID 不匹配而拒绝继续；这并不证明 UID 1000 无法写入。
没有运行 sentinel，因此 writeability 是 **NOT_EXECUTED**。没有修改卷的
UID/GID、权限或内容来继续运行，也没有调整策略后再次尝试。

仅创建了六个新的已标记卷和一个停止态 Kafka copy-up probe。没有启动
Kafka 服务、Sandbox 或 Product API/worker。cleanup 使用逐次重新验证的
完整绑定指纹，终态 owned 容器/网络/卷均为 **0**，非 owned 清单摘要前后
相同。first-divergence 保持 LATCHED。后续健康控制、Active Baseline、
NO_INCIDENT、运行时网络隔离及 executor-denial 均 **未测量**，不能引用历史
prep004 的 30/30 或离线测试作为本次运行时成功。

正式 fault、remediation write、Provider call、formal campaign execution
均为 **0**；formal manifest/candidate/approval/AttemptAuthorization/
WriteIntent/executor/StepReceipt/recovery window 均未创建或执行，
一次性正式 campaign 额度未消耗。Product 数据库未创建。

公开 JSON 提供四次保留结果、实际 copy-up 元数据、所有镜像 Config.Volumes、
完整阶段计划与角色、挂载/标签/生命周期、daemon binding 摘要、清理对比、
原始证据文件 SHA-256 索引。私有完整源码路径、环境及原始 inspect 数据保留在
各自 append-only qualification root；公开 JSON 不导出管理员凭据。

最早的历史 preparation-004 ownership divergence 仍为 **UNKNOWN**。稍后
Docker Desktop bridge recreation 的时间关联证据没有被改写为该早期故障的
已证实原因。

停止边界：独立终审完成后仅打开本 successor **Draft / REVIEW_REQUIRED** PR。
未来任何 formal freeze、Payment fault 或一次 bounded remediation attempt
仍需新的明确用户授权。本 PR 不合并 PR #95、不启动正式 campaign。

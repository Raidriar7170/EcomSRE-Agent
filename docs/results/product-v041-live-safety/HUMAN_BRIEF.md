# v0.4.1 Human Brief

五个真实本地 Payment 安全案例通过。S0 健康无 Candidate；S1 撤销批准与 S2 状态漂移均拒绝授权，外部 Product 写入为 0。S3 三类 API 重放和第二次真实 executor 调用没有第二次恢复；S4 一次 Baseline restore 已 APPLIED，但业务窗口无足够请求，得到 VERIFICATION_FAILED / ESCALATE_HUMAN，不再写入。

每个案例使用独立资源、SQLite/CAS 与键空间，每例 cleanup CLEAN、owned 剩余 0/0/0，非 owned 资源未变。Provider / LLM calls = 0。沿用原 Product 核心语义、固定镜像与唯一 Runbook，没有放宽 Gate。

README、Product 文档、面试讲稿、离线 HTML 与四层图已区分 Diagnosis 无权、独立批准和单次状态绑定恢复。v0.2.4、v0.3、PR #102 和本次数字各自绑定来源。个人职责仍需本人确认，项目结果不证明全部独立贡献。

时延每项 n=1。Job CLAIMED → SUCCEEDED 是诊断任务观测耗时；Candidate 使用 API 响应观测，包含传输。故障到恢复包含预设故障确认和 harness 调度。精确 gateway 消费时间以及跨进程 monotonic 未测量，保留 NOT_MEASURED，不作 mean/P95/SLO 声明。

不证明生产自主自愈、跨环境泛化、通用 exactly-once、完整 28 服务 Harness 成功或全部攻击面覆盖。peer witness 仅支持当前可信本地 harness 归属，不能区分所有共享 NAT 的恶意主体。

[矩阵与时间](README.md) · [Claim map](../../analysis/product-v041-claim-map.json) · [独立审查](REVIEW.md)

合并前需独立审查 PASS、完整验证、exact-head CI 与 fresh SHA-256 closure。合并后再评论并关闭 #95、#97–#101，保留分支和历史终态，最终 commit/tree 与关闭状态记录在 Closeout PR completion comment。

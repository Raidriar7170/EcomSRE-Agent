# Product v0.4 PR-D：执行器与恢复验证审核说明

PR-A #91、PR-B #92、PR-C #93 已依次通过独立审核、精确提交 CI 和 squash merge。
本阶段基线为 `ded81cae5419d9bc950221c24f03cdee7bfa66df`，对应 PR-C CI
`33976288063`：6458 passed / 21 skipped，Ruff PASS，mypy 683 source files PASS。
合并树与 CI 提交树均为 `f05ab07a4a64993f6de0089876fb3ace6ef3d41f`。

本阶段只实现并验证离线行为。正式 v0.4 故障、真实前向写入、Provider 调用均为 0。
权威范围来自 [已激活 Goal 的原文封装](../../config/product-v040/goal-contract.source.json)，
SHA-256 保持 `d8ec6455a6108f40d67eb8441f18e952670b087255c0fb15fc14ccb87e32695a`。

## 实现与安全边界

- 执行器是独立进程，默认关闭。它只能领取 AUTHORIZED 的请求，在本次调用内提交唯一
  WriteIntent，再持久化唯一 dispatch 后调用固定 Payment 适配器。
- WRITE_INTENT_COMMITTED 或 EXECUTING 的遗留记录只能只读核对。缺少回执时保持
  OUTCOME_UNKNOWN / ESCALATE_HUMAN，配置已回到基线也不补造回执。
- `forward_write_count = null` 明确表示外部结果未知；不能把未知写入报告为 0。
  可信适配器明确证明发送前拒绝时可保存 FAILED 回执，计数为 0。
- 写回执必须解析实际 StateObservation，核对完整 dispatch → intent → authorization →
  snapshot → candidate / approval / baseline 链及 CAS 引用，并再次检查执行租约。
  恢复验证重新核对已保存的链条；不会仅信任曾经通过保存检查的字节。
- 一个有效 APPLIED 回执后，只保留两个 create-once 窗口。窗口包含服务健康、端点、独立
  checkout/payment 流量请求与错误计数、配置、flag evaluation 和非自有资源检查。
  verifier 用事先冻结的 healthy bound 重新计算窗口布尔结果，要求新鲜、时间不重叠且
  所有引用可解析。任何缺失或失败均不能到达 RECOVERED。
- 健康策略必须早于授权创建，其完整摘要在发送前固定到 dispatch。gateway、回执读取、
  恢复开始与最终评估事务均再次核对该摘要及 baseline / ownership / target / control
  绑定。写入后重新计算摘要并替换阈值或 baseline，也不能改变既定验收标准。
- 恢复采集有单一租约和逐窗口持久化预约。重启后不重新获取已预约窗口；租约过期后只
  用已有证据完成核对。采集或验证失败不触发第二次写入。

## 控制凭据与网络

上游 flag UI 不校验写凭据，因此新增固定控制 gateway。读、写使用不同 Unix socket
和不同进程环境凭据；写 socket 仅挂载给执行器和 gateway。写请求不含命令、URL、flag
键值或任意目标。gateway 独立验证持久化授权链和租约，并使用独立 SQLite 去重账本在
唯一 HTTP 发送前记录意图。它只提交预绑定的基线文档，禁止重定向、代理和 HTTP 重试。

必须结合 [remediation 网络 overlay](../../config/product-v040/remediation-network.v1.yml)
启用 profile。API/Worker 仅连接内部观察网络，通过固定只读入口获取
Prometheus/Jaeger 数据，并转发严格重建、限制服务数、结果数与时间范围的 OpenSearch
`POST /otel-logs-*/_search`。该 POST 是[上游 Search API](https://docs.opensearch.org/latest/api-reference/search-apis/search/)
的查询操作；任意 DSL、脚本、索引和写路径均不转发。执行器禁用网络，gateway 持有精确上游控制地址。普通 Product
默认启动不启用该 profile。进程要求隔离 profile 标记；PR-E 仍必须实测 API/Worker 到
宿主控制接口的连接被拒绝，标记和静态配置不能替代实际网络证据。

gateway 不挂载 Docker socket。独立观察进程以签名证据提供新鲜自有资源身份与恢复窗口；
此进程的真实 Docker 所有权检查、业务探测、冻结健康阈值与运行资源绑定属于 PR-E。
观察密钥和读写凭据只通过环境传递，原始异常不进入接口响应、指标或公共证据。
这里不声称能防御拥有数据库、凭据或宿主机修改权限的恶意管理员。

配置目录使用只读目录挂载，以跟随上游 write-rename 的新 inode。控制读回比较上游
`/read` 的 flags 投影，并单独核对整个私有配置文件摘要；OFREP 值与 variant 必须一致。
镜像预创建 UID10001 所有的 socket、配置与 ledger 目录，避免新命名卷初始权限阻塞。

## 宽表面修改理由

`app.py` 仅连接可选的只读状态客户端、增加派生指标。普通 Worker 不获得写凭据。
`docker-compose.product.yml` 与 `Dockerfile.product` 承载进程隔离和非 root 运行目录。
CI workflow 增加离线 v0.4 verifier，确保每个精确提交都执行历史绑定与 fixture 演示。
基础 Product schema 仍为 9；remediation extension 以追加迁移 3 保存新记录，迁移 1/2
的 DDL 与摘要不变。历史测试只调整默认服务区段与最新扩展版本的断言。

## 离线验证与未测事项

独立演示：`PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.product.demo_remediation_v040`。
它加载固定合成 fixture，使用真实 Product SQLite/CAS、候选、审批和授权，并执行一次
fake mutation 与两个 fake recovery windows。输出明确标记 OFFLINE_SYNTHETIC_FIXTURE；
演示代码不导入测试，不使用网络、Docker 或真实控制适配器。

初始 Reviewer 发现的租约、回执类型/链条、时钟推进、卷权限、上游返回形状和原子文件替换
问题已修复并增加回归。重入采集测试初次因错误消息正则大小写不匹配而失败，已改为检查
稳定 ProductError.code；未降低恢复门槛。

最终本地完整 Product + v0.4：268 passed / 15 warnings / 18.55 秒，Ruff PASS，mypy 239
source files PASS。独立 Reviewer 重新运行 35 项执行器/gateway 测试和离线 verifier 后
给出 PASS / Must Fix 0 / Claim Accuracy PASS。真实临时 Unix socket 测试使用 fake 上游，
验证凭据、唯一发送和关闭过程，仍不构成 Docker 或 Payment 实测证据。
提交内容核验、精确提交 GitHub CI 和合并仍是本阶段剩余门槛。
Docker 实际启动、网络拒绝、签名观察进程的真实采集和 Payment 写入均未执行，不能从
本阶段 fixture PASS 推断真实恢复成功。

# 快速体验 · EcomSRE-Agent

只选三个入口之一。默认不启动 Docker、不调用 Provider、不需要私有文件，
也不复跑完整 live fault 实验。

## A. 两分钟证据导览

只需浏览仓库：

1. 打开[当前状态](STATUS.md)，确认能力与只读边界。
2. 打开[健康验收 JSON](../results/product-v024-nofault-acceptance-final.json)，
   对照 `traffic.succeeded = 30`、`diagnosis.terminal = NO_INCIDENT`、
   空的 `capability_limitations` 与 `scorer.terminal`。
3. 打开[故障族与规则摘要](../analysis/product-v030-family-and-rule-summary.json)，
   看 `review_ready_snapshot`、`runtime_selected_clause`、
   `shadow_evaluation`、`promotion` 与 `h1`。
4. 对照[完整实验 JSON](../results/product-v030-live-knowledge-evolution.json)
   的 `live_005.cases`，用
   [PR #88 最终记录](https://github.com/Raidriar7170/EcomSRE-Agent/pull/88#issuecomment-5529165572)
   确认最终 CI 与合并状态。

历史失败及集成前状态均被保留，不代表当前阻塞。
阅读不需要私有 DB、镜像锁或遥测目录。

## B. Docker-free 确定性演示

前提：已经取得仓库，终端位于仓库根目录，具有 Python 3.11 和 uv。
下面两条命令已在本次文档收口的干净 worktree 实际执行：

```bash
uv sync --frozen --python 3.11
PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.product.run_product_mvp_demo
```

第一条按锁文件创建仓库局部虚拟环境，不做全局 Python 安装。
第二条运行当前 Product 代码，成功时最后一行是：

```text
ECOMSRE_PRODUCT_MVP_V01_KNOWLEDGE_LOOP_PASS
```

名称保留历史兼容性，不表示代码停留在旧版本。

### 演示覆盖什么

- 进程内 FastAPI TestClient 创建、验证 fixture 环境并激活 Baseline。
- 一个 Core Known 控制、三个 Open-World 正例和两个 No-Incident 控制。
- 一个 REVIEW_READY 故障族，Runtime 规则挖掘、Shadow、扩展复发。
- 明确标记 `SIMULATED HUMAN REVIEW` 的接受/晋升记录；
  LLM advisory 也是固定模拟文字，没有外部模型调用。
- 应用重建后，PROMOTED 故障族和一个 ACTIVE 扩展仍可读取。

演示使用合成 payment / mutex-convoy 场景，**不是 Kafka live 结果重现**。
默认写新建临时目录，结束后自动移除；不启动 HTTP 服务，
不操作已有 Product DB、Docker 资源或故障开关。
依赖可能发出兼容性 warning，成功以退出码和末行标记为准。

源码：[run_product_mvp_demo.py](../../scripts/product/run_product_mvp_demo.py)。
继续阅读：[架构](ARCHITECTURE.md)与[知识演化](KNOWLEDGE_EVOLUTION.md)。

## C. 进阶本地 OTel 集成

以下是接入概览，不是本轮执行过的 live 命令。

需要隔离的本地 OTel Demo、经过验证的来源配置、稳定服务身份、
足够历史窗口与 Active Baseline。
先读[连接器](CONNECTORS.md)、[Baseline](BASELINES.md)、
[API](API.md)及[运维](OPERATIONS.md)，再评估权限和资源所有权。

理解顺序：

1. Product API / Worker / SQLite / CAS 的部署边界。
2. Prometheus、OpenSearch、Jaeger 的字段与服务映射验证。
3. 每条读取覆盖度，以及 Runtime / Changes 的具体来源与授权。
4. 多窗口 Baseline 构建、显式激活、事件绑定。
5. 只读诊断与证据检查；知识晋升仍经过原有门控。

[环境基础示例](../../examples/product/environment.otel-demo.json)
不是当前 v0.3 实测完整配置，也不保证其他机器有相同端口、字段或凭证；
不可直接指向生产环境。

完整 v0.3 live 重现还依赖未公开镜像锁、授权运行状态与保留证据，
不是公开仓库的一键复现承诺，也不是多小时实验的默认启动入口。
当前可直接核对的是已提交结果，可直接运行的是上述确定性演示。
启动环境、故障注入与清理均需独立受控工作流。

[HTML 手册](../interview/ecomsre-agent-v03-handbook.html)可下载后直接在浏览器打开；
在仓库内打开时，附带证据链接也可解析。

# 快速体验 · Product v0.4.1

四个入口均可从已提交的仓库开始。Demo 使用夹具；verifier 校验保留证据，不重新启动真实实验。

B–D 的共同前提：Python 3.11 与 uv，终端位于仓库根目录，并先运行 `uv sync --frozen --python 3.11`。该命令仅创建仓库局部虚拟环境。A 只需阅读文件。

## A. 两分钟结果导览

依次打开 [STATUS](STATUS.md)、[v0.2.4 健康](../results/product-v024-nofault-acceptance-final.json)、[v0.3 知识演化](../analysis/product-v030-family-and-rule-summary.json)、[v0.4 Payment](../results/product-v040-minimal-payment/live-result.json)、[v0.4.1 安全矩阵](../results/product-v041-live-safety/README.md)。不同环境和版本的结果不能合并成一个通用成功率。

## B. Docker-free 知识演化 Demo

仓库根目录，Python 3.11 与 uv：

```bash
uv sync --frozen --python 3.11
PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.product.run_product_mvp_demo
```

预期 `ECOMSRE_PRODUCT_MVP_V01_KNOWLEDGE_LOOP_PASS`。合成场景覆盖 API、Worker、Baseline、故障族、模拟人工门控、Shadow、扩展复发与重启持久化。不是 Kafka live 复跑，无 Provider/Docker 调用。

## C. Docker-free 受限恢复 Fixture Demo

```bash
PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.product.demo_remediation_v040
```

运行当前 Candidate → Approval → Authorization → Executor → Receipt → Recovery 状态机的固定夹具。它证明离线路径，不证明真实 Payment 已运行。源码：[demo_remediation_v040.py](../../scripts/product/demo_remediation_v040.py)。

## D. 已合并 Live Evidence Verifier

```bash
PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.ci.verify_product_v040_minimal_payment
PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.ci.verify_product_v041_closeout
```

第一条校验 PR #102 的保留对象、哈希与恢复结果；第二条检查本次安全矩阵、时间与文档证据。不启动 Docker，不复跑 live fault，也不能替代原始环境采集。

真实环境接入另见 [CONNECTORS](CONNECTORS.md)、[BASELINES](BASELINES.md) 和 [OPERATIONS](OPERATIONS.md)。默认 Product 不启用恢复 profile；Minimal live 结果不是生产部署教程。私有锁、授权和原始证据不随公开仓库提供。

[离线 HTML 手册](../interview/ecomsre-agent-v041-handbook.html)下载后可直接以 file:// 打开，无外部运行依赖；[旧 v03 手册](../interview/ecomsre-agent-v03-handbook.html)继续保留。

## v0.5 默认关闭的调查与离线检查

在仓库已有环境中执行（不发 Provider 请求、不启动 Docker）：

```bash
PYTHONPATH=src:. python -m scripts.product_v050.preflight --data-root .local/product-v050
PYTHONPATH=src:. python -m scripts.product_v050.run_offline_checks --output .local/product-v050/offline-checks.json
```

第一个命令只检查配置存在性和既有只读账本，不输出 key；第二个运行
`tests/product_v050` 并保留安全的逐测试结果与源码绑定。
`FIXTURE_ONLY` 不等于真实模型或 live 验收。

真实请求前，需要为**本项目**配置 `ECOMSRE_LLM_BASE_URL`、
`ECOMSRE_LLM_API_KEY`、`ECOMSRE_LLM_MODEL` 和
`ECOMSRE_PRODUCT_PROVIDER_PRICE_FILE`，不可从其他应用提取凭据。价格文件符合
`PriceSchedule`：`provider_profile`、`model`、`as_of`、`source`、
`input_usd_per_million`、`output_usd_per_million`；价格须覆盖网关附加费用。
返回 snapshot 仅允许 exact model 或价格表显式列出的 `accepted_response_models`。
未知价格或未知模型不能继续付费调用。保持同一活动 campaign 数据根目录，
不通过重建数据库绕过 200 请求 / USD 20 总预算。

分别开启 `ECOMSRE_PRODUCT_INVESTIGATION_ENABLED=true` 与
`ECOMSRE_PRODUCT_KNOWLEDGE_PROPOSER_ENABLED=true` 后，在既有 Product server/Worker 中：

- `POST /v1/incidents/{id}/investigation-jobs`：父诊断完成后创建幂等调查任务。
- `GET /v1/incidents/{id}/investigation`：读取独立的调查记录，父诊断不变。
- `POST /v1/environments/{id}/knowledge-proposal-jobs`：请求体是已完成 discovery
  事件 ID 数组；只创建候选，不执行晋升。
- `POST /v1/knowledge-candidates/{id}/revocations`：认证后的新候选撤销入口。

恢复预览的库入口是 `ecomsre.product.remediation.planner.propose_preview`，
只返回无执行权限的 `PlanPreview`。本节不提供启动新 live campaign 或恢复写入命令；
当前未配置 Provider，真实路径未验证，活动预算和冻结案例要求仍有效。

### v0.5 continuation checks

See [continuation evidence](../results/product-v050/continuation-01/README.md). `PYTHONPATH=src:. .venv/bin/python -m scripts.product_v050.provider_smoke` loads only the explicit project dotenv and dated pricing, then performs read-only preflight. It does not send requests without `--execute`; consumed smoke attempts must not be rerun. `PYTHONPATH=src:. .venv/bin/python -m scripts.ci.verify_product_v050_continuation` verifies the current source-bound result.

### v0.5 real local investigation evidence

```sh
PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.ci.verify_product_v050_docker_stability
```

只校验已保留的 5 个独立本地事件和失败候选，不启动 Docker 或请求 Provider。预期终态为 `ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE`，不是学习成功。

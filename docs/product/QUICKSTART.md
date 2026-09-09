# 快速体验 · Product v0.4.1

四个入口均可从已提交的仓库开始。Demo 使用夹具；verifier 校验保留证据，不重新启动真实实验。

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

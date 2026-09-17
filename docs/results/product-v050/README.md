# Product v0.5 — offline delivery evidence (in progress)

The active [Goal](../../goals/EcomSRE_v0.5_Codex_Goal.md) authorizes a bounded real
investigation/knowledge campaign. The project Provider configuration and dated
prices are unavailable. No real requests or live episodes have occurred.

Engineering implements API/Worker investigation, persisted decisions and bounded
reads; shared model/miner candidate evaluation; Level B resource aggregates;
development and frozen Shadow boundaries; test-only registry governance and
revocation; and field-level remediation previews. Current proof is **FIXTURE_ONLY**.
Full repository and publication checks are still being completed.

| Evidence layer | Current result |
| --- | --- |
| Provider | NOT_CONFIGURED / NOT_ATTEMPTED; requests 0, cost USD 0 because no requests |
| Live telemetry | NOT_ATTEMPTED; independent live incidents 0; episodes 0/12 |
| Investigation | Offline API/Worker, references, windows, restart, limits and numeric prediction tests |
| Knowledge proposal | Fixture compilation/admission/rejection; no real LLM candidate |
| Level A/B | Bounded deterministic evaluator and normal matcher; unseen numeric fixture values |
| Holdout/promotion | No real frozen holdout or learned-rule promotion |
| New event reuse | Fixture plumbing only; no learned-rule recurrence claim; LLM call metric for learned reuse is null |
| Remediation | Preview-only, NONE authority, no new Product external writes |
| Cleanup | NOT_REQUIRED_NO_V050_RUNTIME_CREATED; not a claim of a completed live cleanup |

[offline-checks.json](offline-checks.json) projects executed pytest case outcomes
and binds the implementation sources; it is not experimental accuracy data.
[checks.json](checks.json) retains review and regression disposition and the
integration failures repaired. [preflight.json](preflight.json) is a read-only
configuration observation. [acceptance.json](acceptance.json) is derived by the
verifier; it cannot promote offline evidence to ACCEPTANCE_PASS.

The [case plan](../../../config/product-v050/case-plan.json) has 18 logical slots
but remains PLANNED_NOT_FROZEN: no real dataset, run, holdout label or sample count
has been invented. The [design](../../analysis/product-v050-design.md) states
supported fields/operators and the deliberately bounded first implementation.

## Verify without replaying a live campaign

```bash
PYTHONPATH=src:. python -m scripts.ci.verify_product_v050
PYTHONPATH=src:. python -m scripts.ci.verify_product_v050_history
PYTHONPATH=src:. python -m scripts.product_v050.run_offline_checks --output .local/product-v050/offline-checks.json
```

The historical adapter verifies the original v0.4.1 evidence and its original
presentation projection, plus exact authorized successor documentation hashes.
It does not rerun live experiments, alter the old manifest, or certify v0.5 runtime
performance. Historical v02323 verification runs directly with its original job
contract bytes preserved.

## 中文审查摘要

本次可审查的增量是模型提议与确定性 Runtime 之间的工程接口；证据只支持离线
协议、持久化、求值与权限隔离。独立审查发现的隐藏推理留存、模型身份/价格边界、
旧撤销兼容与历史 manifest 锚定问题均已修复。实际模型调查、Level B 学习有效性、
独立晋升和后续复发均没有成立，不能据此扩大对外表述。

解除实测缺口需要用户提供本项目已授权 Provider 配置路径和可核对的计费信息。
不需要另开账号或从其他应用取 key；预算仍为总计 200 请求 / USD 20 / 12 episodes。
恢复仍限只读预览，Draft PR 不代表 merge、release 或部署。

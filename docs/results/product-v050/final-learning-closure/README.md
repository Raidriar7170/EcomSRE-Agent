# Final learning closure — 开发通过，完整闭环未通过

最新追加：[固定规则新独立验证](fixed-validation-20260927-result.md)已因采集协议不适配 OTLP push 停止；新 live 1/4，累计14/17，未到资格/Shadow/晋升/复用。以下保留此前开发与旧 N4–N7 结果，旧计数按当时状态理解。

同一 [Draft PR #104](https://github.com/Raidriar7170/EcomSRE-Agent/pull/104)，执行版本 `b19ecbf`。沿用原 Goal、控制补充和原账本；[本次修复授权与工程记录](development-binding-repair-20260927.md)、[机器结果](development-binding-repair-result.json)、[当前验收索引](acceptance.json)。终态 **ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE**，不声称 PASS_LEVEL_A；Level B 未恢复。

| 层级 | 实际结果 |
|---|---|
| 离线枚举 | 新引用绑定下 3,682 组合重算一致，条件与证据绑定可行；非模型生成、非学习成功、未提供枚举答案 |
| 真实模型 | 原模型一次新请求（ordinal 2）提出 Runtime 健康与 queue lag 两项合取，HTTP 200；Runtime 未改条件 |
| 正式开发 | 原五例 5/5 TRUE，e04/e05 TRUE；N2、D 均 FALSE 且组件已知；旧 N3 非 TRUE，仍不算完整真负例 |
| 选择锁 | 首次通过即锁定，03:53:19 UTC；早于 N4 预留 03:54:09 UTC，剩余三次语义槽未抽取 |
| 机械 Shadow | 一次 gate=true；3 原始事件中 N4 TRUE、N5/N6 FALSE，另有 1 目标反事实、2 来源失败变体 |
| 独立控制资格 | **未成立**：N5 实际 OPEN_WORLD / UNREGISTERED_OBSERVED_ANOMALY，存在 fraud latency 与 payment error 强异常；预定 NO_INCIDENT stratum、开关恢复和成功流量不能证明真实健康 |
| 测试晋升 | 机械 gate 后确实晋升；随后复核发现健康资格问题，不能据此认定验收成立；最终已撤销 |
| N7 正常 API/Worker | 实际 CORE_KNOWN / CONFIGURATION_ERROR，learned bindings 为空，复用检查失败；调用增量 0 不等于成功复用 |
| 撤销与清理 | REVOKED；追加只读回放不命中，历史诊断不变；owned 22 容器、1 网络、5 卷已清理，剩余0，非项目未变 |

## 条件修复与原始证据

协议 `.3` 将引用明确绑定事件、父诊断、读取、窗口与对象摘要。旧 scoped metadata 中四个事件的 Runtime 摘要串用了另一事件；新视图修正12条服务投影（4目标、8比较）并保留发生身份，实际观测值和224个条件结果未变。旧 `.2` 请求、视图、摘要及失败均保留。实际发送 schema 仅依据固定来源映射约束第一、第二条件异来源，第三项可空，保留3,509个合法组合；不按开发结果裁剪。完整反馈含两次旧拒绝，没有手写赢家、排名或枚举候选清单。

真实模型草稿、wire、响应CAS和provenance已独立核验一致；正式候选为 `registration-ae3902fb32fd96d87608cb93`。N4–N6锁定后采集，没有挪作开发、改规则或重新盲测。三个派生控制实际为 raw UNKNOWN / SOURCE_COVERAGE_INCOMPLETE，符合fail-closed，但不表示确认健康；原始/派生分母分开，缺失分层保留NOT_AVAILABLE。

N5暴露了控制资格与机械标签之间的缺口，必须如实保留。N7在机械gate通过、晋升后启动；健康资格问题于N7在途复核发现，保留既成运行并完成撤销清理。N7因复用失败提前退出，原proof中的 `replay_after_revocation=true` 只是预设意图；**原链撤销回放及capability/environment mismatch检查未执行**。单独追加的只读撤销回放通过，不能冒充原链完整通过。

## 账本、限制与下一步

| 口径 | Provider请求 | 承诺费用 USD | live starts | 语义尝试 |
|---|---:|---:|---:|---:|
| 此次修复续行 | 1 | 0.051622 | 4 | 1 |
| 整个 final-closure 轮 | 3 | 0.148988 | 8/8 | 3/6 |
| 项目累计 | 60 | 1.067489 | 13/13 | 沿用各原轮记录 |

Invoice actual未知；原4次未知usage预留仍计入承诺。旧 repeated-error stop未删除，续行另行追加；所有历史失败、N1/e04缺失、N3角色和原预算保留。没有开发补采、模型切换、Product恢复写入、merge/tag/release或生产部署。

现有已见数据的实际区分来自queue lag，Runtime健康没有展示额外区分增益。本次证明模型可以在修复后的输入/选择机制下提出并通过开发，未证明新机制发现或完整学习闭环。下一步应先处理**独立控制的实际健康资格及正常事件时间窗口/既有Core诊断干扰**，不能再靠选择候补或调整该候选解决；根因仍需另外的只读诊断。本轮live额度已满，不再采集或重试。

## 验证与保留

聚焦30项通过（此前相关86项亦通过）；完整回归 **6797 passed / 21 skipped**，792.72秒；Product mypy171源文件和Ruff通过。历史v0.4.1/v0.5只读verifier通过。源码修复独立复核无Must Fix；验收复核提出健康资格问题，已通过如实报告“未成立”解决过度声明风险，数据缺口本身未解决。

可重复的只读/fixture命令：

```sh
PYTHONPATH=src uv run pytest -q tests/product_v050/test_control_repair.py tests/product_v050/test_source_bound_draft.py tests/product_v050/test_final_closure.py
PYTHONPATH=src uv run python -m scripts.ci.verify_product_v050_history
PYTHONPATH=src uv run python -m scripts.ci.verify_product_v050
```

历史：[此前README](pre-binding-repair-README.md)、[此前验收](pre-binding-repair-acceptance.json)、[旧停止记录](control-repair-execution-result.json)。私有原件留在原CAS与 `.local/product-v050/live-final-closure-07/`，离线诊断单独保存，未送入proposer。

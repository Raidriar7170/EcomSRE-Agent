# v0.5 可行性与受约束提议：开发未通过

终态仍为 `ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE`，不是完整验收 PASS。
执行 [当前 Goal](../../../goals/EcomSRE_v0.5_Knowledge_Feasibility_and_Constrained_Proposal.md)，继续 Draft PR #104。

本轮零请求审计先确认 Level A 输入可表达，再执行独立版本 `knowledge-draft-v050.2`。
同一模型 `gpt-5.4-mini-2026-03-17`、Responses Provider 和累计账本；medium reasoning、8192 output 上限同时用于费用预留。
实际工具 schema 使用当前任务/目标/成员/角色的短句柄 enum；专属高优先级任务说明与跨服务背景分离，完整保留数据缺口。
输入由旧轮约 50,200 tokens 降至本轮 15,759 / 15,781 / 16,536；不删掉资源数值或将未知补成零。

## 已见输入与依赖

| 事件 | 原角色 | 完整目标证据 | 目标补查资源 offset30/query30/sampling10/count5 |
| --- | --- | --- | --- |
| e01 | Discovery | Metrics / Runtime / 初始 Resources | 已绑定 |
| e02 | Discovery | Metrics / Runtime / 初始 Resources | 合法但未采集 |
| e03 | Discovery | Metrics / Runtime / 初始 Resources | 合法但未采集 |
| e04 | Development | Metrics / Runtime / 初始 Resources | 合法但未采集 |
| e05 | Development | Metrics / Runtime / 初始 Resources | 已绑定 |

逐字段、查询窗口、证据来源、缺口与谓词状态见 [feasibility.json](feasibility.json)。日志多数截断；e05 有完整补查日志；空 Trace 不是负证据。
原两例 Development 可按同一 Level A 谓词语义求值，但不能按同一补查资源依赖求值 Level B。
e04 初始资源窗口只有 10 秒，不能冒充 offset30/query30 的补查；保留 CAS 中没有可重建的同查询对象。
没有独立健康或 confusable Development 事件；原健康基线和 fixture 回归不充当该分母，也不提供独立特异性估计。

## 实际模型与开发结果

| 请求（累计序号） | schema | 准入 / 开发结果 |
| --- | --- | --- |
| proposal:0（55） | 有效 | `TWO_SOURCES_REQUIRED`，谓词全为 Metrics |
| proposal:1（56） | 有效 | 准入；实际开发 **1/2**，所有已见事件 **1/5** |
| proposal:2（57） | 有效 | 执行条件重复，`DUPLICATE_KNOWLEDGE_CANDIDATE` |

模型选择 queue-lag、latency、memory-growth 三个既有谓词。e04 三项 TRUE；e05 memory-growth FALSE。
Runtime 只翻译句柄，未替模型改谓词或阈值。候选仍为 DRAFT，未冻结、未独立验证、未晋升、未新事件复用。
[实际调用投影](calls.json)、[冻结协议](protocol.json)、[结果](result.json) 保留请求、CAS、schema 和源码绑定；自由模型文字及原始遥测只留本地。
机械测试和公开 JSON 一致性核验不是模型学习证据。独立只读 Reviewer 另从保留 CAS 与既有求值器重算，结果一致，Must Fix 0。

## 停止边界与累计账本

本轮 3 次请求 / **USD 0.078920** 承诺；已耗尽初稿加两次语义修订，不追加第 4 次。
累计 **57/200 次、USD 0.918501/20** 承诺，含历史未知用量预留 **USD 0.095449**；按配置上界费率计价，实际账单未知。
累计 live episode **5/12**；本轮新增 0，Product 恢复写入 0。

D 阶段只读预检通过：0 容器，3 网络，3 卷；daemon/context/endpoint 和旧清理后 inventory 一致，未接纳新基线。
D 并非普遍禁止；本轮开发未通过且语义轮已耗尽，新 episode 既不能补旧 e04 的过去窗口，也不能重新开启提议轮，因此未启动无后续用途的补采。
旧 live-01 `BLOCKED_SAFETY / clean=false`、live-02 `clean=true`、失败 session、attempt marker、角色、分母、预算预留均保留。
没有扩大 Product 工具权限，没有 merge/release。

只读重验公开证据：

```sh
PYTHONPATH=src:. .venv/bin/python -m scripts.ci.verify_product_v050_docker_stability
```

当前源码 fixture 结果见 [offline-checks.json](offline-checks.json)；旧轮测试仍绑定各自历史提交。

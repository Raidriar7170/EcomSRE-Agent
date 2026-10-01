# v0.5 控制资格修复与 N7 只读追溯

本次仅代码、fixture 和保留材料诊断。没有 Provider、Docker、live、重新选优、恢复注册或追加预算。原 SQLite SHA-256 前后一致：`aaaf8d90a3ac707ab7a179061d609179822bf77c0614b4c46aa1793a8c566e4a`。完整机器结果见 [retained-diagnostic.json](retained-diagnostic.json)；逐 action 的查询窗口、CAS 摘要、记录数量与时间范围均保留。

## 资格缺口修复

新协议 `v050-observed-control-qualification-v1` 分别记录预定 stratum、正常 Product 诊断所证明的实际资格和候选匹配。健康资格要求正常 `NO_INCIDENT`、无独立强异常、各候选服务必要 Metrics/Runtime 覆盖；Core 资格要求正常 `CORE_KNOWN` 及来源支持；目标资格要求目标服务独立强异常、来源支持和正常 `OPEN_WORLD`。资格函数不接收候选条件或匹配结果，仅使用目标标识。

证明绑定事件、正常诊断、memory、Runtime 输入、baseline、capability、来源对象与观测摘要。新 freeze 固定证明，evaluate 重算，promote 再独立检查版本、源码、候选、审计和资格。旧机械 gate 数值仍单独保存，但实际控制不合格会得到 `ORIGINAL_CONTROL_UNQUALIFIED` / `REJECTED`。缺失旧协议标记的评估在消费状态之前拒绝；直接晋升也不能绕过。

对保留事件应用新函数只是另存诊断：N4 目标资格成立，N5 健康资格不成立，N6 Core 资格成立。**N5 仍是 OPEN_WORLD，实际 fraud-detection 延迟及 payment 错误异常保留；其候选 FALSE 不证明健康。** 旧 gate、晋升、撤销、失败与各事件角色未改写。旧选择锁仍绑定旧源码；本修复不授权继续旧锁或覆盖旧评估。

## N4–N7 时间线

以下均为 2026-09-27 UTC。激活/恢复时间是配置读回时间，不意味着遥测已达到稳态。

|事件|事件起点|激活读回|诊断观察终点|恢复读回|episode 结束|
|---|---|---|---|---|---|
|N4|03:54:09.816422|03:54:12.774504|03:56:23.065987|03:56:33.958591|03:56:50.483018|
|N5|03:57:01.809824|03:57:04.744132|03:59:15.206058|03:59:24.999296|03:59:41.241014|
|N6|03:59:52.574802|03:59:56.028489|04:02:06.427299|04:02:16.903446|04:02:33.219869|
|N7|04:03:26.527250|04:03:29.874103|04:05:40.167996|04:05:48.002505|04:06:11.887112|

各窗口终点为上表诊断观察终点；精确起点如下。

|事件|Core Metrics / Logs / Traces（300s）|queue Metrics（60s）|Changes（3600s）|
|---|---|---|---|
|N4|03:51:23.065987|03:55:23.065987|02:56:23.065987|
|N5|03:54:15.206058|03:58:15.206058|02:59:15.206058|
|N6|03:57:06.427299|04:01:06.427299|03:02:06.427299|
|N7|04:00:40.167996|04:04:40.167996|03:05:40.167996|

实际查询以观察终点回溯，未截断到本事件起点。N5 的 300s 窗口包含 N4；N7 的 300s 窗口包含 N6。Logs/Traces 返回的是有上限的记录，不能将返回片段当作完整时间序列。

## N7 Core 支持的来源与归属

纯 admission 重建确认唯一 clause 为 `configuration:change-and-error-metric`，target/root 均为 `payment`（`svc-2c9d744f72d1830e20105f5c`）。

|支持谓词|原始 evidence ref|内容与归属|
|---|---|---|
|CHANGE_RECENT_ROLLOUT|`e:a:changes:payment:1:fe6e55766941`|04:02:16.911253Z 的 CONFIGURATION/COMPLETED 记录；与 **N6 restoration Changes API** 返回的 v22_record 完全相等。是此前事故恢复记录，不是 N7 激活。|
|METRIC_ERROR_RATE_STRONG|`e:a:metrics:payment:core:0:1de5bc0b6c38`|31 samples 的聚合错误率 `0.49593106413511445`，外层窗口 04:00:40.167996–04:05:40.167996，横跨 N6/N7。|

错误率 PromQL 内含 `rate(...[5m])`；外层 query_range 再汇总 sampled values 均值。因此潜在底层支持最早回溯至 **03:55:40.167996**，不能把该聚合值全归给 N7。当前证据明确证明时间窗口混合及旧恢复事实参与 Core，**无法证明错误率全是旧残留，也无法量化 N6/N7 各自贡献**。原 CAS 只保留 MetricFact 汇总与样本数，缺少原 query_range matrix、各 series labels/时间戳/值、counter 变化及实际命中 PromQL 分支。没有发现这两个支持引用串事件或串服务：按事件→action→outcome→record index/hash 复核均一致；问题不是已证实的引用作用域错误。

正常 bridge 的 Core 分支先返回，不执行 Extension matcher。冻结输入的只读 spy 回放得到 matcher 调用 **0**，且完整 diagnosis 与历史完全相等。结论是 **NOT_EXECUTED_CORE_ADMITTED**，不是匹配 FALSE，也不是 UNKNOWN。本次不修改优先级。

## 冻结学习规则的诊断回放

候选 `registration-ae3902fb32fd96d87608cb93` 保持 REVOKED，compiled SHA `d91d59dc8a1e403dd3a949c0cd56b0f49ba60d6e863c4da191c7766d9d2bbf3b`。N7 上：

- `core:RUNTIME_HEALTHY`：TRUE；支持 `e:a:runtime:all-candidates:e46103319e85:1:4b2f6bc4f832`。
- `ga:METRIC_QUEUE_LAG_OUTLIER`：TRUE；支持 `e:a:metrics:fraud-detection:queue-lag:0:4ce0eb3d4815`。
- 原合取：TRUE / CANDIDATE_MATCHED；所需 RUNTIME、METRICS 的目标服务覆盖成立，无 UNKNOWN。

纯求值及 bridge 相关源码与历史 freeze 摘要一致；只调用纯函数，不经过治理接口，不写注册。该结果说明**规则条件在保留输入上成立**，并不证明正常 API/Worker 复用；历史 N7 仍失败。现有已见数据中实际区分来自 queue lag，Runtime 健康没有展示额外区分增益。

## 下一版最小采集契约（尚未执行、未获本次 live 授权）

1. 在新测试晋升前，冻结候选之外的三类资格定义及证明；独立核验目标异常、健康无异常、Core 已知控制，绑定正常诊断、事件与完整来源输入。机械 gate 与资格必须同时通过。预定标签、配置恢复或候选 FALSE 均不能替代资格。
2. 按实际查询依赖计算时间隔离，而非固定等几分钟：Core Metrics 的 300s 外窗加 300s 内层 rate、Changes 的 3600s 回溯、Logs/Traces 窗口、采样/抓取/摄入延迟均要纳入。保留历史，等待旧支持离开新事件观察窗；不得删指标、缩短既有检测窗口、降低阈值或交换 Core/Extension 顺序。时间隔离只是必要条件，还须正常健康资格实测成立。
3. 下一次保存原始 query_range 请求/响应（matrix、series 标签、时间戳、值、step、实际 PromQL 分支所需底层数据）、抓取及摄入时间、配置操作和读回、Changes API 事实、Logs/Traces 查询响应及截断信息、事件/诊断时间与完整输入摘要。当前保留材料不足以进一步确定错误率归因。
4. 旧 N4–N7 已被观察，不重标或重新算盲测。新独立验收至少需要 **3 个新原始 holdout（目标、健康、Core）+ 1 个新事件正常复用及撤销检查，共 4 个新 live episode**，明确另行授权与原账本追加上限；当前 **13/13** 已用尽，不能执行。若独立健康预检本身需要额外事件，应事先计入另行批准的明确上限，不隐含免费补采或自动重试。
5. 保持同一冻结候选内容，无 Provider/选优需求；新协议/新盲测计划须追加关联旧候选和旧撤销记录，不改旧选择锁或恢复原 ACTIVE。只有新独立验证合格后，才可在授权测试环境走新的晋升链，再由正常 API/Worker 检验零 Provider 命中及撤销后不再命中。Level B 仍不恢复。

当前结论仍为 `ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE`：真实模型提出、原正式开发通过已发生；完整独立验收和正常新事件复用尚未成立。本次修复堵住资格漏洞，不把历史失败改成学习成功。

## 本次验证

`PYTHONPATH=src:. uv run pytest tests/product_v050 -q`：263 passed。包含异常健康控制且机械 gate=true、直接晋升拒绝、缺失协议/审计及证明变更拒绝、跨事件绑定拒绝、旧冻结记录评估拒绝且整行不变。保留数据审计通过 readonly SQLite，相关纯代码与历史 freeze 摘要一致；原 DB 新鲜 SHA-256 前后相同。此次验证范围是 v0.5 fixture 和保留证据，不是新的 live 验收。

`uv run ruff check .` 全仓通过；`uv run mypy src/ecomsre/product`：172 source files 无错误。独立只读复核已关闭旧协议先消费状态的问题，未发现剩余实质问题；复核重算的公开诊断与原库摘要一致。

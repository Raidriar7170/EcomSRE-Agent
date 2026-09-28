# 离线工程校准交付（非 v0.5 验收）

当前唯一范围：[Fresh Start Brief](../../../goals/EcomSRE_Fresh_Start_Brief.md)。旧自动 Goal 已由用户终止，旧失败、注册 REVOKED/DRAFT、停止批次和预算没有重置。本次新增 Provider、Docker、故障、live/holdout、晋升与恢复写入均为0；首次离线交付未提交；用户随后明确授权本任务相关代码提交并推送现有Draft PR #104及核对精确提交CI，仍不授权merge/release。

## 接手事实与范围

初始聊天目录 `Documents/New project` 在 `phase3/restricted-remediation-replay` / `d93d267`，有既存修改，完全保留。实际使用现有 `product-v050-llm-investigation-knowledge` worktree，初始干净，分支 `codex/product-v050-llm-investigation-knowledge`、HEAD `09ba84fb0b8d0fe27bb9fa54103dd54f3df78246`；GitHub只读核对PR #104仍OPEN/Draft且HEAD一致。只读进程检查未发现旧v0.5执行器，`lsof`未发现原SQLite/WAL/SHM打开句柄。未调用Docker，报告的旧CLEAN不被当成当前daemon资源证明。

指定Brief文件接手时不存在；本会话用户粘贴请求已给出完整执行授权，现已将其范围落盘，并最小更新AGENTS/STATUS及DEC-065。当前聊天无活动Goal对象。历史Goal文档不批量重写。STATUS属于历史校验器明确允许的后继展示路径，仅刷新其successor摘要；原历史摘要和验证器不变。

读范围：上述工作区的采集/校准、资格/freeze/promotion直接调用链、相关测试/CI、指令与结果文档，以及精确保留09准备、关联08归档和CAS。写范围：AGENTS、STATUS、DECISIONS、新Brief、本汇总、historical-successor-bindings的STATUS后继摘要；`ingestion_evidence.py`、`sampling_support.py`、`calibration_replay.py`、`validation_capture.py`、`validation_live.py`、`evolution_v050.py`、`validation_batch_v050.py`及三份相关测试；本次收口另修正`tests/analysis/test_rcaeval_re2_v1_attribution.py`的历史任务作用域测试。原库和08/09保留物冻结；本次输出在独立 `.local/engineering-calibration/`，测试仅用临时库。最终仓库范围为该worktree，未改变上游子模块指针或字节。

## 实现及可复用入口

```sh
PYTHONPATH=src:. uv run python -m scripts.product_v050.calibration_replay \
  --capture-root .local/product-v050/live-final-closure-09 \
  --objects .local/product-v050/objects \
  --output .local/engineering-calibration/publication-replay-final
```

以上命令从`/Users/raidriar/Documents/EcomSRE-Agent-worktrees/product-v050-llm-investigation-knowledge`运行。输入为本机私有09保留目录中的authorization、receipt、请求索引、collector/compose及关联CAS，CAS实物位于`objects/sha256/<前两位>/<sha256>.json`；它们不随Git推送，新机器需取得同一授权保留副本，不能凭公开报告重造真实材料。SQLite位于`.local/product-v050/product.sqlite3`，此入口不读取或打开它。实际输出为`.local/engineering-calibration/publication-replay-final/replay.json`。每次使用全新输出目录；重复路径拒绝覆盖。入口只读文件/CAS，不创建Product app、不打开数据库、不导入Live控制器，不执行HTTP。按原binding版本重算并精确比较原assessment（已覆盖v2/v3），再以同一v3纯函数输出已见开发诊断。每项保留实际PromQL/内外窗口、原聚合响应、selector/labels、首末样本、实际中位周期、最大间隔、样本数、reset、支持区间及UNKNOWN原因；记录源码/输入SHA-256、HEAD、UTC和单调耗时。输入校验失败拒绝发布；普通证据不足仍输出其他独立查询。不产出可用于正式晋升的收据。

`--context`可绑定额外保留故障/恢复/Changes事实；入口已读取09保留准备时间、基线时间、runtime快照及日志返回范围。没有相关trace细节或OTLP起始时间会明确说明不可用。上下文不会填补样本，也不会将配置已恢复变成遥测健康。

未来显式v3使用现有acquire→verify→verify_receipt链，不另建引擎或数据库。`protocol_v3`绑定配置和源码；原live批次入口继续选择原v1/v2，停止记录不变，新v3不会自动安装批次。资格、freeze、evaluate、promotion仍重读CAS并重算，不信任`passed=true`；损坏原始样本的贯通测试验证直接晋升也拒绝。正式新批次的授权/安装不在本次交付内。

## 参数与语义

| 路径 | 配置依据 | v3支持约束 |
|---|---|---|
| container CPU/memory | 实际Collector docker_stats 2s | 年龄、内部gap、左边界分别检查，周期加25%调度裕量再加1s，即3.5s |
| consumer queue | 实际kafkametrics 10s | 对应13.5s；不修改queue规则或业务窗口 |
| span calls/histogram | 实际connector flush5s | 对应7.25s；不把flush等同于事件出生或到达时间 |
| Kafka应用/JMX | 必须有有效应用周期配置证据 | 当前为UNKNOWN，不从观测到60s反推配置。fixture明确60s时对应76s，真实漏一个周期仍拒绝 |

裕量是固定工程参数，按生产者周期选择，与候选是否命中无关，尚未经过真实校准。每条至少2点，另按外层10s网格核对每个内层rate窗口（左开右闭）是否至少2点；60s生产者不能凭外窗里有很多点就满足1m rate。更快业务需求不被阈值放宽掩盖。无内窗查询也逐个验证评估时点，要求确实存在不晚于该时点的样本且未超过生产者年龄上限或300s。未来v3原始采集增加300s前缀以保留首时点前驱；只从最近前驱检查当前支持，不把前缀变成更长的业务窗口。保留v2请求不改写，没有前驱即UNKNOWN。v3显式拒绝同一query混合lookback、offset、@及未支持的时间语法，尚非任意PromQL解析器。

末点新但内部缺口独立报告；晚出现标签不补零，已观察实例的新标签与未知出生分开，counter下降报告reset但不推定一定重启，当前保守判支持不足。histogram按去除`le`后的完整标签分组，绑定配置中的完整有限桶集合（将显式duration换算为指标毫秒单位）；默认桶集合无有效版本证据则UNKNOWN，不从返回桶反推应有桶。另核对+Inf、时间网格、累计桶顺序及增量顺序；错误子集与同来源total核对标签、时间、累计值及增量。独立复核发现的“累计有序但增量反序”漏洞已修复并回归。EMPTY仍保留为EMPTY，完整备选/total只代表查询来源支持，不宣称掌握未返回标签全集，也不单独证明完整健康负例。

## 保留材料得到的结果

原v2准备的21项assessment精确复现，历史FAIL_WINDOW_COVERAGE不变。初版v3诊断曾为12项支持/9项不足；本次代码审阅修复首点评估及完整桶集合后，最终v3回放为10项具备返回序列支持、11项不足；这些不是新的采集或正式成绩。

- Kafka request/failed/Produce p95实际约60.002s、末样本年龄约25.172s。Compose与cached image环境中没有显式`OTEL_METRIC_EXPORT_INTERVAL`。冻结上游声明Java agent2.29.0，其[依赖绑定](https://github.com/open-telemetry/opentelemetry-java-instrumentation/blob/v2.29.0/dependencyManagement/build.gradle.kts)对应SDK1.63.0，[SDK固定版本默认周期](https://github.com/open-telemetry/opentelemetry-java/blob/v1.63.0/sdk/metrics/src/main/java/io/opentelemetry/sdk/metrics/export/PeriodicMetricReaderBuilder.java)为1分钟。这解释了观测的可能来源，但没有提取镜像内实际jar/运行时有效配置，故不能将默认值证明升级为有效周期证明；3项继续`PRODUCER_PERIOD_UNPROVEN`。
- fraud-detection/payment晚出现的是flagd EventStream的`STATUS_CODE_ERROR`标签；同一实例其他标签更早存在，保留counter没有reset，相应17桶组一起出现。支持起点后的首点分别约110.736s/120.736s，之后约5s连续。这支持“已有实例上的新标签首次被观察到”，不证明合法出生，也不证明服务重启或漏采。6项继续`LEFT_SUPPORT_MISSING`。
- queue lag首点晚于首评估时点1.754499s，即使随后10s连续仍为`INSTANT_EVALUATION_SAMPLE_MISSING`。checkout latency的保留connector只有flush间隔，没有显式桶配置或绑定的有效默认桶证据，因此新增`HISTOGRAM_EXPECTED_BUCKETS_UNPROVEN`；fraud/payment latency也同时存在此项不足。不放宽条件恢复12/9。
- runtime快照在20:30:21 UTC显示四服务RUNNING、restart_count=0，但不是覆盖随后全部窗口的实例生命周期证明。births保存的是created状态，StartedAt为零值，不可当启动时间。原准备日志有真实记录，但返回截止早于晚出现标签；没有与首次错误对应的trace详情。OTLP起始时间未保留。服务启动不足与合法标签出生的最终归属仍UNKNOWN。
- 既有最近恢复/Changes时间与实际支持窗一起展示；没有删除旧指标、缩短业务窗口、交换Core/Extension优先级或重算旧事件角色。

原始私有回放依次保留在`retained-01`至`retained-04`及收尾目录；本次收口源码对应`publication-replay-final/replay.json`，另一次相同源码重跑为`publication-replay-repeat/replay.json`。每次含源码/输入摘要和失败，早期输出不覆盖。原数据库未通过Product持久化接口打开。当前新路径不触及注册/Provider账本。

## 验证

前次检查点：v0.5套件310 passed；最终采样/回放/治理贯通39 passed，UTC记录修正后的入口回归另1 passed。全库主回归6849 passed、21 skipped、1 failed（848.10s），唯一失败为下述历史工作区路径限制。Ruff通过，Product mypy173文件通过，上述6个历史/后继验证器全部通过。

全库主回归在收尾前启动；之后的时间语法拒绝、诊断字段和UTC记录窄修由最终聚焦链路复核，不将此结果写成精确提交CI。该结果是旧检查点，不替代本次最终验证。当前独立只读复核已关闭累计/增量、混合lookback、首点评估和配置桶集合误判，并指出v3回放请求版本衔接；已将回放绑定原version，并增加v2/v3相同入口反例。

收尾阶段冻结的原SQLite/WAL/SHM及08/09材料共167文件摘要复核一致；回放另逐个验证引用CAS。前次全tracked-diff及新增源码、声明证据和冻结材料SHA-256结果保存于私有`.local/engineering-calibration/final-integrity.json`，本次收口另存`publication-integrity.json`，只是字节完整性证据，不覆盖上述失败，也不产生产品验收PASS。

执行命令：

```sh
PYTHONPATH=src:. uv run pytest tests/product_v050 -q
PYTHONPATH=src:. uv run pytest -q
uv run ruff check .
PYTHONPATH=src:. uv run mypy src/ecomsre/product
PYTHONPATH=src:. uv run python -m scripts.ci.verify_product_v050_history
PYTHONPATH=src:. uv run python -m scripts.ci.verify_product_v050
PYTHONPATH=src:. uv run python -m scripts.ci.verify_product_v050_continuation
PYTHONPATH=src:. uv run python -m scripts.ci.verify_product_v050_provider_unblock
PYTHONPATH=src:. uv run python -m scripts.ci.verify_product_v050_live_resume
PYTHONPATH=src:. uv run python -m scripts.ci.verify_product_v050_docker_stability
```

上列`live_resume`/`docker_stability`是保留证据验证器，不执行Live或Docker。fixture证明正常60s/漏采、新末点/中断、新标签/未知出生/reset、子集及histogram错配、可算但rate内窗不足、假passed拒绝、全治理链重验以及独立目录重复回放。fixture不能证明真实配置、出生、全标签总体或未来业务健康。独立只读复核关闭增量错配及混合lookback两点。

### 唯一历史范围失败及修复

完整节点：`tests/analysis/test_rcaeval_re2_v1_attribution.py::test_live_worktree_has_no_frozen_or_undeclared_changes`。

```sh
PYTHONPATH=src:. uv run pytest tests/analysis/test_rcaeval_re2_v1_attribution.py::test_live_worktree_has_no_frozen_or_undeclared_changes -q
```

修复前该命令在本次dirty worktree实际复现失败，完整输出保留于`.local/engineering-calibration/publication-scope-failure.log`。原测试取当前整个仓库`git status --porcelain=v1 -uall`的路径，调用`validate_allowed_paths(paths)`。实际失败断言是`unexpected = set(paths) - set(ALLOWED_PUBLIC_PATHS)`非空后抛出`ValueError("frozen or undeclared path in attribution diff: ...")`，并非历史内容摘要断言失败。实际allowlist是**11个历史RCAEval归因交付路径**，前次汇总写“五个”有误，在此更正。

被拒绝路径包括当前授权的AGENTS/STATUS/Brief、v0.5采样与回放实现、测试和本报告。它们确实不属于旧归因任务范围，但不能据此认定当前工程任务越权。生产`validate_allowed_paths`及11路径列表完全不变；历史归因输出、绑定和安全检查未修改。原测试节点改用临时真实Git仓库，验证clean、允许路径的unstaged/staged、受保护代码及新任务指针的unstaged/staged，后两者均必须抛出同一错误。没有skip、xfail、删除检查、恢复旧Goal或扩展旧allowlist。

修复后完整归因测试文件30项通过；本次最终v0.5与归因套件347 passed（119.34s），Ruff与Product mypy（173文件）通过；两次同源码真实保留数据回放的21项诊断完全相同，原库及08/09共167文件摘要不变。最终新采样/回放及历史范围聚焦测试日志见`publication-focused.log`，v0.5与归因回归见`publication-product.log`，完整回归见`publication-full-regression.log`。提交后再次在真实干净worktree运行上述完整节点，并把Git HEAD/status与输出保存在`publication-clean-commit-check.log`。PR的Agent mainline显式checkout pull_request.head.sha；最终提交CI以该SHA的GitHub Actions记录为准，运行链接与提交号在交付回复中给出，不把旧HEAD成功冒充当前结果。

## 下一次真实工程校准建议（等待一次性明确授权，本次未执行）

这是开发观察计划，不是新正式盲测。首选同一owned部署完成全部观察，不把每轮准备失败拆成新验收批次。所有原始响应、失败、修复前后参数与版本分别留存为已见开发数据，无独立成绩、Provider、事件槽位或知识晋升。

1. **有效Kafka配置与周期。** 首次采集前，绑定image digest、resolved Compose与Collector配置；只读取owned Kafka的有效环境、实际JVM命令/系统属性中允许的OTel配置键、javaagent manifest版本和JMX配置，核对环境、`-D`及程序覆盖的优先级。只保留白名单键，禁止导出整份环境或凭据。若省略export interval，需实际jar/SDK版本及其固定默认值证据，不能仅依据上游声明；不能完整证明时保持UNKNOWN。将配置证据SHA绑定到每条完整标签序列，记录至少600s原始timestamp间隔、末点年龄和漏采，而不是query_range生成的时间网格。若有效配置无法取得，可另申请显式60s配置变更，不在本计划默默改值。
2. **标签生命周期。** 在业务观察前记录服务/collector StartedAt、RestartCount、实例ID与观察起点，从同一部署持续保留三轮的完整标签、首末样本、counter/reset、配置桶全集及桶组出现时间。原始查询窗口保留所需rate内窗和instant前驱；相邻窗口重叠，首轮启动不足仍记UNKNOWN。按首次异常标签时间查同实例EventStream span/log及Changes/恢复记录，并在每轮结束记录重启计数。OTLP start time仅在现有链真实可读时保存；缺失不合成。标签首次返回不是合法出生证明，counter reset也不等于重启。
3. **流量与故障。** 先用既有受限正常流量；对配置、周期及启动标签观察无需新增故障。额外至多30个串行业务请求，每秒至多1个，用既有允许路由；不扩大到外部支付或写目标。正常流量足够判断周期和已出现序列，却未必产生晚出现的EventStream错误标签；若未出现就保留生命周期UNKNOWN。本最小方案故障次数为0、恢复写入为0。确需受限故障时，应在本轮证据后具体说明无法正常观察的信号、已有注入入口、精确恢复操作及恢复后完整支持窗，再另定一次范围；本方案不预授权故障。
4. **资源与硬上限。** 仅沿用已接受sandbox manifest、exact project labels及loopback端点中的容器/网络/卷，执行前重新核对ownership和非owned基线；不增加服务、镜像、卷、端口或宿主权限。部署最多1次，不build/pull替代镜像、不自动拆除重建；最长占用90分钟（含归属核对、准备、观察和结束时已有规范的精确owned清理）。最多3轮采集，初轮先积累所需600s支持，后轮使用同一实例和重叠窗口。总HTTP读取最多500次：启动/基线最多200；每轮正常/raw最多80，配置/Logs/Traces/Changes最多20，合计300；失败/重试也计数。另Docker只读归属/身份/配置检查最多60次；任何容器内只读检查须列入同一白名单且计数，不执行任意shell。额外业务流量30次独立计数；每请求超时10s、每轮采集阶段最多120s、串行且响应每条上限2MiB，截断保留并判不足。原始周期采集可用范围查询减少轮询，不扩大次数。准备失败不自动消耗正式批次或旧停止槽位。
5. **工程修复与停止。** 最多一次采集代码的离线修复后重试，重试占上述3轮/500请求/90分钟预算，不重置额度；只允许范围内查询/解析/诊断错误，修复需先过聚焦离线检查，冻结每轮参数与源码。普通证据不足允许同轮独立检查继续；不得因候选是否命中调参数。归属不明、非owned漂移、秘密泄漏、安全/权限冲突立即停止相关操作；重复同类基础设施错误、无法证明配置、无法取得出生证据或任何上限耗尽则结束本轮并保留诊断。不得global prune、扩大权限或更换布局。结束清理由现有exact owned协议执行；失败如实保留，不操作未知资源。成功也不触发新holdout或晋升。

以上完整范围需后续一次性明确批准；本次没有任何新增live操作。结论：工程校准路径已交付；有效Kafka配置、完整默认桶配置与标签出生/启动因果仍有具体数据阻塞。不是 `v0.5 ACCEPTANCE_PASS`，不代表已修复全部live问题。

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

## 下一次真实工程校准建议（7bbf88d时待授权；后续批准与执行见下节）

这是开发观察计划，不是新正式盲测。首选同一owned部署完成全部观察，不把每轮准备失败拆成新验收批次。所有原始响应、失败、修复前后参数与版本分别留存为已见开发数据，无独立成绩、Provider、事件槽位或知识晋升。

1. **有效Kafka配置与周期。** 首次采集前，绑定image digest、resolved Compose与Collector配置；只读取owned Kafka的有效环境、实际JVM命令/系统属性中允许的OTel配置键、javaagent manifest版本和JMX配置，核对环境、`-D`及程序覆盖的优先级。只保留白名单键，禁止导出整份环境或凭据。若省略export interval，需实际jar/SDK版本及其固定默认值证据，不能仅依据上游声明；不能完整证明时保持UNKNOWN。将配置证据SHA绑定到每条完整标签序列，记录至少600s原始timestamp间隔、末点年龄和漏采，而不是query_range生成的时间网格。若有效配置无法取得，可另申请显式60s配置变更，不在本计划默默改值。
2. **标签生命周期。** 在业务观察前记录服务/collector StartedAt、RestartCount、实例ID与观察起点，从同一部署持续保留三轮的完整标签、首末样本、counter/reset、配置桶全集及桶组出现时间。原始查询窗口保留所需rate内窗和instant前驱；相邻窗口重叠，首轮启动不足仍记UNKNOWN。按首次异常标签时间查同实例EventStream span/log及Changes/恢复记录，并在每轮结束记录重启计数。OTLP start time仅在现有链真实可读时保存；缺失不合成。标签首次返回不是合法出生证明，counter reset也不等于重启。
3. **流量与故障。** 先用既有受限正常流量；对配置、周期及启动标签观察无需新增故障。额外至多30个串行业务请求，每秒至多1个，用既有允许路由；不扩大到外部支付或写目标。正常流量足够判断周期和已出现序列，却未必产生晚出现的EventStream错误标签；若未出现就保留生命周期UNKNOWN。本最小方案故障次数为0、恢复写入为0。确需受限故障时，应在本轮证据后具体说明无法正常观察的信号、已有注入入口、精确恢复操作及恢复后完整支持窗，再另定一次范围；本方案不预授权故障。
4. **资源与硬上限。** 仅沿用已接受sandbox manifest、exact project labels及loopback端点中的容器/网络/卷，执行前重新核对ownership和非owned基线；不增加服务、镜像、卷、端口或宿主权限。部署最多1次，不build/pull替代镜像、不自动拆除重建；最长占用90分钟（含归属核对、准备、观察和结束时已有规范的精确owned清理）。最多3轮采集，初轮先积累所需600s支持，后轮使用同一实例和重叠窗口。总HTTP读取最多500次：启动/基线最多200；每轮正常/raw最多80，配置/Logs/Traces/Changes最多20，合计300；失败/重试也计数。另Docker只读归属/身份/配置检查最多60次；任何容器内只读检查须列入同一白名单且计数，不执行任意shell。额外业务流量30次独立计数；每请求超时10s、每轮采集阶段最多120s、串行且响应每条上限2MiB，截断保留并判不足。原始周期采集可用范围查询减少轮询，不扩大次数。准备失败不自动消耗正式批次或旧停止槽位。
5. **工程修复与停止。** 最多一次采集代码的离线修复后重试，重试占上述3轮/500请求/90分钟预算，不重置额度；只允许范围内查询/解析/诊断错误，修复需先过聚焦离线检查，冻结每轮参数与源码。普通证据不足允许同轮独立检查继续；不得因候选是否命中调参数。归属不明、非owned漂移、秘密泄漏、安全/权限冲突立即停止相关操作；重复同类基础设施错误、无法证明配置、无法取得出生证据或任何上限耗尽则结束本轮并保留诊断。不得global prune、扩大权限或更换布局。结束清理由现有exact owned协议执行；失败如实保留，不操作未知资源。成功也不触发新holdout或晋升。

以上完整范围需后续一次性明确批准；本次没有任何新增live操作。结论：工程校准路径已交付；有效Kafka配置、完整默认桶配置与标签出生/启动因果仍有具体数据阻塞。不是 `v0.5 ACCEPTANCE_PASS`，不代表已修复全部live问题。

## 本轮已获授权的真实工程校准

用户在同一会话明确批准`7bbf88d`中上述完整方案，执行范围与硬上限不变。本轮记录根为`.local/engineering-calibration/live-01`，独立授权、计数与输出，不消费或恢复旧停止批次。启动计时、准备与清理均计入90分钟。下方将记录真实Docker操作、每轮采集与最终清理；历史离线段落的零live计数仅代表前次检查点。

### 实际执行与资源账本（2026-09-28 UTC）

本轮已结束，授权额度不自动续用。授权计时05:22:55.594636，启动确认05:31:55.066013，精确清理完成05:55:22.126983；含准备与清理共1946.532347秒（32分26.532秒）。当前daemon/context/Unix endpoint与接受的本机布局核对一致，执行前没有其他运行容器；三个builtin网络与三个既有匿名卷作为非owned只读基线保留，不凭旧暂停状态推定安全。新的nonce绑定owned 22容器、1网络、5卷；固定cached ARM64镜像、端口、配置与原布局，未build/pull、改SDK周期或拆除重建。

| 独立工程计数 | 实际 / 授权上限 |
|---|---:|
| 部署启动 | 1 / 1 |
| 采集轮次 | 3 / 3 |
| HTTP读取（含失败） | 239 / 500 |
| Docker及容器白名单只读（含归属/清理检查） | 59 / 60 |
| 额外正常物理业务请求 | 30 / 30 |
| 采集代码修复重试 | 1 / 1 |
| Provider、故障、恢复写入、holdout、晋升 | 各0 |

239次读取包括固定版本源码材料8次，以及每轮62次正常/原始Metrics和15次生命周期/Logs/Traces。235次HTTP 200，首轮4次错误Jaeger路径返回404；没有响应体截断。Kafka日志受300条查询上限限制，不是完整日志。正常请求为15组既有cart/checkout、合计30次物理POST，全部成功，串行间隔至少1.01秒；没有把一组两个请求算成一次。Docker真实变更共12次CLI调用：创建1网络、5卷、Compose create/start、exact stop/rm/network rm/volume rm；涉及的实际资源数量为22/1/5，不能把CLI次数当成资源数量，也不能宣称零Docker操作。

清理前重新核对daemon、完整ownership、birth IDs及非owned基线，只删除精确本任务资源。最终`cleanup.json`为`clean=true, owned_remaining=0, non_owned_unchanged=true`。未执行global prune。记录根内`operations.jsonl`在操作前计数，`receipts/`保留返回摘要；保留失败和修复前结果。旧账本继续为60 Provider请求、USD1.067489预算承诺、14/18历史live及旧闭环9/13，**另列本轮1次工程部署/3轮采集**，未借用旧槽位。原SQLite/WAL/SHM及08/09材料167文件摘要再次一致，旧注册、规则、停止状态和原结果未修改。

### 已证实的生产者配置与观测

- **Kafka broker与agent。** 本轮运行日志报告Kafka **4.3.1**、commit `26b251a451ce941d`。owned Kafka PID1实际Java命令加载`/tmp/opentelemetry-javaagent.jar`；容器内jar manifest为**2.29.0**，jar SHA-256为`546531ca690a8603d2923b6db26bbda35c6409327b1e610430ae33c2f8f68050`。只保留白名单OTel环境/系统属性和摘要，未持久化整份环境或jar。`KAFKA_OPTS`中的系统属性也展开核对；JMX目标为kafka-broker、已绑定JMX文件、cumulative temporality，没有显式export interval、agent配置文件/扩展或实验配置文件覆盖。
- **有效默认周期的依据。** [agent2.29.0依赖](https://github.com/open-telemetry/opentelemetry-java-instrumentation/blob/v2.29.0/dependencyManagement/build.gradle.kts)绑定SDK1.63.0；[MetricExporterConfiguration](https://github.com/open-telemetry/opentelemetry-java/blob/v1.63.0/sdk-extensions/autoconfigure/src/main/java/io/opentelemetry/sdk/autoconfigure/MetricExporterConfiguration.java)及[PeriodicMetricReaderBuilder](https://github.com/open-telemetry/opentelemetry-java/blob/v1.63.0/sdk/metrics/src/main/java/io/opentelemetry/sdk/metrics/export/PeriodicMetricReaderBuilder.java)声明60秒默认值。实际jar、配置排除与固定源码共同支持本轮60秒工程声明，观测周期仅作为一致性核对。没有向配置伪造显式`OTEL_METRIC_EXPORT_INTERVAL`。材料语义经源审阅，摘要校验本身不证明源码语义，正式准入限制见下文。
- **Collector与桶。** 实际容器Image与固定cached镜像身份一致，镜像元数据版本**0.157.0**，配置路径/命令无额外覆盖；不是另执行`--version`所得。绑定配置明确docker_stats **2s**、kafkametrics **10s**、span metrics flush **5s**，没有显式histogram覆盖。[v0.157.0默认配置](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.157.0/connector/spanmetricsconnector/config.go)与[connector实现](https://github.com/open-telemetry/opentelemetry-collector-contrib/blob/v0.157.0/connector/spanmetricsconnector/connector.go)对应有限毫秒桶`2,4,6,8,10,50,100,200,400,800,1000,1400,2000,5000,10000,15000`及`+Inf`，共17桶；并非从返回桶反推期望全集。

| 生产者 | 三轮原始中位周期范围 | 最大相邻间隔 | 末样本年龄范围 |
|---|---:|---:|---:|
| container | 2.000s | 2.010s | 0.038–1.707s |
| queue | 10.000s | 10.010s | 2.176–9.845s |
| span metrics | 5.000–5.001s | 5.010s | 0.861–3.588s |
| Kafka应用/JMX | 59.999–60.001s | 60.006s | 6.733–39.057s |

第一轮Kafka末样本年龄39.057秒没有仅因超过旧统一30秒常量被误判断流。三轮没有发现已建立序列中断、陈旧末点、counter reset或histogram/子集错配。这里的“没有发现”限保留的返回序列与观察窗口；不能证明所有潜在标签从未漏采。固定25%周期+1秒调度裕量未根据匹配结果修改；真实漏整周期、末点新但中间断流等仍由fixture拒绝。

### 三轮支持、生命周期与唯一修复

| 轮次 | 查询窗口（UTC） | 具备支持 / 证据不足 | 主要不足 |
|---|---|---:|---|
| 1 | 05:37:21.759–05:42:21.759 | 11 / 10 | checkout/fraud/payment业务序列及Kafka Produce p95缺左侧支持；部分rate内窗不足 |
| 2 | 05:42:49.359–05:47:49.359 | 15 / 6 | fraud/payment的error_rate、latency、request_support仍缺左侧支持 |
| 3 | 05:49:52.077–05:54:52.077 | 21 / 0 | 返回序列在固定窗口内均具备支持 |

三轮使用相同查询、周期参数、外窗300秒、各查询原内窗及首评估点前驱采集；没有缩短业务窗口、补出生前零、调候选或改Core优先级。等待已观察的新标签积累支持后，第三轮自然具备窗口支持；21/21不是预设目标，也不是正式成绩。每轮`result.json`中的`formal_pass`与`promotion_eligible`始终false，三份离线回放均精确复现。

三轮22容器均RUNNING、RestartCount=0；保留StartedAt、完整metric标签和同实例span。fraud/payment的EventStream ERROR标签分别首次采样于05:41:58.568及05:44:38.571。第二轮查到对应同一`service.instance.id`的client span：duration599.991826/600.004183秒，结束于05:41:50.134887/05:44:30.653183，gRPC status4；首次错误指标晚约8.433/7.918秒。相同操作的flagd server span报告server-side timeout；第三轮又查到下一组约600秒的完成span。all/errors查询有重叠，按trace+span去重后第二轮4条（2client+2server），第三轮8条，不能将原始汇总的8/16条当成独立事件数。

同实例其他标签更早存在，相关counter不下降，相同标签的17桶组一起出现。这支持**长寿命RPC结束后产生新的错误标签**，没有证据把它归为服务重启或已建立序列漏采。首轮checkout及Kafka p95在启动后尚未积累完整查询支持，后续已补足；最早发射前是否存在未返回样本仍未知。首次观察不是通用出生证明，OTLP start timestamp未由当前链保留。queue是gauge，context中的下降仅为`decreases`，不能被当成counter reset。fraud/payment返回日志没有EventStream文本，关联依据是span；Kafka日志只取最早300条（第三轮total1631），不宣称全日志无异常。

唯一工程修复发生于首轮后：Jaeger错误路径`/api/traces`返回4次404，改为已有允许路径`/jaeger/ui/api/traces`；同时将上下文缺失显式表示为unavailable、计数null并暴露日志返回上限。35项聚焦检查通过后才用原剩余预算采第二轮，没有重建部署或重置额度。首轮原始404和原摘要保留；`round-1/context/assessment-correction.json`追加说明“不可用”，不能把原零条当成不存在span。第三轮没有第二次修复重试。

### 源码、离线回放与正式准入限制

新增`engineering_capture.py`复用现有Owned.validate与采样支持函数，以固定步骤实现计数、资源连续性、采集及清理；`engineering_configuration.py`只允许两类Kafka只读观察；`engineering_context.py`补生命周期/日志/span；`engineering_traffic.py`逐物理请求计数。每轮独立目录拒绝覆盖，保存源码摘要、参数、请求和原始结果；运行时源码与最终离线guard不同，保留的source_sha256不被改成最终Git提交。

新增`engineering_replay.py`只读取保留文件和独立proof CAS，不打开原库、不请求网络、不操作Docker。实际已运行：

```sh
PYTHONPATH=src:. uv run python -m scripts.product_v050.engineering_replay \
  --root .local/engineering-calibration/live-01 --round 1 \
  --output .local/engineering-calibration/live-01-replay-round-1
PYTHONPATH=src:. uv run python -m scripts.product_v050.engineering_replay \
  --root .local/engineering-calibration/live-01 --round 2 \
  --output .local/engineering-calibration/live-01-replay-round-2
PYTHONPATH=src:. uv run python -m scripts.product_v050.engineering_replay \
  --root .local/engineering-calibration/live-01 --round 3 \
  --output .local/engineering-calibration/live-01-replay-round-3
```

输出均为指定目录的`replay.json`，支持数分别11/15/21且`original_diagnostics_reproduced=true`。再次离线运行须选择全新`--output`；已存在路径拒绝覆盖，输入前后摘要重验。输入根及objects是本机私有材料，不随Git推送。旧09回放入口与结果保留，本轮有效配置不能反向证明旧09运行时默认值。实际live步骤为同一根上的`engineering_capture prepare/start`、`engineering_configuration`、正常traffic三组、`engineering_capture collect --round N`与`engineering_context N`、最后`engineering_capture cleanup`；这些历史命令**不构成再次执行授权**，该根的额度已消耗。

工程默认声明与显式配置分开。`versioned_producer_defaults`要求版本、镜像、override缺省与材料摘要，工程回放检查原材料字节完整性；**它不能自动验证材料是否真的声明该默认值**。独立审阅给出反例：保留真实材料摘要却把60改成180，单纯摘要校验仍成立。因此清理后加了离线准入守卫：共同`ingestion_evidence.verify`拒绝任何非空工程默认声明；资格、freeze、evaluate、promotion均经现有收据重验链拒绝，不靠报告的false标志。60/180同proof的反例均拒绝。这里没有第二次live修复或重试，已采集原始数据/结果不改写；工程回放仍可解释同一手工审阅声明。这是明确的正式准备缺口，不宣称配置默认值已经自动获得正式资格。

### 验证范围与正式验证前剩余项

- fixture验证：正常60秒与漏采、内部中断、新标签/未知出生/reset、错误子集/总量及桶对应、rate不足、清理归属/预算保留、Kafka白名单解析、404不可用语义、输入篡改/重复回放拒绝，以及工程声明不得进入正式重验。fixture不证明真实丢包/重启恢复行为。
- 真实保留数据验证：上述有效配置材料、三轮实际周期与窗口、StartedAt/RestartCount、同实例EventStream关联、无reset/桶错配、11→15→21支持、三份精确离线复现及owned清理。没有注入真实漏采、重启或故障，不能声称这些live动态分支全部验证。
- 正式前仍需：冻结并审阅可正式接受的默认配置语义绑定（或另行授权显式配置），不能直接复用本工程声明；明确既有业务查询中EventStream超时与“健康控制”的语义关系及完整负例资格；按独立协议重新固定候选/采集/资格和未见事件；需要真实故障/恢复的动态支持验证须另行授权。OTLP出生时间不可用、未返回标签总体、启动前缺失和完整日志范围继续保留UNKNOWN。正常Core优先级与原规则不变。

工程校准路径已交付，本轮授权已结束。全部新数据都是已见开发数据；不生成`v0.5 ACCEPTANCE_PASS`、不晋升、不重启旧批次、不自动进行正式验收。

本轮离线验证：采样、摄入贯通、工程生命周期与回放聚焦58 passed；最终回放来源/UTC记录补充再经入口测试1 passed。完整回归`PYTHONPATH=src:. uv run pytest -q`为**6876 passed、21 skipped**（809.47秒），跳过项是既有私有材料/历史冻结条件，未新增skip/xfail。全库Ruff、Product mypy173文件和前述六个历史/后继验证器通过。独立只读复核确认默认声明守卫在共同正式验证链生效，无新增必须修复项。日志分别为`.local/engineering-calibration/live-01-offline-focused.log`、`live-01-full-regression.log`及`live-01-verifiers.log`。

最终源码另两次重放第三轮到`live-01-replay-final/replay.json`与`live-01-replay-repeat/replay.json`，均21项支持且精确复现，并记录回放源码摘要、原采集源码摘要及UTC；重跑只需在上方第三轮命令替换新输出目录。旧09材料重放到`live-01-old09-replay/replay.json`仍为10支持/11不足，历史assessment精确复现。本轮提交后的干净状态历史范围节点结果保存`live-01-clean-commit-check.log`；精确提交CI与最终完整tracked-diff/冻结材料校验另存同一私有目录的`live-01-ci-*`及`live-01-integrity.json`。这些后置结果以机器记录及交付回复为准，不用旧提交成功替代新提交CI。

## 2026-09-28 离线收口：默认语义绑定与健康查询核对

本节是同一工程任务的后续，基线`6ef5867b02a8220b1c1dc997d64c26c281126d04`。用户仅授权窄范围离线实现、保留材料回放、测试、报告及PR #104提交推送/精确CI；不新增活动Goal。本轮Provider、Docker、故障、恢复写入、正式事件、晋升均0，上一节真实校准的清理、计数、失败和原始结果不变。

### 默认配置现在能验证什么

新增受控`config/product-v050/reviewed-producer-defaults-v1.json`，只对应本次审阅的arm64 Kafka镜像、agent2.29.0 jar摘要及嵌入SDK1.63.0 PeriodicMetricReaderBuilder类摘要、Collector0.157.0镜像。固定8份来源URL与正文摘要，数值由映射确定：Kafka60秒；span_metrics有限桶2、4、6、8、10、50、100、200、400、800、1000、1400、2000、5000、10000、15000毫秒（另有+Inf）。这不是通用源码推理器，也不接纳未审阅版本。

新增显式`ingestion-sample-evidence-v4` / `reviewed-producer-default-credential-v1`路径。`default_credentials.resolve`从受控映射取数值，要求固定来源、镜像/jar/class/版本身份、独立容器、运行起点早于完整支持窗口、零重启、当前观察时点、deployment/occurrence/incident/requirements绑定。完整JVM投影拒绝显式周期、未知OTel选项、额外agent、配置文件及`@argfile`/VMOptionsFile/Flags不透明输入；只保留白名单值及未知存在性，不读参数文件。Collector只接纳审阅命令、入口、无覆盖环境、实际只读bind目的地/源身份摘要与所读配置内容的一致绑定；默认桶路径不接受显式histogram覆盖。纯投影函数不执行任何Docker或文件读取，调用方必须提供新鲜受控读取的实际字节，不能从旧裁剪记录补字段。

`validation_capture.protocol_v4`可将新凭证纳入现有raw receipt；共同`verify_receipt`在资格、freeze、evaluate及直接promotion重验中执行同一解析。两份解析代码及映射加入现有`evaluation_bindings`，冻结后实现变更也会失效。未自动切换live入口，不改SDK/Collector配置；v2/v3历史行为保留，旧工程`versioned_producer_defaults`继续拒绝。旧收据、旧批次和历史成绩不追溯升级。

**新路径具备正式校验能力；本轮保留材料仍不足以生成正式凭证。** 实际8份来源、镜像、jar、版本和SDK类材料均与映射一致，但旧进程投影裁剪了未知选项，旧Collector快照未保留完整Cmd/Entrypoint/Env，jar检查未保留同实例/同观察时点绑定。现有材料不能补造这些否定性证明；新协议同时要求实际配置挂载及读取内容绑定。离线审计返回`UNKNOWN_INCOMPLETE_RUNTIME_EVIDENCE`、`formal_credential_created=false`。完整新凭证的正例仅由fixture证明，不声称真实正式凭证已取得。

回归覆盖合法映射；同材料60→180；错桶、版本、镜像、jar、实例范围、启动覆盖、显式周期、未知agent、JVM参数文件、Collector命令/挂载/配置内容冲突；缺完整投影；来源内容错误。临时测试库贯通raw capture→资格→freeze→evaluate→promotion，并在篡改凭证后逐层拒绝。没有写原库或修改真实注册。

### EventStream的现行查询贡献

第三轮窗口仍为2026-09-28 05:49:52.077–05:54:52.077 UTC。现有业务查询只按service/status筛选，没有按span_name或span_kind排除EventStream；因此它同时进入错误分子、请求分母、请求支持和延迟histogram。回放使用原始query响应及真实`PrometheusConnectorV1`，保留其丢弃NaN、对有限时点取均值的行为，不补零、不改阈值。

| 服务 | 错误率有限时点均值 | 延迟p95有限时点均值 | 请求支持均值 /s | 延迟时点支持 |
|---|---:|---:|---:|---|
| fraud-detection | 0.3759530792 | 8267.204301 ms | 0.0230760442 | 31/31有限，17点为15000ms |
| payment | 0.0476190476 | 717 ms | 0.0234043964 | 21/31有限、10 NaN，1点为15000ms |

原始counter按各评估点的原300秒内窗做增量归因，仅在所有返回标签具有同一采样网格且无reset时比较，不在首次出现前补零，不另造PromQL引擎。fraud的31个共同网格窗口中17个有错误；payment的31个中21个有正请求增量、1个有错误。**两服务所有观察到的错误增量均来自EventStream**；与原错误率查询的最大绝对差分别为2.78e-17与0。两服务分别17/1个窗口有>15000ms桶外增量，全部来自EventStream。窗口互相重叠，不能把这些窗口数当成独立RPC次数。15000ms是最大有限桶边界处的分位值，并不把已保留约600秒RPC缩短为15秒。

已有同实例span说明这些是长寿命EventStream RPC结束后的超时错误标签；本节不默认超时无害。第三轮21/21仅证明返回序列具备采样支持，不能证明业务健康。没有绑定本部署/本窗口的健康baseline、完整Typed Runtime及正常诊断行动输入，因此只调用现有纯memory/`evaluate_no_incident_v22`做**显式不完整输入诊断**：`METRIC_BASELINE_COVERAGE_INCOMPLETE`、`RUNTIME_COVERAGE_OR_HEALTH_INCOMPLETE`。空baseline表示缺失，不是零健康基线；不以无STRONG异常推导健康。不生成正常诊断、NO_INCIDENT控制资格或正式PASS；`healthy_qualification=NOT_ISSUED`。旧部署baseline不能充当本次基线，22容器running也不等于Typed业务健康。

如果未来希望“业务指标”只覆盖业务入口，需先确定业务SLO的操作/方向语义，并一致界定错误分子、请求分母/支持及histogram；不能只删ERROR标签。调整将需要重新建立基线、复核已见目标/健康/Core/正常复发开发数据及固定规则在新定义下的结果，原5/5仅属于旧定义，不能继承为新定义成绩。本轮未作这种调整，查询、阈值、Core/Extension优先级均不变。

### 可复现命令、输入与输出

在本worktree执行（需本机私有保留材料，不随Git上传）：

```sh
PYTHONPATH=src:. uv run python -m scripts.product_v050.health_semantics_replay \
  --root .local/engineering-calibration/live-01 --round 3 \
  --output .local/engineering-calibration/semantic-01/retained-final
PYTHONPATH=src:. uv run python -m scripts.product_v050.health_semantics_replay \
  --root .local/engineering-calibration/live-01 --round 3 \
  --output .local/engineering-calibration/semantic-01/retained-repeat
PYTHONPATH=src:. uv run pytest tests/product_v050/test_default_credentials.py \
  tests/product_v050/test_ingestion_evidence.py \
  tests/product_v050/test_health_semantics_replay.py -q
```

输出为各新目录`replay.json`，包含逐时点增量/原始查询摘要/Connector typed facts/partial coverage/默认材料审计、输入和复用源码SHA及UTC。输入包括round-3的result、query/raw元数据和响应、固定来源及运行身份文件；程序核对原查询参数、响应摘要、状态、截断、输入前后摘要。使用MockTransport仅服务保留响应，无网络和数据库读取。重复运行须换全新输出目录，禁止输出位于输入根内、禁止覆盖既有结果。原工程`engineering_replay`入口与历史11/15/21结果保留。

### 尚需真实观测的最小范围（本轮未执行、未授权）

未来单次已授权owned观察需把Kafka实际完整命令/允许环境投影、同实例同观察时点的jar/class检查，以及Collector Cmd/Entrypoint/覆盖名、实际只读挂载与配置读取绑定一并保留；未知或不透明配置仍拒绝，不靠修改周期/桶让默认验证通过。无需重学规则、Provider、故障或新平台。这是填补运行证据，受控版本默认数值已经审阅，不再重复周期排障。

若还要判断现行健康控制，需要同部署同定义下的绑定健康baseline、完整Typed Runtime/服务集合和正常入口行动输入，并覆盖EventStream结束的原业务查询窗口。先保留超时原貌；没有健康语义依据时仍UNKNOWN，不能承诺正常流量必然得到健康资格。是否调整业务范围是另一个明确语义决策；本轮不授权或启动新的真实采集、正式控制、独立holdout或晋升。

本节聚焦验证58 passed；随后补充默认值向sampling profile传递的断言，凭证测试28 passed。全库Ruff、Product mypy（173文件）及六个历史/后继验证器通过；独立只读审阅发现的JVM参数文件、Collector挂载绑定和冻结代码范围三项已修复并复核关闭。两次回放的typed records、贡献、覆盖、输入/源码摘要一致；实际MockTransport耗时、其派生result摘要和回放UTC随运行变化，不宣称输出文件逐字节相等。原库/旧08/09证据及已结束live-01共811个冻结文件摘要未变。

完整回归命令为`PYTHONPATH=src:. uv run pytest -q`；日志位于独立`.local/engineering-calibration/semantic-01/full-regression.log`。同目录保留`focused-final.log`、`default-profile-final.log`、`ruff-final.log`、`mypy.log`、`verifiers.log`、干净提交检查、精确HEAD CI及最终tracked-diff/冻结证据完整性结果。完整回归与提交后CI以这些最终机器记录及交付回复为准，不复用上一节6876项或旧提交CI成功作为本节结论。没有新增skip/xfail或删除历史检查。


## 2026-09-28 同部署无故障工程联调（live-02）

用户于2026-09-28进一步明确授权同一任务的无故障工程联调：最多1次owned部署、3轮、90分钟含准备清理、600 HTTP读取、90 Docker/白名单容器只读、30串行正常物理请求（≤1/s）、一次范围内采集修复重试及最多2个无故障工程诊断事件。Provider、故障、恢复写入、正式验收及晋升均0；旧批次和原库只读不变。新环境、提前固定的同部署基线、完整Typed Runtime和正常API/Worker记录仅写独立工程目录。固定时间观察，不以NO_INCIDENT为目标，不改PromQL/阈值/Core顺序；按归属清理、同报告更新、提交推送PR #104并核对精确CI，不merge/release。

执行前固定清单：复用v4映射，逐轮读取Kafka进程白名单/未知存在性、jar/class摘要、Collector实际挂载配置和最新同实例身份；各操作保留真实UTC，验证为窗口结束后120秒内独立采集，不要求伪造同时刻。全部4服务Typed Runtime包含Kafka consumer实际membership；新独立Product API/Worker和数据库记录环境、实际DEPLOYMENT Changes、基线、最多2诊断和实际行动证据。无Provider配置加载，不复用原Campaign。

固定时间表相对唯一部署start完成：第1轮9分30秒保留配置/Runtime/context；12分钟通过既有DEMO_ONLY baseline builder构建5×36秒窗口（lookback180、warmup180、minimum5），因此基线窗口约6–9分钟，先固定再进入检查窗口。第2、3轮23及28分钟，各检查前300秒；最大时间偏差120秒，超过停止采集并清理，不等待理想诊断。正常流量分3组各5对cart/checkout，每对2次物理请求；组内时间分别30/150/270/390/480，1080/1140/1200/1260/1320，1440/1480/1520/1560/1600秒，每次串行间隔≥1秒。首次观测不作为出生证明；基线构建失败保留NOT_AVAILABLE，不伪造诊断。

读取范围：已保留fixed layout/images/source材料、现有产品/摄入实现；写入范围：engineering integration/capture/configuration/traffic/v4 collector、默认凭证真实时点检查、HTTP计数hook、聚焦测试、现有任务指针与本报告及STATUS后继摘要。新数据仅`.local/engineering-calibration/live-02`，计数单独账本；冻结范围为原库、旧08/09、已结束live-01及semantic-01保留材料。最终仓库范围为本worktree相对`e03a0aa8036b683076eef0c407fa10c6db7d17e0`完整tracked delta；私有CAS/数据库/凭据不提交。

预定执行入口：`PYTHONPATH=src:. uv run python -m scripts.product_v050.engineering_integration run`。create-once目录拒绝重启；普通采集缺失保留诊断，权限/归属/秘密失败停止相关操作，finally使用现有精确owned清理。HTTP及Docker读取在操作前计数，保留13次Docker读取与15分钟供清理。此命令的存在不授予后续再次执行权限。


### 本轮实际结果：联调未完成，资源已清理

实际开始2026-09-28 20:46:50.538630 UTC，结束20:56:54.534661 UTC，占用**603.996秒**。资源检查证明当时没有其他容器或Docker CLI任务；保留的非项目网络/卷语义与此前快照一致。仅启动同一owned部署一次，固定镜像/布局未变，没有修改SDK或Collector配置。

| 账目 | 本轮实际 |
|---|---:|
| owned部署 / 采集轮次尝试 / 完整轮次 | 1 / 1 / 0 |
| Docker白名单只读 / Docker资源操作命令 | 51 / 12 |
| HTTP读取 / 正常物理请求 / 成功checkout | 0 / 10 / 5 |
| 采集接线修复续接 / 新工程诊断事件 | 1 / 0 |
| Provider / 故障 / 恢复写入 / 正式事件 / 晋升 | 全部0 |

资源操作12条包括创建网络/卷/容器、唯一start及最终stop/rm；不是把真实Docker操作计为零。精确删除22容器、1网络、5卷，`cleanup.json`确认owned剩余0、non-owned unchanged。旧live-01账目239 HTTP/59 Docker只读/30正常请求/1修复保持原值；旧Provider60次、USD1.067489承诺、14/18 live和旧闭环9/13均未消费或复活。

两项接线问题按时间如实保留：

1. 启动后、首轮前代码复查发现事件API要求service ID规范排序，而目录按逻辑服务名排序。暂停并结束本任务采集进程，使用唯一获准修复额度加入`sorted(ids)`和反例测试；保持owned部署、原anchor/截止时间，确认当前归属后从第一组第2对流量续接。已完成第一对2个请求未重复。此处未发生API事件创建；是预先发现的确定性接线错误。修复前源码、暂停/续接UTC、进程与前缀响应摘要分别保留，未把续接当成第二次部署。
2. 首轮在固定9分30秒时点开始，Kafka CLI显示fraud consumer Stable且一个成员获orders:0；四服务均RUNNING/healthy、RestartCount=0。随后正常环境创建API返回**422 INVALID_REQUEST**：实际payload的`connector_configs[3].snapshot_ref`为`runtime-current.json`，不满足`^pilot/[a-zA-Z0-9_.-]{1,120}\.json$`。这是本次采集接线错误，**不是遥测不健康或v4配置被拒绝**。当时流程尚未读取完整配置/jar，也未生成环境绑定Typed Runtime、基线、诊断、Changes或指标/日志/span输入；不能从源Runtime健康推导NO_INCIDENT。唯一修复额度已用，finally立即清理；没有重建或再次live。

因此逐项结论为：**v4真实凭证未取得；本轮指标/基线覆盖未评估；正常入口没有实际诊断，谓词及EventStream对健康拒绝/Core命中的作用均NOT_EVALUATED。** 不将上一轮EventStream贡献或21/21支持移植为本轮结果，不补造诊断，不将失败称为健康holdout。先前受控默认映射仍具备正式校验能力，但本轮没有新的真实凭证来证明它通过。

### 清理后的离线修复与验证边界

Runtime引用及原子更新位置统一到`pilot/runtime-current.json`，原逐轮快照保持独立。新增`offline_preflight()`在任何Docker操作之前，用临时数据库调用真实环境创建API；坏路径复现422，正确路径201。新增实际API/Worker贯通fixture覆盖同一payload、四条真实格式DEPLOYMENT Changes、五窗基线、两类规范排序和正常诊断；网络仅由MockTransport提供。该贯通检查又暴露并修复严格typed JSON解析及Core动作排序、queue最后的memory重建问题；现在对正常诊断memory SHA逐值重验，未绕过摘要检查。配置采集移到独立于环境API的位置，避免普通API拒绝丢失独立配置证据。续接只认可完整成功前缀并拒绝已有后续输出，不宣称任意中断点都可重试。

**上述清理后修复均仅经fixture，未再次部署验证。** 原失败请求、运行源码摘要、修复续接源码摘要及结果不升级为最终代码执行证据。新聚焦检查84 passed；完整回归、Ruff、Product类型检查、六个历史验证器及精确提交CI另存下述机器记录，不以旧提交成功替代。

实际命令与证据位置：

```sh
# 本轮真实命令，授权现已结束；不得据此重跑。
PYTHONPATH=src:. uv run python -m scripts.product_v050.engineering_integration run
PYTHONPATH=src:. uv run python .local/engineering-calibration/integration-02/resume.py

# 可重复离线验证，全部使用临时测试库/fixture，无Docker/外部HTTP。
PYTHONPATH=src:. uv run pytest tests/product_v050/test_engineering_integration.py -q
PYTHONPATH=src:. uv run pytest tests/product_v050/test_engineering_integration.py \
  tests/product_v050/test_engineering_capture.py \
  tests/product_v050/test_default_credentials.py \
  tests/product_v050/test_ingestion_evidence.py \
  tests/product_v050/test_health_semantics_replay.py -q
PYTHONPATH=src:. uv run pytest -q
```

私有采集根`.local/engineering-calibration/live-02`保存authorization、source-version、operations、逐操作receipt、repair/repair-resume、traffic、round-1 Runtime源投影、原422 payload、failure与cleanup；`.local/engineering-calibration/integration-02`保存原进程/续接日志、只读失败复现、临时库测试、汇总`result.json`、冻结833文件清单、全量回归及后置CI/完整性。API拒绝发生在外部读取前，所以HTTP原始响应CAS为空；独立新Product库的环境/事件/注册均0。私有根、数据库、CAS和凭据不提交，旧原库只读，历史原始材料不改写。

### 剩余最小真实范围

本轮用户要求的完整同部署输入仍未收齐，目标未达成。下次仍只需同一既有布局的一次owned无故障工程观察，沿用前述固定时序与上限（1部署、3轮、90分钟、600 HTTP/90 Docker读取、30串行正常物理请求、最多2工程事件），在部署前先通过现在的真实API/Worker离线接线检查；按真实时点收全v4投影与实例绑定、提前固定基线、完整Typed Runtime及正常入口输入，并覆盖EventStream结束窗口。没有现成数据能替代这些观测。当前1次部署授权已经消费且资源清理，剩余额度不授权重新部署；**本报告不启动或请求逐条读取审批，不自动重跑，也不转为正式验收。** Provider、故障、恢复写入与晋升继续0。业务PromQL、阈值、Core/Extension顺序及固定规则均未修改。


## 新补跑 live-03：离线门禁与执行授权

当前新增授权（live-03）：先通过不替换 samples 的完整离线 API/Worker/v4 预演，再执行一次新的有界无故障补跑。新目录 `.local/engineering-calibration/live-03`，最多1 owned部署、3轮、90分钟含准备清理、600 HTTP读取、90 Docker/容器白名单只读、30正常串行物理请求（≤1/s）、2工程事件、2次范围内接线/采集修复。离线修改不占live修复额度。保持固定观察时钟，不重建、不重复已完成动作；安全/秘密/归属问题、无法安全续接、错过时间或额度耗尽即清理停止。Provider/故障/恢复写/正式holdout/晋升均0；旧live-02及原账本只读不变。先独立保存配置，再走同部署提前固定基线、完整Typed Runtime和正常API/Worker；不以健康为目标。完成同报告更新、提交推送PR #104与精确CI，不merge/release。

完整预演命令：`PYTHONPATH=src:. uv run pytest tests/product_v050/test_engineering_rehearsal.py -q`。真实入口也在任何 Docker 操作前执行该门禁。模拟边界仅为外部 HTTP、白名单 Docker 配置投影输入及时间；受控映射和公开源码材料未替换。凭证解析、采集、原始响应捕获、支持判断、真实 API/Worker、基线绑定、诊断与 memory 摘要重验均执行。实例出生固定为部署起点，基线窗口为起点后360–540秒，配置观测570秒起，构建720秒，诊断1380/1680秒。配置投影是 fixture，不代表真实配置读取成功；真实结果另行追加。


### live-03 实际完成：配置有效，正常诊断完整执行，业务健康未通过

UTC 2026-09-29 01:53:15.317815 开始，02:21:51.134628 结束；总占用 **1715.816813秒（28分35.817秒）**。启动前重新确认无其他Docker CLI/容器任务，非owned网络/卷语义与允许的原布局一致；固定镜像/路由/SDK/Collector配置未修改。本次没有采集修复或续接，没有重新部署。

| 工程独立账目 | 实际 / 上限 |
|---|---:|
| owned部署 / 完整采集轮 | 1/1；3/3 |
| HTTP读取 / Docker白名单只读 | 372/600；71/90 |
| Docker资源操作命令（明确计入） | 12，含create/start和精确清理 |
| 正常物理请求 / 工程事件 / live修复 | 30/30；2/2；0/2 |
| Provider / 故障 / 恢复写 / 正式holdout / 晋升 | 全部0 |

30请求为15对cart/checkout，全部200，15个checkout响应成功；物理请求最小间隔1.026374秒。精确清理22容器、1网络、5卷后owned剩余0，非owned未变。旧60 Provider请求/USD1.067489预算承诺/14 of 18 live及旧闭环9 of 13账本保持原样，本次是并列工程记录，不消费停止批次。

**配置凭证。** 首轮在环境API前独立保存完整白名单JVM投影、实际jar/class摘要、Collector命令/挂载内容及实例绑定，保留各操作真实时间（process 02:03:07.569367、jar .694847、挂载 .739707、runtime .783621 UTC）。后两轮重复取得各自窗口的独立证明。实际Kafka固定image/agent2.29.0/jar/class、SDK1.63.0对应源码与Collector0.157.0固定image/挂载配置全部满足现有受控映射；Kafka默认周期60秒、span metrics默认桶集合由验证器从映射导出。基线支持及第2/3轮三个新凭证均为 `VERIFIED`，原始采集 `acquire/verify` 每组21/21支持；清理后再次从原始CAS用同一 `dc.resolve/ie.verify` 重验并逐对象比对原assessment相同。旧凭证/工程声明未升级。

**基线与输入。** 真实环境 `env-9c1a893dea7b9483edd71a8d` 经API创建/验证成功。正常baseline Worker以DEMO_ONLY、lookback180/warmup180、5×36秒窗口构建5/5；使用01:59:34.912020–02:02:34.912020 UTC数据，02:05:34.912020构建，随后固定为 `base-4e251aeb4ae934c625432b23`（SHA `8e7b6456e2a318319367ee9b85b18218a2d117e0fd8e99d22983a2622aab4c29`）。两个检查窗口分别02:11:37.587622–02:16:37.587622、02:16:37.489415–02:21:37.489415 UTC，均晚于基线固定。没有同窗自建、旧部署基线、补零或人为抬高基线。四服务真实StartedAt以DEPLOYMENT Changes入库，Runtime包括Kafka consumer membership；三轮均RUNNING/healthy/restart_count=0。

**正常入口诊断。** 两个工程事件经API创建，两次Worker均SUCCEEDED；实际行动、connector输入/原始响应、Typed Runtime、指标、基线、Changes、日志/trace、evidence index和memory全部保留。两次memory摘要纯函数重验完全一致。两次结果均 `INSUFFICIENT_EVIDENCE / ABSTAIN`：决策trace为 `known_admission_status=NONE`、extension_match_count=0、required_coverage_satisfied=true、failed_sources=[]；novelty识别到强残余，但 `OPEN_WORLD_ROOT_AMBIGUOUS`，未唯一定位根因。没有Core命中、没有learned复用或知识晋升。这不是接线失败，也不是NO_INCIDENT。

| 正常Worker实际指标 | 第2轮 | 第3轮 | 固定基线 |
|---|---:|---:|---:|
| fraud-detection latency（ms） | 8528.4385 | 6344.9462 | 95 |
| payment latency（ms） | 6502.1283 | 8226.7645 | 2.8242 |
| fraud-detection error ratio | 0.0472365 | 0.0364388 | 0 |
| payment error ratio | 0.0377331 | 0.0519438 | 0 |

上述是正常connector对窗口查询结果的实际归约值，不是单次RPC时延。两轮fraud/payment的 `METRIC_LATENCY_STRONG` 成立；第3轮另有payment `METRIC_ERROR_RATE_STRONG`。四服务 `RUNTIME_HEALTHY` 成立；第2轮fraud/kafka/payment、第3轮kafka有 `RESOURCE_MEMORY_GROWTH_STRONG`。健康纯函数覆盖四服务，但两次均 `accepted=false / STRONG_ANOMALY_PRESENT`。内存增长是另外的资源证据，未证明泄漏，也不归因于EventStream或默认视为无害。

**EventStream与证据边界。** 从与正常诊断相同评估时点的保留原始calls和histogram矩阵重算可观察counter增量：两窗口fraud/payment全部已观察错误增量、全部>15000ms桶增量均归属于EventStream；每窗口每服务各评估点最大为1次。第3轮末点增量已0，但前面的评估点仍包含这些长RPC，不能用末点替代全窗口。EventStream实际进入现有请求分母、错误分子及延迟分布；它经强延迟（及第3轮payment错误）参与拒绝健康，未造成Core命中。这里证明观测增量归属和查询贡献，不证明根因、完整p95因果分解、用户请求健康或超时无害，也不把离线排除后的假想结果当正式定义。

上下文捕获与正常诊断证据范围不同：第2轮全生命周期上下文在all/errors查询合计返回16条EventStream记录，按trace/span ID去重为8个span，正常诊断的有界trace输入不含EventStream；部分日志/trace存在截断标记。当前Jaeger connector按请求窗口的start/end查询；长寿命RPC的开始与结束跨窗，是需要明确的证据时间语义。`required_coverage=true`表示所需来源/服务可用，不代表所有日志/span被完整枚举。原始请求及截断情况保留，没有补造诊断。部分业务查询为30/31有限点，采样支持不保证每个业务结果点有限；Kafka空span集合的派生零值不能当业务健康证据。原始指标的21/21也仅覆盖返回序列，未证明未知标签人口完整、出生前零或完整负例。短基线早于首个长RPC结束，具备构建与采样支持，仍不等于稳定业务健康总体。

### 交付、验证与后续最小缺口

本轮源码只将新入口绑定live-03/两次修复上限，并把完整离线预演设为任何Docker前的必过门禁；新增集成测试和保留公开源码fixture，没有修改业务PromQL、阈值、Core/Extension顺序或固定规则。保留原来的局部samples空函数测试，新增完整链路没有跳过内部校验。反例覆盖错误pilot路径、逆序ID、过期/错实例凭证、过早基线与重叠检查窗口；源码材料和默认映射不被mock。Docker配置投影/遥测响应/时间的fixture通过只证明接线；上述live-03才证明真实配置读取、真实v4验证和正常诊断。

```sh
# 重复离线预演：临时库、外部读fixture，不修改原库/历史结果
PYTHONPATH=src:. uv run pytest tests/product_v050/test_engineering_rehearsal.py -q
# 实际live-03执行命令：create-once，授权现已消费，不得自动重跑
PYTHONPATH=src:. uv run python -m scripts.product_v050.engineering_integration run
# 保留数据离线复核（私有脚本；输出create-once，不覆盖先前结果）
PYTHONPATH=src:. uv run python .local/engineering-calibration/integration-03/analyze.py
PYTHONPATH=src:. uv run python .local/engineering-calibration/integration-03/window-audit.py
```

私有输入根 `.local/engineering-calibration/live-03`：逐操作账本/receipts、三轮proofs/Runtime/context、baseline与baseline-support、两诊断的samples/raw.jsonl、diagnosis-job/raw.jsonl、diagnosis/evidence/index/memory/health-predicate、独立Product库/CAS、finished/cleanup。输出 `.local/engineering-calibration/integration-03/result.json`（v4/支持重验/决策记录）、`window-audit.json`（逐实际评估点EventStream贡献、预算/基线时序）以及测试/CI/完整性记录。需要重复离线审核时选新输出文件/目录；不覆盖历史记录，不打开原库写连接。公开提交不包含这些私有库/CAS/请求或任何凭据。

聚焦预演/接线/默认凭证48项通过；Ruff全库和Product mypy通过。稳定交付的全量回归、六个历史验证器、干净提交范围检查与精确提交CI结果记录于同一私有输出目录的verification/ci文件，不能以旧提交成功替代。最终完整性相对 `eb3b66a274c7125ec0bcd020215c5a0a3b93d751` 校验本次完整tracked delta及976个声明冻结文件；其中包含原库、旧批次和live-02/integration-02，未将本次证据混入旧记录。

本轮两项主要交付已取得：真实v4验证，以及两次输入齐备、原因可追溯的正常API/Worker诊断；**不是正式健康holdout或v0.5 ACCEPTANCE_PASS**。配置/接线不再需要一次部署来证明“能执行”。正式验证前仍需先决定业务总体语义：最小建议是显式区分业务RPC与控制流RPC的指标用途，并明确长RPC按结束时间进入指标、如何关联跨窗trace；保留EventStream自身超时信号，不直接过滤错误标签。若接受范围变化，必须另作明确协议版本、用全部已见目标/健康/Core开发数据重新回放（包括原5/5），旧成绩不改写。本轮没有实施此建议。

仍需真实观测的最小内容是一个未来另行授权的无故障同部署观察：先建立包含完整EventStream结束周期、来源范围明确的基线，再固定后续检查窗口；同时保留连续内存序列及运行时信息，区分启动/GC变化与持续增长。现有短基线及有界trace不能替代该观测，也不需要为本轮结果新增故障或Provider。该建议不自动启动新部署或正式验收，不恢复停止批次。

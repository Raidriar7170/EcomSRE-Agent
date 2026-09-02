# Product v0.2.4 阻塞 Human Brief

仓库状态：`REVIEW_REQUIRED`

一次 cAdvisor 聚焦尝试在当前 Docker Desktop 上只能看到 root/self series，没有可用的 checkout container series，且已 `CLEAN`。实现随后按授权切换为现有 OTel Collector contrib `docker_stats` receiver；checkout 由稳定的 `container_name="ecomsre-live-sandbox-v1-checkout"` 选择，未把 `host_metrics` 或整机指标作为服务证据。

当前 Goal 授权的三项 telemetry coverage 已由 live evidence 通过：Metrics 返回 checkout 的 3 个请求 kind，各且仅各一条；Resources 返回 checkout 的唯一一条 10 秒、5 点记录；Jaeger 在 `minimum_duration_ms=0` 时返回 checkout 的 12 条有效 `TraceSpanV22`，并且 `SOURCE_TRACES_COVERAGE_GAP` 已消失。因此可以保真记录：

```text
ECOMSRE_PRODUCT_V024_METRICS_CONTRACT_PASS
ECOMSRE_PRODUCT_V024_RESOURCES_COVERAGE_PASS
ECOMSRE_PRODUCT_V024_TRACES_COVERAGE_PASS
```

最终 fresh No-Fault 运行 `20260902T1155Z-b2bd47c1` 创建了新的 Runtime Authority 与新的 `DEMO_ONLY` Baseline，Baseline 5/5 windows 通过，健康流量为 30/30，且 Diagnosis 没有 capability limitation。该运行仍真实结束为 `OPEN_WORLD`：相对 fresh Baseline，checkout 的 `LATENCY_P95_MS` 从 mean `96.8389830230388 ms` 变为 `145.29307826172328 ms`，10 秒 Resource 记录的 memory slope 从 Baseline mean `-11659.946666666667 B/s` 变为 `12697.6 B/s`。未修改的 Diagnosis 将其保守分类为 `UNREGISTERED_OBSERVED_ANOMALY`。

两个 anomaly 均独立满足冻结语义：latency ratio 为 `1.500357332616`，超过 `1.5` 阈值且 delta 为 `48.454095238684 ms`；负的 Baseline memory slope 使冻结比较式的 trigger floor 为 `1 B/s`，而当前值为 `12697.6 B/s`。五个 memory 样本本身并非单调增长，因此若要排除这种健康运行噪声，需要改变 Resource trend 的语义或冻结 anomaly 规则；这不属于当前 Goal 的 telemetry coverage 修复权限。

未修改的 v0.2.3 No-Fault scorer 返回：

```text
ECOMSRE_PRODUCT_V023_NOFAULT_NOT_SUPPORTED
FALSE_INCIDENT_TERMINAL
FRESH_HEALTHY_RUNTIME_MISSING
LOGS_PROFILE_BINDING_MISSING
```

其中 fresh Runtime connector 本身为 `SUCCESS_NONEMPTY`、checkout `RUNNING/healthy`；`FRESH_HEALTHY_RUNTIME_MISSING` 是 `OPEN_WORLD` 未把 Runtime ref 纳入本次 resolved diagnosis refs 后的级联结果。`LOGS_PROFILE_BINDING_MISSING` 则是独立结果：ACTIVE P01 未改变，但有界 Logs 结果真实为 `truncated=true`，不满足 unchanged scorer 对 fresh P01 evidence 的完整性要求。这些结果不能重写为成功。

依照 Goal 的 stop condition，本工作树保留现场并停止：不修改 Diagnosis/scorer，不再次运行 Incident/No-Fault，不运行合并前全量套件，也不 commit、push、创建或合并 PR。因此不能铸造：

```text
ECOMSRE_PRODUCT_V024_TELEMETRY_CAPABILITY_REPAIR_COMPLETE
```

清理结果：Product `CLEAN`，Demo `CLEAN`，queue default 与外层 baseline 均未改变。

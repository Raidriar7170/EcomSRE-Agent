# EcomSRE-Agent Phase 0 Goal Mode Prompt

> 用法：将本文件全文复制到 Codex 新任务中。建议在已经完成 Planning Packet Consistency Audit，且结果为 `PASS` 或 `PASS WITH FIXES`、`Remaining blockers: None` 后执行。

---

进入 **EcomSRE-Agent Phase 0 goal mode**。

你必须持续推进，直到满足下列二者之一：

1. Phase 0 的全部验收条件真实通过；
2. 遇到符合本 Prompt 所定义 stop condition 的真实阻断，并保存足够证据证明当前无法安全继续。

不要因为完成了脚手架、写完代码、单次 smoke test 成功、某个子任务完成，或“看起来基本可用”而提前停止。

---

# 1. 权威输入

开始前完整阅读：

- `AGENTS.md`
- `docs/PROJECT_CHARTER.md`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/DECISIONS.md`
- `docs/SAFETY_BOUNDARIES.md`
- `docs/PHASE_0_ACCEPTANCE.md`
- `docs/OPEN_QUESTIONS.md`

这些文档及其中的 `DEC-001` 至 `DEC-012` 是当前权威。

如本 Prompt 与冻结文档存在冲突：

1. 安全边界优先；
2. `PHASE_0_ACCEPTANCE.md` 优先；
3. 不允许自行修改已冻结 Decision；
4. 将冲突记录为 blocker；
5. 不得通过静默改变范围、版本、阈值或运行环境来解决冲突。

---

# 2. Phase 0 唯一目标

在当前 Apple Silicon Mac 上建立一个**可重复、确定性、无 LLM**的故障实验闭环：

```text
bootstrap
→ preflight
→ 启动固定版本 OpenTelemetry Demo
→ readiness
→ baseline 测量
→ 注入 adServiceFailure
→ fault 测量
→ reset
→ recovery 测量
→ Prometheus / Jaeger / OpenSearch 新鲜数据验证
→ 保存完整 evidence
→ 连续完成 3 个完整循环
```

Phase 0 结束时，必须能够通过一个非交互命令完成正式验收，并得到明确退出码和可审计证据。

---

# 3. 明确 Non-goals

本阶段禁止实现或提前搭建：

- LLM provider；
- Agent；
- Single-Agent；
- Multi-Agent；
- Incident Commander；
- Metrics Agent；
- Logs Agent；
- Trace Agent；
- Change Agent；
- RCA Judge；
- Remediation Planner；
- Verifier Agent；
- LangGraph；
- CrewAI；
- AutoGen；
- Microsoft Agent Framework；
- FastAPI 服务；
- React / Vite UI；
- Kubernetes；
- kind；
- AIOpsLab；
- 自动修复；
- Restricted Executor；
- Feature Service；
- Ranking Service；
- 模型训练；
- Agent benchmark；
- Agent 性能结论；
- 生产级自治 SRE claim。

不要因为后续可能需要而创建这些模块的空壳、接口占位或未使用依赖。

---

# 4. 必须完成的工作

## 4.1 最小仓库基础结构

建立最小且清晰的 Phase 0 实现结构。可以采用类似：

```text
src/ecomsre/
  phase0/
  environment/
  evidence/
  telemetry/
  scenarios/

scripts/
config/
tests/
third_party/
docs/
```

但应以现有规划文档为准，不要为了形式创建大量空目录。

技术约束：

- Python 3.11；
- `uv`；
- Pydantic；
- pytest；
- 标准库优先；
- 不引入 Agent 框架；
- 不引入不必要的数据库或消息队列。

创建并维护：

- `pyproject.toml`
- `uv.lock`
- `Makefile`
- `.gitignore`

所有运行逻辑必须通过明确的 Python CLI、Make target 或受控脚本调用，禁止依赖手工 UI 操作完成正式验收。

---

## 4.2 上游版本冻结

按冻结决定引入：

- OpenTelemetry Demo tag：`3.0.0`
- commit：`1755859a9de82c2e5e225be68abc401a5ebf2b4f`

要求：

- 使用只读 submodule，或规划文档明确允许的等价固定方式；
- 不跟踪 `main`；
- 不使用浮动 tag；
- 不修改上游源码；
- 不维护私有 Compose fork；
- 优先复用上游 `compose.yaml` 与 `compose.observability.yaml`；
- 不使用 `compose.full.yaml`；
- 不引入 extras。

检查实际 Compose 依赖图。若官方 core stack 正常运行需要 Kafka、Accounting、Fraud Detection 等服务，可以让它们运行，但：

- 它们不是 Phase 0 的验收对象；
- 不为其开发额外工具；
- 不将其写入 Phase 0 能力 claim；
- 不因此扩大项目范围。

---

## 4.3 Image Digest Lock

Bootstrap 阶段允许联网拉取固定镜像。

完成后生成机器可读的 image lock manifest，至少记录：

- logical image name；
- source reference；
- resolved digest；
- architecture；
- platform；
- image ID；
- acquisition timestamp；
- upstream commit；
- Compose config hash。

正式验收必须使用缓存镜像，并使用：

- `--pull never`；
- 或能够提供同等保证的机制。

若发现：

- 固定镜像仅支持 `amd64`；
- digest 不一致；
- ARM64 manifest 缺失；
- 镜像无法按冻结版本复现；

则：

1. 不静默启用 amd64 模拟；
2. 不切换到另一个上游版本；
3. 不切换到 `main`；
4. 不修改上游源码掩盖问题；
5. 保存完整证据；
6. 按 stop condition 终止。

---

## 4.4 Preflight

实现自动 preflight，并记录：

- macOS version；
- host architecture；
- CPU；
- total memory；
- Docker 可见 CPU；
- Docker 可见 memory；
- available disk；
- Docker client version；
- Docker server version；
- Docker Compose version；
- Docker engine information；
- relevant port use；
- relevant container、network、volume；
- project ownership labels；
- required image availability；
- upstream commit；
- Compose config hash。

支持基线：

- Apple Silicon；
- 原生 `linux/arm64`；
- Docker Desktop；
- Docker Compose v2；
- 至少 16 GB 内存；
- 至少 25 GB 可用磁盘。

当前机器预期为：

- Apple Silicon M5 Pro；
- 48 GB unified memory；
- 2 TB SSD。

但不得因为机器信息已知而跳过自动探测。

发现以下情况必须 fail closed：

- 同名但 ownership 不明的资源；
- 固定端口被其他项目占用；
- ownership 无法确认；
- Docker 未启动；
- Docker 资源不足；
- 宿主架构或镜像架构不兼容；
- 固定镜像不可用；
- 上游 commit 与冻结值不符。

禁止停止、删除、接管或修改未知资源。

---

## 4.5 资源隔离

使用稳定的 Docker Compose project name，例如：

```text
ecomsre-phase0
```

所有由本项目创建的资源必须带可验证 ownership 信息。

至少隔离并记录：

- container；
- network；
- volume；
- temporary directory；
- evidence directory；
- process metadata；
- lock file；
- port ownership record。

清理逻辑只能处理满足以下全部条件的资源：

- 当前项目创建；
- ownership label 匹配；
- 当前运行或项目 manifest 明确记录。

永久禁止：

- `docker system prune`；
- 全局 volume cleanup；
- 停止未知容器；
- 删除未知 network；
- 删除未知 volume；
- 杀死未知宿主进程；
- 修改 Docker Desktop 全局设置；
- 接管已存在但归属不明的资源。

---

## 4.6 环境生命周期命令

实现概念上等价的命令：

```bash
make bootstrap
make preflight
make env-up
make readiness
make phase0-acceptance
make env-down
```

命令名称如与规划文档不一致，以规划文档为准，但必须满足：

- 非交互；
- 有明确退出码；
- 可重复；
- 幂等，或能够检测并明确报告非幂等状态；
- 有 timeout；
- 有日志；
- fail closed；
- 不依赖手工点击 flagd UI；
- 不执行广泛 Docker cleanup。

`env-down` 不得默认删除失败证据。

若提供类似：

```bash
make env-down PRESERVE_EVIDENCE=1
```

则默认行为仍必须保护 evidence。

---

## 4.7 Scenario Control 与 Hidden Truth 隔离

实现 `adServiceFailure` 的程序化注入和 reset。

注入方式必须基于冻结上游版本中真实、可重复的 feature flag 控制接口。

正式验收禁止依赖手工 UI。

至少在逻辑和文件层面分离：

```text
scenario controller / evaluator
observer probe
```

Scenario controller 可以知道：

- 精确 feature flag key；
- 精确 value；
- scenario identity；
- expected fault mechanism；
- expected phase transition。

Observer probe 不得读取：

- feature flag key；
- feature flag value；
- scenario 文件名；
- scenario label；
- expected answer；
- evaluator-only artifact；
- hidden ground truth。

Observer probe 只能通过以下可观察信号判断影响：

- 实际请求结果；
- Prometheus；
- Jaeger；
- OpenSearch；
- 被明确允许的服务状态。

为 observer-visible artifact 与 evaluator-only artifact 使用不同目录、不同数据对象或不同访问接口。

---

## 4.8 确定性 Request Probe

关闭 `OQ-003`。

实现一个不读取 hidden truth 的确定性 request probe。

Probe 必须：

- 对真实运行中的 Demo 发起请求，或观测真实运行流量；
- 产生或观测真实 Ad Service `GetAds` 调用；
- 记录请求时间、结果、错误和可用关联信息；
- 不通过读取 flag 状态判断故障；
- 不把 readiness 当成业务成功；
- 不依赖随机手工操作；
- 能在 baseline、fault、recovery 窗口重复执行；
- 能与当前 run 和 scenario phase 关联。

若实际 OTel Demo 接口无法直接逐请求观察 `GetAds`，允许结合：

- 受控前端请求；
- k6 load generator；
- Prometheus span-derived metrics；
- 其他固定、可审计的上游流量路径。

但必须确保最终统计分母是实际 `GetAds` 调用数，并在文档中明确 measurement semantics。

---

## 4.9 查询字段发现与冻结

关闭 `OQ-002`。

不要凭记忆假设 OTel Demo 3.0.0 的字段或指标名称。

通过固定版本真实环境确认：

- Ad Service 的实际 `service.name`；
- Prometheus 中用于 `GetAds` 总量和错误量的指标；
- `demo.*` 属性；
- Jaeger 查询所需 service / operation；
- OpenSearch 中 service identity 和时间字段；
- Trace 与日志可用的关联字段。

将最终查询冻结为版本化 fixtures，例如：

```text
config/queries/prometheus/
config/queries/jaeger/
config/queries/opensearch/
```

每个 fixture 至少记录：

- 目标；
- query；
- expected schema；
- upstream version；
- applicable service；
- failure semantics；
- freshness semantics。

禁止把较旧的 `app.*` 字段作为静默 fallback。

---

## 4.10 单循环确定性状态机

使用确定性状态机实现单循环：

```text
PREFLIGHT
BOOTSTRAP_VERIFIED
ENV_STARTING
READINESS
STABILIZING_BASELINE
MEASURING_BASELINE
INJECTING
STABILIZING_FAULT
MEASURING_FAULT
RESETTING
STABILIZING_RECOVERY
MEASURING_RECOVERY
TELEMETRY_VALIDATION
FINALIZING
SUCCESS / FAILED / BLOCKED / UNSAFE
```

状态转换必须：

- 有明确进入条件；
- 有明确退出条件；
- 有 timeout；
- 有失败原因；
- 写入 event log；
- 不依赖自然语言判断；
- 不调用 LLM；
- 不允许越过中间状态静默继续。

---

## 4.11 统计验收

每个窗口：

- baseline；
- fault；
- recovery；

都必须满足：

- 至少 200 次有效 `GetAds` 调用；
- 每个窗口最长等待 180 秒；
- 默认 stabilization 30 秒，并可配置；
- 使用当前窗口内的增量或 rate；
- 不读取跨窗口累计 counter 直接判定；
- 保存总调用数；
- 保存错误数；
- 保存错误率；
- 保存窗口开始和结束时间；
- 保存原始查询及原始响应；
- 计算并保存 95% Wilson confidence interval。

主验收阈值：

```text
baseline error rate ≤ 1%
fault error rate ∈ [5%, 20%]
recovery error rate ≤ 1%
```

正式验收必须连续运行 3 个完整循环。

只有三轮全部通过，Phase 0 才能判定为 SUCCESS。

任何失败都必须：

- 不自动放宽阈值；
- 不删除 run；
- 不只保留成功重跑；
- 保留完整证据；
- 记录失败阶段；
- 记录失败原因；
- 记录是否属于环境、上游、统计或遥测失败。

---

## 4.12 Telemetry Readiness

### Prometheus

必须证明：

- 存在当前 run 的新鲜数据；
- 能查询到 Ad Service 调用量与错误量；
- 数据时间戳落入当前窗口；
- 查询结果可归属当前 run 或当前 scenario phase。

### Jaeger

必须证明：

- 存在当前 run 新生成的 Trace；
- 可归属 `adservice`；
- 至少包含与 Ad 调用相关的 span；
- Trace 时间落入当前 run 时间窗。

### OpenSearch

必须证明：

- 存在当前 run 新生成的日志；
- 可归属 `adservice`；
- 日志时间落入当前 run 时间窗；
- 不是上一次 run 的残留日志。

至少使用以下维度建立关联：

- service identity；
- run 时间范围；
- scenario phase。

若上游提供，则额外记录：

- trace_id；
- span_id；
- request correlation。

不强制三类遥测必须对应完全相同的一次请求，因为可能存在采样差异。

任一通道无法满足 freshness 与 ownership：

- telemetry readiness 失败；
- 即使 Prometheus 已检测到错误率变化，也不得宣布完整 Phase 0 SUCCESS。

---

## 4.13 Evidence Store

每次运行生成唯一 `run_id`。

建议证据目录结构：

```text
artifacts/runs/<run_id>/
  manifest/
    machine.json
    environment.json
    upstream.json
    images.json
    compose.json
  events/
    state_transitions.jsonl
    commands.jsonl
  cycles/
    cycle-001/
      baseline/
      fault/
      recovery/
      telemetry/
      verdict.json
    cycle-002/
    cycle-003/
  hidden/
    scenario_control/
  observer/
    requests/
    metrics/
    traces/
    logs/
  final/
    acceptance.json
    summary.md
    checksums.json
```

要求：

- observer 与 hidden artifact 逻辑分离；
- 所有重要 artifact 有 content hash；
- 保存原始响应；
- 保存解析后结果；
- 保存执行命令；
- 保存退出码；
- 保存时间戳；
- 保存状态转换；
- 保存失败原因；
- 大型运行证据默认不提交 Git；
- schema、示例和说明可以提交；
- 失败 run 不自动删除。

---

## 4.14 正式运行的外部依赖约束

关闭 `OQ-004`。

Bootstrap 可以联网。

正式 acceptance run 必须：

- 使用已缓存镜像；
- 不拉镜像；
- 不安装 package；
- 不获取代码；
- 不更新 submodule；
- 不访问外部模型 API；
- 不依赖外部 container registry；
- 不依赖外部 package index；
- 不依赖外部 SaaS；
- 不依赖未声明的运行时外部服务。

不要求声称“密码学证明宿主机没有任何网络数据包”。

应提供可审计证据证明：

- acceptance 命令未执行 pull；
- Docker 使用 `--pull never` 或等价机制；
- 没有 package installation；
- 没有代码下载；
- 所有声明依赖均来自固定仓库与缓存镜像；
- 没有发起未声明的外部运行时依赖请求。

---

## 4.15 测试

至少实现以下测试。

### Unit Tests

- threshold logic；
- Wilson interval；
- state transitions；
- timeout handling；
- resource ownership；
- unknown-resource fail closed；
- evidence hashing；
- run ID；
- window isolation；
- stale telemetry rejection；
- hidden-truth isolation；
- failed-run preservation。

### Contract Tests

- Prometheus response parsing；
- Jaeger response parsing；
- OpenSearch response parsing；
- feature flag control adapter；
- image lock schema；
- manifest schema；
- evidence schema；
- command-log schema。

### Integration Tests

- preflight；
- environment startup；
- readiness；
- inject / reset；
- one-cycle smoke；
- full three-cycle acceptance。

不允许用 mock integration result 替代真实正式验收。

---

## 4.16 文档更新

实现完成后更新：

- README；
- Phase 0 运行说明；
- troubleshooting；
- evidence schema；
- exact command reference；
- observed machine fingerprint；
- observed upstream query fields；
- known limitations；
- failure recovery instructions。

关闭：

- `OQ-001`
- `OQ-002`
- `OQ-003`
- `OQ-004`

每一项关闭都必须引用实际 evidence。

不要关闭：

- `OQ-005`
- `OQ-006`
- `OQ-007`
- `OQ-008`

---

# 5. 安全要求

全程禁止：

- 广泛 Docker cleanup；
- 删除未知资源；
- 停止未知容器；
- 修改宿主机；
- 修改全局 Docker 设置；
- 使用生产凭据；
- 访问企业系统；
- 修改非项目目录；
- 静默改变上游 commit；
- 静默切换为 amd64；
- 静默放宽验收阈值；
- 通过隐藏失败重跑制造通过结果；
- 使用任意 shell 执行未受控清理；
- 运行 `docker system prune`；
- 删除不带 ownership label 的资源。

需要 Docker、网络或文件系统高权限操作时，遵循当前 Codex 审批模式。

---

# 6. 验证要求

完成前至少执行并记录：

```bash
uv sync --frozen
uv run pytest
git diff --check
make preflight
make readiness
make phase0-acceptance
```

必要的 bootstrap、env-up 和 env-down 应按文档执行。

`make phase0-acceptance` 必须真实完成 3 轮完整循环，不得使用：

- dry-run；
- mock result；
- 手工伪造 evidence；
- 预置成功 JSON；
- 只执行单轮后复制结果。

---

# 7. SUCCESS 完成条件

只有以下全部成立，才可报告 `SUCCESS`：

1. 固定上游版本和镜像 digest；
2. Mac ARM64 环境通过 preflight；
3. 一键启动和 readiness 通过；
4. `adServiceFailure` 可程序化注入和 reset；
5. observer probe 不读取 hidden truth；
6. baseline、fault、recovery 阈值满足；
7. 连续三轮完整循环通过；
8. Prometheus、Jaeger、OpenSearch 均有当前 run 新鲜数据；
9. 正式验收不依赖外部 registry、package index、模型 API 或 SaaS；
10. evidence 完整且可审计；
11. 测试通过；
12. `OQ-001` 至 `OQ-004` 已通过证据关闭；
13. 没有实现任何 Phase 1+ 功能；
14. 没有违反安全边界；
15. `git diff --check` 通过。

---

# 8. BLOCKED / FAILED / UNSAFE 条件

## 8.1 允许终止为 BLOCKED 的情况

仅限：

- 固定上游镜像不存在可用 ARM64 manifest；
- 固定 OTel Demo 3.0.0 在受支持基线上存在可复现上游阻断；
- Docker Desktop 不可用且需要用户手工处理；
- 必需端口被未知资源占用，无法安全处理；
- 实际上游无法提供冻结要求中的必需遥测通道；
- 必须修改冻结 Decision 才可能继续；
- 当前 Codex 权限无法完成必要但安全的操作，且需要用户批准。

## 8.2 FAILED_ACCEPTANCE

用于：

- 环境可以运行，但三轮统计验收未通过；
- fault 注入效果不落在冻结阈值；
- recovery 未恢复至阈值；
- telemetry freshness gate 未通过；
- acceptance 运行出现未声明外部依赖；
- 测试或正式验收失败，但不属于安全风险。

## 8.3 UNSAFE

用于：

- 资源归属不明；
- 必须删除或停止未知资源才可继续；
- 需要突破安全边界；
- 状态无法确定；
- 可能影响非项目资源；
- 发现命令具有超出项目命名空间的影响范围。

终止为 BLOCKED、FAILED_ACCEPTANCE 或 UNSAFE 时，必须输出：

- blocker / failure 分类；
- 首次发生步骤；
- 精确命令；
- 退出码；
- 相关日志；
- environment manifest；
- 已尝试的安全修复；
- 为什么不能继续；
- 最短人工处理步骤；
- 明确列出禁止静默采用的替代方案。

---

# 9. Goal Mode 行为约束

你应当持续推进并自主处理普通工程问题，例如：

- 修复代码错误；
- 调整测试；
- 重试可安全重试的本地命令；
- 修复解析和 schema 问题；
- 修复项目内路径、配置和脚本；
- 根据真实上游数据冻结 query fixture；
- 完善 evidence 和文档。

但遇到下列情况必须停止，而不是自行改变合同：

- 需要改变冻结上游版本；
- 需要改变统计阈值；
- 需要改变主机支持基线；
- 需要引入 Kubernetes 或 AIOpsLab；
- 需要跳过任一遥测通道；
- 需要读取 hidden truth 才能让 observer 通过；
- 需要执行广泛 Docker cleanup；
- 需要访问非项目资源；
- 需要实现 Phase 1+ 功能才能通过 Phase 0。

---

# 10. 最终输出格式

完成后只输出：

```text
Verdict: SUCCESS / BLOCKED / FAILED_ACCEPTANCE / UNSAFE

Goal status:

Implemented files:

Upstream pin and image digests:

Commands executed:

Test results:

Acceptance cycle results:

Telemetry readiness:

Evidence location:

Closed open questions:

Remaining open questions:

Safety checks:

Known limitations:

Recommended next phase:
```

不要仅汇报“代码已完成”。

只有在正式三轮验收真实通过后，`Recommended next phase` 才能写：

```text
Phase 1: Read-only observability tool layer and Single-Agent RCA baseline.
```

完成后停止，不进入 Phase 1。

# EcomSRE-Agent Phase 0 Bounded Repair + One Real Smoke Loop

进入 **EcomSRE-Agent Phase 0 bounded-repair goal mode**。

本次任务不是重新执行完整的 Phase 0 Goal Prompt，也不是继续扩展架构。你必须基于当前仓库做**最短、可审计的修复**，处理已确认的 Must Fix，并以**一次真实、非 canonical 的端到端 smoke loop**作为停止目标。

持续推进，直到：

1. 本 Prompt 定义的 bounded repair 全部完成，并真实完成一次 smoke loop；或
2. 命中本 Prompt 定义的 `BLOCKED`、`FAILED_SMOKE` 或 `UNSAFE` stop condition，并保存足够证据。

不要因为代码编译、离线测试通过、Docker 启动、单个遥测接口可访问或故障开关切换成功而提前停止。

---

# 0. 当前基线与权限边界

## 0.1 当前审阅基线

当前公开仓库：

- Repository: `Raidriar7170/EcomSRE-Agent`
- Reviewed commit: `051fc3de3be4186b4597bb1e8a2b83b274870ddc`
- Upstream OTel Demo tag: `3.0.0`
- Upstream commit: `1755859a9de82c2e5e225be68abc401a5ebf2b4f`

开始前执行并记录：

```bash
git status --short
git rev-parse HEAD
git submodule status
```

要求：

- 不 reset、checkout、clean、rebase 或覆盖用户现有修改；
- 若 HEAD 已在 reviewed commit 之后，先审阅新增 diff，并在最终报告中说明；
- 若存在无关未提交修改，保留它们，不擅自改写；
- 不 commit、不 push、不创建 PR、不发布 release。

## 0.2 权威输入

完整阅读：

- `AGENTS.md`
- `docs/PROJECT_CHARTER.md`
- `docs/ARCHITECTURE.md`
- `docs/ROADMAP.md`
- `docs/DECISIONS.md`
- `docs/SAFETY_BOUNDARIES.md`
- `docs/PHASE_0_ACCEPTANCE.md`
- `docs/OPEN_QUESTIONS.md`
- `docs/PHASE_0_GOAL_MODE_PROMPT.md`
- `docs/superpowers/plans/2026-07-30-ecomsre-phase0.md`

本 Prompt 只授权本次 bounded repair 和一次真实 smoke loop。若与旧文档存在冲突：

1. 永久安全边界优先；
2. 本 Prompt 对“当前修复范围、开发工具缓存、smoke 目标和停止条件”的明确规定优先；
3. 不得修改 `DEC-001` 至 `DEC-012` 的业务含义、上游版本或 canonical 阈值；
4. 发现必须修改 Decision 才能继续时，停止为 `BLOCKED_DECISION_CHANGE_REQUIRED`。

## 0.3 明确禁止

本次禁止：

- Phase 1 或更高阶段功能；
- 任何 LLM provider、Single-Agent、Multi-Agent、Commander、Judge 或 Planner；
- LangGraph、CrewAI、AutoGen、Microsoft Agent Framework；
- FastAPI、React、Kubernetes、kind、AIOpsLab；
- 自动修复、Restricted Executor、Feature Service、Ranking Service；
- 模型训练；
- broad Docker cleanup；
- `docker system prune`；
- 删除或停止未知容器、network、volume 或进程；
- 修改 Docker Desktop 全局设置；
- 使用 `uvx`；
- 临时联网下载未冻结开发工具；
- 运行 Ruff 或修复现有 219 个 Ruff 问题；
- 新增与本次修复无关的抽象层、数据库、消息队列、插件系统或安全框架；
- 为了通过 smoke 放宽 canonical Phase 0 阈值或删除失败证据。

---

# 1. 本次唯一交付目标

完成下列 bounded repair：

1. 修复真实 CLI 跨进程执行路径；
2. 让 bootstrap 真正生成并验证 ARM64 image lock；
3. 修复 command evidence 中进程退出码与项目终态混淆；
4. 固定项目内 UV cache / temp 边界，消除 `uvx` 类工具违规；
5. 启动真实 OTel Demo，从真实数据完成 Task 7 telemetry/probe fixture 冻结；
6. 更新过期 authority 文档与当前状态；
7. 保存一份脱敏的旧 `UNSAFE` 事件审阅证据；
8. 实现并运行一次真实、非 canonical 的 smoke loop；
9. 安全停止所有已证明属于本项目的资源；
10. 保存完整 smoke evidence 和最终报告。

本次成功只允许声明：

```text
ONE_REAL_SMOKE_LOOP_PASSED
```

不得声明：

```text
PHASE_0_SUCCESS
PHASE_0_COMPLETE
PRODUCTION_READY
MULTI_AGENT_COMPLETE
```

正式三轮 canonical acceptance 不属于本次执行目标。

---

# 2. 先做最小状态修正

在实现前修正文档状态，但不要写成已经通过 smoke。

## 2.1 `AGENTS.md`

将错误的：

```text
PLANNING_FROZEN
Phase 0 implementation has not started
```

更新为准确状态，例如：

```text
PHASE0_BOUNDED_REPAIR_IN_PROGRESS

- Phase 0 offline implementation exists.
- Offline unit/contract/fixture-backed tests have passed previously.
- Live bootstrap, image lock, telemetry promotion, and smoke loop are not yet verified.
- Current disposition remains REVIEW_REQUIRED until this bounded repair completes.
```

完成 smoke 后再更新为：

```text
PHASE0_REAL_SMOKE_VERIFIED

- One non-canonical real smoke loop passed.
- Formal three-cycle Phase 0 acceptance has not been executed.
- Phase 1 is not authorized.
```

## 2.2 `docs/PHASE_0_ACCEPTANCE.md`

保留 canonical 合同，不修改三轮、200 attempts、180 秒和错误率阈值。

只修正过期事实：

- 不再声称所有命令都不存在；
- 明确区分：implemented、repairing、not yet formally accepted；
- 增加一小节说明 `phase0-smoke` 是 `NON_CANONICAL` diagnostic，不能产生 Phase 0 `SUCCESS`。

## 2.3 `docs/OPEN_QUESTIONS.md`

不要预先关闭任何 OQ。

完成 live smoke 后：

- 只关闭真实证据足以关闭的项目；
- `OQ-004` 在没有 canonical offline acceptance 之前默认保持 open；
- 每个关闭项必须引用本次真实 artifacts；
- 不得以测试 fixture、截图或 Codex prose 关闭 OQ。

---

# 3. 修复项目内开发工具与缓存边界

## 3.1 `.gitignore`

至少加入：

```gitignore
.ecomsre-cache/
.ecomsre-tmp/
.env
.env.*
!.env.example
```

保留：

```gitignore
artifacts/
.venv/
.pytest_cache/
.ruff_cache/
```

但不要忽略：

```text
docs/review-evidence/
```

## 3.2 Make / UV 环境

在 Makefile 或固定项目脚本中设置：

```text
UV_CACHE_DIR=<repo>/.ecomsre-cache/uv
TMPDIR=<repo>/.ecomsre-tmp
```

要求：

- 路径必须由仓库根目录解析，不使用硬编码 `/Users/...`；
- 运行前创建项目内目录；
- 不写 `$HOME/.cache/uv`；
- 不使用 `uvx`；
- 不新增 Ruff；
- 本次测试使用项目锁定的 pytest；
- development/bootstrap 阶段若确需下载 Python 依赖，必须使用项目内 cache 并记录；
- live smoke 阶段使用 `uv run --frozen --no-sync`，不得在线安装依赖。

## 3.3 临时目录

生产 CLI 的受控 subprocess 环境不得默认写 `/tmp`。

将 `TMPDIR` 绑定到项目内 `.ecomsre-tmp/<run-or-process-scope>/`，并确保：

- 路径在 repo 内；
- 当前用户拥有；
- 非 group/world writable；
- 不跟随 symlink；
- 清理只处理本项目创建的临时文件；
- failed smoke 不删除 evidence。

---

# 4. 修复 Command Evidence 模型

当前模型错误地要求外部进程退出码等于 EcomSRE outcome 退出码。修复为明确的三层语义。

## 4.1 新字段

command evidence 至少记录：

```text
process_exit_code: int | null
process_timed_out: bool
classification: Outcome
terminal_exit_code: int
```

含义：

- `process_exit_code`：真实子进程返回值；超时或无法启动时允许 null；
- `classification`：EcomSRE 对该命令或步骤的分类；
- `terminal_exit_code`：`classification.exit_code`；
- 不要求 `process_exit_code == terminal_exit_code`。

继续记录：

- 完整 argv 的脱敏版本；
- cwd；
- UTC start/end；
- monotonic start/end/duration；
- stdout/stderr 的独立 artifact 路径和 hash；
- timeout；
- run_id；
- command/purpose ID；
- 是否允许网络；
- 预期文件系统作用域；
- 观察到的文件系统作用域；
- classification reason code。

## 4.2 单一审计执行入口

所有真实外部命令必须经过一个统一、可审计 runner。

至少覆盖：

- git submodule 操作；
- docker context/info/version/compose；
- image pull/manifest inspect/image inspect；
- lifecycle up/ps/down；
- telemetry curl/HTTP 若通过 subprocess；
- smoke orchestration 调用的外部命令。

禁止绕开 runner 直接调用 `subprocess.run()`，测试专用 fake 除外。

## 4.3 旧 UNSAFE 事件审阅证据

从本机当前已有 artifacts 中寻找原始：

- UNSAFE evidence bundle；
- terminal acceptance record；
- command evidence；
- Human Brief。

在：

```text
docs/review-evidence/phase0-unsafe-20260730/
```

创建脱敏审阅包，至少包含：

```text
incident-summary.md
command-record.json
affected-paths.txt
test-summary.txt
current-disposition.json
```

要求：

- 将 `/Users/<name>/...` 脱敏为 `$HOME/...`；
- 不包含 key、cookie、token、authorization header；
- 不提交完整 artifacts；
- 不改变原始事实；
- 若某个原始字段不存在，写 `NOT_RECORDED`，不得根据日志文本推断伪造；
- 明确区分已证实事实与推断；
- 记录旧事件的 `process_exit_code` 若原始证据存在；否则写 null。

---

# 5. 修复真实 CLI 跨进程执行合同

当前生产 CLI 不得依赖测试注入的内存对象。

## 5.1 实现真实 `phase0 preflight`

`phase0 preflight` 必须在当前进程中真实收集：

- host facts；
- Docker Desktop / context / daemon facts；
- current upstream commit；
- resolved Compose hash；
- image lock verification；
- relevant ports/resources；
- ownership state；
- local cache/pull policy；
- supported/blocked result。

它必须：

- 返回稳定 exit code；
- 写 machine/environment/preflight evidence；
- 不修改 Docker 资源；
- 不依赖调用者注入 `AuthenticatedPreflightEvidence`；
- 允许作为单独诊断命令运行。

## 5.2 写操作前同进程 fresh preflight

不要把 `AuthenticatedPreflightEvidence` 作为跨进程可持久化对象。

生产路径采用：

```text
每个需要 authority 的命令
→ 在当前进程重新收集新鲜只读事实
→ issue fresh in-process evidence
→ 立即执行对应动作
```

具体要求：

### `up`

- 当前进程重新执行 fresh preflight；
- preflight 成功后才允许 Compose mutation；
- 不要求 CLI 调用者先传入内存 evidence；
- 不读取测试 monkeypatch authority。

### `health` / `status`

- 从固定 artifacts 重新认证 ownership context；
- 重新验证当前 Docker context、daemon ID、endpoint 和资源；
- 现场生成 readiness；
- 不依赖旧进程的 `readiness_evidence`。

### `inject` / `reset`

- 重新认证 ownership；
- fresh environment health；
- fresh three-signal readiness；
- 只有通过后才打开 evaluator control runtime；
- 继续保持 observer/evaluator 隔离。

### `stop`

- 重新认证 ownership；
- fresh daemon/endpoint/resource revalidation；
- 不要求 Prometheus/Jaeger/OpenSearch 全部 ready；
- stop 只针对当前 ownership manifest 完全匹配的资源；
- ownership 不确定则 `MANUAL_INTERVENTION_REQUIRED`，不扩大清理。

## 5.3 测试注入边界

可以保留 dependency injection 方便 unit tests，但：

- 生产 `main()` 默认路径必须自行收集真实 evidence；
- production success 不能依赖 test capability token、fixture registry 或 SimpleNamespace；
- 增加 contract test，证明从新的 Python 进程调用 Make targets 时 authority 可以重新建立。

---

# 6. 让 Bootstrap 真正生成 Image Lock

## 6.1 精确上游

Bootstrap 只允许：

- 初始化声明的 submodule；
- 获取固定 commit/tag；
- 拉取冻结镜像；
- 检查 registry manifest；
- 生成 image lock；
- 写项目证据。

继续禁止：

- 跟踪 main；
- fallback 到其他 tag；
- amd64 emulation；
- patch upstream；
- 使用 `latest`。

若 `--depth 1` 导致 `3.0.0` tag 无法验证，可显式获取**唯一允许的 tag 或固定 commit**，但不得执行泛化 fetch main。

## 6.2 解析真实 Compose inventory

使用：

- `compose.yaml`
- `compose.observability.yaml`
- `config/phase0/compose.phase0.yaml`

生成 resolved Compose JSON，并记录：

- exact stdout；
- sha256；
- unique image references；
- service → image mapping；
- platform；
- pull policy；
- port publication plan。

不允许因为重复 image reference 就错误拒绝整个 Compose；若多个 service 合法共享同一 image reference，应：

- image inventory 按 unique source reference 锁定；
- service mapping 单独记录；
- 不把共享镜像当作安全冲突。

如现有模型错误要求每个 service image reference 唯一，修复该模型和测试。

## 6.3 ARM64 manifest 与本地镜像

对每个 unique image reference：

1. 获取 image-index/manifest digest；
2. 证明存在 `linux/arm64` platform manifest；
3. 记录 resolved ARM64 digest；
4. 使用 `--platform linux/arm64` 拉取；
5. 记录本地 image ID；
6. 复核 source、digest、architecture、OS；
7. 保存原始 manifest/inspect evidence。

若任一必需镜像无 native ARM64：

- 停止为 `BLOCKED_UPSTREAM_ARM64_UNAVAILABLE`；
- 不启用 amd64 模拟；
- 不换版本；
- 保存完整镜像和服务列表。

## 6.4 Image lock 写入

当前 `config/phase0/image-lock.json` 是 `UNINITIALIZED` placeholder。

允许 bootstrap 将其**一次性**替换为真实 candidate lock，要求：

- 原文件必须仍是合法 `UNINITIALIZED` placeholder；
- 原文件若已是 LOCKED，不覆盖，只验证；
- atomic/write-once；
- 写后重新加载和验证；
- lock 绑定 upstream commit 和 resolved Compose hash；
- 记录创建时间和所有 unique image entries；
- 本次不 commit/push；
- 最终报告明确它是本地 working-tree candidate，尚未得到 Git 发布授权。

---

# 7. 在启动环境前修复端口暴露边界

真实 smoke 前，resolved Compose 必须通过端口安全检查。

要求：

- 所有宿主机发布端口只能绑定 `127.0.0.1` 或 `::1`；
- 不允许 `0.0.0.0`、`::` 或空 host IP；
- 仅发布 smoke 实际需要的服务端口；
- 其他服务只通过 Docker network 访问；
- 不为每个 core service 暴露随机 host port。

最小宿主机访问面建议只包括：

- `frontend-proxy`；
- `prometheus`；
- `jaeger`；
- `opensearch`；
- `flagd` 的 OFREP 端口；
- 若真实 readiness 必须，再加入最小额外端口。

使用 Compose override，而不是修改 submodule。

在 mutation 前程序化检查 resolved Compose：

- host binding 全部 loopback；
- target/service 在 allowlist；
- 无未知固定端口冲突；
- 未通过则 `UNSAFE_PORT_EXPOSURE`。

---

# 8. 完成真实 Task 7 Telemetry / Probe 冻结

## 8.1 启动真实 owned environment

按顺序：

```text
bootstrap
→ preflight
→ up
→ health/readiness
```

要求：

- `up` 使用 `--pull never --no-build`；
- 不在 smoke 期间拉镜像；
- 所有资源拥有 project/run labels；
- 所有 host ports loopback；
- startup 后重新发现真实 container/network/port IDs 并生成 authenticated ownership manifest；
- 无法证明 ownership 时停止，不执行 inject。

## 8.2 发现真实 Prometheus 字段

从当前 run 的真实数据确认：

- Ad 的 exact emitted `service.name`；
- `GetAds` exact operation；
- raw counter metric；
- total attempts label set；
- error classification label/value；
- target incarnation/restart identity；
- scrape interval；
- maximum accepted lag；
- counter reset/staleness behavior；
- exact raw-counter queries。

要求：

- canonical measurement 不使用 `rate()`、`increase()` 或 `delta()`；
- 使用窗口边界前后 raw counter samples 计算 delta；
- 若 counter reset、target restart、series drift 或 stale marker，窗口失败；
- 错误 series 缺失是否代表零，必须由 live evidence 证明并写入 fixture；
- 保存 raw API responses。

## 8.3 发现真实 Jaeger 与 OpenSearch 字段

确认并冻结：

### Jaeger

- service identity；
- operation；
- API query；
- trace/span time semantics；
- current-run freshness；
- Ad-related span 判定。

### OpenSearch

- index；
- service identity field；
- timestamp field；
- trace_id / span_id field（若存在）；
- current-run freshness；
- Ad log 判定。

不允许把旧 run 数据当作当前 readiness。

## 8.4 独立 deterministic probe

从真实环境验证一个 observer-only probe，优先检查当前候选：

```text
GET /api/data?contextKeys=telescopes
```

但不得仅因源码中存在候选值就直接冻结。

必须证明：

- 请求经 frontend business path；
- 真实触发或可归因到 `GetAds`；
- 不读取 flag state、flag key、scenario name 或 evaluator-only artifacts；
- baseline、fault、recovery 都可执行；
- response contract 明确；
- raw request/response 和 GetAds attribution evidence 已保存。

## 8.5 Promotion

只有真实 current-run artifacts 完整时，才允许将：

```text
config/phase0/telemetry-queries-v3.0.0.json
```

从 `UNRESOLVED/CANDIDATE` 更新为 `FROZEN`。

Promotion proof 必须引用：

- live Prometheus responses；
- live Jaeger responses；
- live OpenSearch responses；
- emitted identity evidence；
- counter mapping；
- probe → GetAds attribution；
- upstream/Compose hash；
- fixture hash；
- 独立的 deterministic review artifact。

若任何 backend 不能被可靠冻结，停止为：

```text
BLOCKED_TELEMETRY_FIXTURE_UNRESOLVED
```

不要使用 test fixture 替代 live proof。

---

# 9. 实现真实非 canonical Smoke Runner

## 9.1 接口

增加一个明确的诊断入口，例如：

```bash
make phase0-smoke
```

它可以调用：

```text
python -m ecomsre.cli phase0 accept --diagnostic-smoke
```

或一个等价的、受控的 Python entry point。

要求：

- canonical `phase0-accept` 默认合同仍是 3 cycles；
- `phase0-smoke` 必须显式标记 `canonical=false`；
- smoke 不生成 acceptance-report.json；
- smoke 生成独立 `smoke-report.json`；
- smoke pass 可以返回进程 exit code 0，但报告不得使用 Phase 0 `Outcome.SUCCESS` 语义；
- 推荐使用现有 `DiagnosticRunResult` 或等价强类型模型；
- 不创建新的泛化 orchestration framework。

## 9.2 Smoke 参数

本次真实 smoke 固定：

```text
cycles: 1
stabilization_seconds: 30
minimum_getads_attempts_per_window: 100
window_deadline_seconds: 120
baseline_max_error_rate: 0.01
fault_min_error_rate: 0.05
fault_max_error_rate: 0.20
recovery_max_error_rate: 0.01
```

这些是本次 smoke 的 recorded diagnostic policy。

必须明确：

- 由于 cycle 数和 sample requirement 非 canonical，不能产生 Phase 0 success；
- error-rate boundaries 保持与 canonical contract 一致；
- 任何 diagnostic override 都写入 manifest 和 report；
- 不修改 `Phase0Policy()` 的 canonical defaults。

## 9.3 Smoke 顺序

真实执行：

```text
1. project-local Python environment verified
2. bootstrap already complete and image lock verified
3. fresh preflight
4. environment up with no pull/no build
5. ownership closeout
6. initial readiness
7. 30s stabilization
8. baseline window: >=100 GetAds attempts, <=120s
9. inject adServiceFailure, ack/readback <=30s
10. 30s stabilization
11. fault window: >=100 attempts, <=120s
12. reset, ack/readback <=30s
13. 30s stabilization
14. recovery window: >=100 attempts, <=120s
15. final Prometheus/Jaeger/OpenSearch freshness
16. evidence finalization
17. safe owned stop
18. smoke report and checksums
```

每个 window 保存：

- UTC 和 monotonic bounds；
- raw Prometheus start/end samples；
- attempts/errors/delta；
- error rate；
- Wilson interval；
- exact fixture version/hash；
- probe raw request/response；
- telemetry raw responses；
- decision reason。

## 9.4 Smoke pass 条件

全部满足：

- bootstrap/image lock verified；
- fresh supported preflight；
- no unknown resource/port；
- environment up with no pull/no build；
- ownership authenticated；
- Task 7 registry FROZEN from live proof；
- baseline >=100 attempts and error <=1%；
- fault >=100 attempts and error 5%–20%；
- recovery >=100 attempts and error <=1%；
- inject/reset readback each <=30s；
- current-run Prometheus fresh；
- current-run Jaeger fresh；
- current-run OpenSearch fresh；
- probe independent from hidden truth；
- observer/evaluator split preserved；
- safe owned stop completed；
- evidence hashes complete；
- no external pull/install/fetch during smoke；
- tests pass；
- no Phase 1 implementation。

最终 smoke report：

```json
{
  "canonical": false,
  "diagnostic_status": "PASSED",
  "phase0_complete": false,
  "formal_three_cycle_acceptance_executed": false
}
```

不得把它写成 Phase 0 `SUCCESS`。

---

# 10. 测试策略

## 10.1 必须新增或修复的 focused tests

至少覆盖：

- fresh preflight can be collected in a new process；
- `up` does not require injected in-memory evidence；
- `health/status/inject/reset/stop` rebuild authority in process；
- stop does not require telemetry readiness；
- command evidence separates process and terminal exit codes；
- `uvx` is absent from repository commands/docs；
- UV/TMP paths remain under repo；
- image inventory permits legitimate shared image references；
- image lock is generated once and then immutable；
- missing ARM64 fails closed；
- resolved Compose rejects non-loopback host exposure；
- live-only registry cannot be promoted by test capability；
- smoke result cannot be mistaken for canonical acceptance；
- smoke cannot close Phase 0；
- failed smoke evidence is retained；
- every external command is audited。

## 10.2 不得做的测试替代

不得用：

- synthetic telemetry；
- fixture Docker output；
- monkeypatch authority；
- prewritten success JSON；
- copied artifacts；
- test capability token；
- manual flagd UI；

替代最终真实 smoke。

## 10.3 验证命令

使用项目内 cache/temp，执行并记录：

```bash
uv sync --frozen
uv run --frozen --no-sync pytest
git diff --check
make phase0-bootstrap
make phase0-preflight
make phase0-smoke
```

必要时 smoke runner 内部执行 up/health/inject/reset/status/stop。

不要运行：

```bash
uvx ...
ruff ...
make phase0-accept
```

本次不执行正式三轮 acceptance。

---

# 11. Evidence 输出

本次真实 run 使用唯一 opaque `run_id`，建议保存在：

```text
artifacts/phase0/observer-visible/<run_id>/
artifacts/phase0/evaluator-only/<run_id>/
artifacts/phase0/reports/<run_id>/
```

至少包含：

```text
observer-visible/<run_id>/
  machine-manifest.json
  environment-manifest.json
  run-manifest.json
  inputs/
  commands/
  lifecycle/
  telemetry/
  cycles/001/baseline/
  cycles/001/fault/
  cycles/001/recovery/
  dependency-audit/

evaluator-only/<run_id>/
  scenario-ground-truth.json
  control/
  control-intents.jsonl
  control-events.jsonl
  readbacks/
  locks/

reports/<run_id>/
  smoke-report.json
  checksums.sha256
  human-summary.md
```

要求：

- raw 与 parsed evidence 都保留；
- observer 不泄漏 flag key/value、scenario identity 或 evaluator paths；
- command stdout/stderr 不只保存在内存；
- 失败 run 不删除；
- safe stop 结果进入最终报告；
- 所有重要文件有 hash。

---

# 12. Stop Conditions

## 12.1 `BLOCKED`

仅在下列真实情况停止：

- Docker Desktop 未运行或需要用户操作；
- 当前 Docker context/daemon 不符合 accepted baseline；
- 未知端口或资源冲突；
- 固定镜像缺失 native ARM64；
- 固定 upstream/tag 无法安全获取或验证；
- Compose 无法在不修改 frozen Decision 的情况下运行；
- Task 7 某必需 backend 无法从真实环境可靠冻结；
- 需要用户批准当前 Codex 权限之外的安全操作；
- 需要修改 DEC-001 至 DEC-008 才能继续。

## 12.2 `FAILED_SMOKE`

环境可运行，但出现：

- baseline/fault/recovery sample timeout；
- error-rate threshold failure；
- inject/reset timeout；
- telemetry freshness failure；
- probe attribution failure；
- evidence incomplete；
- safe owned stop failure但状态仍明确；
- smoke tests fail。

保留现场和 evidence，不重跑到“碰巧成功”后隐藏首次失败。

允许在同一任务中修复明确工程 bug 后创建**新的 run_id**重试，但：

- 原失败 run 必须保留；
- 最终报告列出全部真实 attempts；
- 不得只报告最后一次成功；
- 最多进行 3 次真实 smoke attempts；
- 3 次仍未通过则停止 `FAILED_SMOKE`。

## 12.3 `UNSAFE`

立即停止：

- ownership 无法证明；
- 需要停止/删除未知资源；
- host port 暴露到非 loopback；
- 命令写入未授权项目外路径；
- 出现 `uvx` 或未冻结工具下载；
- 需要 broad cleanup；
- evaluator truth 泄漏到 observer；
- Docker daemon/context 发生未解释变化；
- 状态无法确定；
- evidence 无法安全保存；
- 需要突破永久安全边界。

停止后只允许：

- 保存证据；
- reset 单一 allowlisted fault（若状态可证明）；
- stop 已证明属于项目的资源；
- 输出人工处理步骤。

---

# 13. 防止范围膨胀

本次遵守：

- 优先复用已有模型、store、ownership、scenario 和 telemetry 代码；
- 可删除不必要复杂度；
- 不新增新的 HMAC/Capability 层，除非现有层无法满足跨进程真实路径且有最小证明；
- 不为未来 Phase 创建接口；
- 不增加测试数量作为目标；
- 不追求 Ruff clean；
- 不重写整个仓库；
- 不重新进行 grill-me；
- 不做第二套 runtime；
- 不把一次 smoke 扩展成三轮正式验收；
- 不进入 Phase 1。

若发现实现计划将明显超过本 Prompt 范围，先收缩为最短闭环，而不是继续抽象。

---

# 14. 完成条件

只有下列全部成立，才可报告 `ONE_REAL_SMOKE_LOOP_PASSED`：

1. authority 文档状态准确；
2. 项目内 UV/TMP 边界生效；
3. 不再使用 `uvx`；
4. command evidence 正确区分 process/classification/terminal exit；
5. 旧 UNSAFE 事件有脱敏 review evidence，且未伪造缺失字段；
6. `phase0 preflight` 是真实命令；
7. 后续命令不依赖跨进程内存注入；
8. bootstrap 生成并验证真实 ARM64 image lock；
9. resolved Compose host ports 全部 loopback 且最小化；
10. 真实 OTel Demo 启动；
11. Task 7 registry 从真实 current-run evidence 冻结；
12. 独立 probe 被真实证明可归因到 GetAds；
13. 一次真实 baseline→inject→fault→reset→recovery loop 通过；
14. Prometheus、Jaeger、OpenSearch current-run freshness 通过；
15. safe owned stop 通过；
16. smoke evidence 和 checksums 完整；
17. focused tests 和完整 pytest 通过；
18. `git diff --check` 通过；
19. 没有执行正式三轮 acceptance；
20. 没有进入 Phase 1。

---

# 15. 最终输出格式

完成后只输出：

```text
Verdict: ONE_REAL_SMOKE_LOOP_PASSED / BLOCKED / FAILED_SMOKE / UNSAFE

Baseline commit:

Current HEAD and working-tree status:

Files changed:

Must Fix closure:
- Cross-process CLI authority:
- Bootstrap/image lock:
- Command evidence schema:
- UV/TMP safety boundary:
- Task 7 live telemetry registry:
- Authority documentation:
- UNSAFE review evidence:

Commands executed:

Python dependency/cache behavior:

Image lock summary:

Resolved Compose and loopback port audit:

Real smoke attempts:
- run_id:
- baseline attempts/errors/rate:
- fault attempts/errors/rate:
- recovery attempts/errors/rate:
- inject acknowledgement:
- reset acknowledgement:
- Prometheus freshness:
- Jaeger freshness:
- OpenSearch freshness:
- probe attribution:
- environment stop:
- diagnostic status:

Evidence locations:

Tests:

Open questions closed:

Open questions remaining:

Safety assessment:

Known limitations:

Formal Phase 0 acceptance executed: No

Phase 0 complete: No

Recommended next step:
```

只有 smoke 真实通过时，Recommended next step 才写：

```text
External review of the live smoke evidence, then a separate bounded task for the formal three-cycle Phase 0 acceptance.
```

不要写 Phase 1。

完成后停止。

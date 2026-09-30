# EcomSRE-Agent Repository Instructions

## Authority

Use this order of authority:

1. The user's explicit, scoped request.
2. The accepted decisions in `docs/DECISIONS.md`.
3. Safety boundaries in `docs/SAFETY_BOUNDARIES.md`.
4. Phase acceptance contracts, beginning with `docs/PHASE_0_ACCEPTANCE.md`.
5. Architecture, charter, roadmap, and open questions.

Do not silently reinterpret an accepted decision. If implementation evidence
invalidates one, stop and propose a new Decision Record.

## Current state

当前唯一活动目标：[Trace 状态保真与操作级判别特征](docs/goals/EcomSRE_Trace_Representation_Repair_Codex_Goal.md)，2026-09-30 通过 /goal 激活。沿用当前工作树，暂停 D/D-lite；先执行离线修复与四个已见开发事件计算。用户已明确批准新增≤USD3/160次Provider调用及本地提交，仍受原累计≤USD20/1600次限制；不推送、更新远程 PR、Docker/live、新下载或未见测试。下列为保留的历史结果与授权，不据此恢复旧任务。

本轮研究交付：原trace-only服务级24事件×A/B/C/D共96条测试已结束，未观察到D收益，全能力覆盖仍不完整。上一轮16条开发（C/D完成3/8与1/8）因共用相反预测校验误约束C，不支持策略比较。本轮协议拆分后的8条开发检查已结束：C完成4/4，D完成2/4，拒答均0；D两条因真实目标/引用违规未完成。D实际执行4次对比（仅1次可判定）及4次普通ReAct回退；不判策略优劣，不启动未见测试或追加修订。累计529请求/USD6.185609，全部历史与失败记录保留。见[同一阶段报告第12节](docs/results/semantic-investigation-v1/README.md)。

本轮结果：live-03 已结束并 CLEAN：1次新owned部署、3完整轮、372 HTTP读取、71 Docker白名单只读、12 Docker资源操作命令、30正常物理请求、2工程事件、0修复；总占用1715.816813秒。基线及两诊断窗口的真实v4配置均VERIFIED、每组21/21原始样本支持；同部署DEMO_ONLY基线5/5窗口固定，四服务Typed Runtime齐全。两正常API/Worker诊断均SUCCEEDED且memory重验一致，诊断为INSUFFICIENT_EVIDENCE/ABSTAIN：required_coverage=true、failed_sources=[]，多个服务强残余异常导致OPEN_WORLD_ROOT_AMBIGUOUS；健康谓词STRONG_ANOMALY_PRESENT拒绝。owned剩余0、非项目未变，Provider/故障/恢复写/正式holdout/晋升0。采样支持不等于健康验收；此授权已消费，不自动重启，旧live-02失败和停止账本不变。

当前新增授权（live-03）：先通过不替换 samples 的完整离线 API/Worker/v4 预演，再执行一次新的有界无故障补跑。新目录 `.local/engineering-calibration/live-03`，最多1 owned部署、3轮、90分钟含准备清理、600 HTTP读取、90 Docker/容器白名单只读、30正常串行物理请求（≤1/s）、2工程事件、2次范围内接线/采集修复。离线修改不占live修复额度。保持固定观察时钟，不重建、不重复已完成动作；安全/秘密/归属问题、无法安全续接、错过时间或额度耗尽即清理停止。Provider/故障/恢复写/正式holdout/晋升均0；旧live-02及原账本只读不变。先独立保存配置，再走同部署提前固定基线、完整Typed Runtime和正常API/Worker；不以健康为目标。完成同报告更新、提交推送PR #104与精确CI，不merge/release。

Most recent authorized follow-up (now ended): 用户于2026-09-28进一步明确授权同一任务的无故障工程联调：最多1次owned部署、3轮、90分钟含准备清理、600 HTTP读取、90 Docker/白名单容器只读、30串行正常物理请求（≤1/s）、一次范围内采集修复重试及最多2个无故障工程诊断事件。Provider、故障、恢复写入、正式验收及晋升均0；旧批次和原库只读不变。新环境、提前固定的同部署基线、完整Typed Runtime和正常API/Worker记录仅写独立工程目录。固定时间观察，不以NO_INCIDENT为目标，不改PromQL/阈值/Core顺序；按归属清理、同报告更新、提交推送PR #104并核对精确CI，不merge/release。

本次无故障工程联调已终止并CLEAN：1部署、1轮尝试（0完整轮）、51 Docker只读、12资源操作命令、0 HTTP读取、10正常物理请求、1修复、0事件，占用603.996秒。首轮环境创建因Runtime snapshot_ref缺pilot/前缀返回422，尚未取得v4凭证/基线/正常诊断。清理后接线修复仅经fixture，不改写失败；新live需另行明确授权，不自动恢复。

The prior bounded engineering calibration was explicitly authorized by the user against commit `7bbf88d`: one owned deployment, three rounds, 90 minutes including cleanup, 500 HTTP reads, 60 Docker/allowlisted in-container reads, 30 serial normal requests, and one bounded collection repair/retry. Provider, faults, recovery writes, formal holdout and promotion remain zero. The authorized calibration is now consumed and CLEAN; further live work requires new explicit authorization. The same [summary report](docs/results/product-v050/engineering-calibration/README.md) records execution; no historical campaign is resumed.


Current Product status: [STATUS](docs/product/STATUS.md). On 2026-09-27 the user
terminated the unfinished automatic v0.5 Goal and designated
[Fresh Start Brief](docs/goals/EcomSRE_Fresh_Start_Brief.md) as the sole current
engineering task. The initial offline-only scope was explicitly extended by the
bounded authorization above. Committing and pushing this task's scoped changes
to Draft PR #104 and checking exact-head CI are authorized; no merge or release. Prior Goals/amendments are historical, not active continuation work.
Consumed/stopped campaigns remain stopped; their one-shot restrictions do not
ban repeatable offline engineering tests. Permanent safety rules remain binding.
The historical Phase 0 state below applies only to that consumed campaign;
it does not prohibit separately authorized Product work. Safety, evidence
and change discipline remain binding.

### Historical Phase 0 state (preserved)

The project is in `PRE_SMOKE_OFFLINE_REPAIR_READY`.

- The 12 decisions `DEC-001` through `DEC-012` are accepted.
- Phase 0 offline implementation and fixture-backed tests exist.
- Live bootstrap produced and verified a local `linux/arm64` candidate image
  lock.
- The single authorized non-canonical smoke
  `f1c9253b03dd4afca4284a89524562fb` terminated `UNSAFE` before readiness or
  measurement because observer-evidence sanitization prevented the authenticated
  post-up authority handoff.
- A post-terminal bounded repair removed that observer leakage, and the same
  authenticated run authority then completed a project-scoped stop. The smoke
  result remains `UNSAFE`; the later stop does not rewrite it.
- The follow-up offline repair closes the six static Must Fix items covering
  post-up failure classification, stop-authority retention, observer projection,
  append-only recovery sealing, image-lock test isolation, and named-volume
  ownership. These changes have offline test evidence only.
- The pre-smoke offline repair v2 closes four additional static Must Fix items:
  frozen-upstream Jaeger/Prometheus config-bind preservation, explicit
  compare-and-swap image-lock rotation, typed pre-mutation versus
  mutation-possible start disposition, and direct stop through
  `FreshStopAuthority`. These paths also have offline test evidence only.
- Required config binds are enforced by fixture-backed resolved mount-plan
  checks, but the repaired Compose plan has not been expanded by a real Docker
  runtime.
- The Compose override changed after the current image lock was created. The
  checked-in lock still binds the pre-repair resolved Compose hash. Before any
  future `up`, a separately authorized live task must re-resolve Compose and
  explicitly rotate and verify a matching candidate lock; hash mismatch without
  rotation authorization must fail closed. No real rotation has been executed.
- The direct-stop minimal authority path has not been exercised against the real
  Docker daemon.
- `OQ-001` is closed by the preserved real preflight fingerprint.
  `OQ-002` through `OQ-004` remain open.
- Phase 0 is incomplete. No second smoke or formal acceptance has been run.
- PR disposition remains `Draft / REVIEW_REQUIRED`.
- Any future bounded smoke remains governed by
  `docs/PHASE_0_BOUNDED_REPAIR_SMOKE_PROMPT.md`.
- The one-smoke allowance has been consumed. Do not run another smoke without
  new explicit authorization.
- The offline-repair scope itself does not authorize commit, push, or PR
  updates; publication requires a separate explicit user request. Publication
  does not authorize a second smoke, deployment, release, formal three-cycle
  acceptance, or Phase 1 work.

Do not extend beyond the bounded-repair prompt. If a Phase 0 behavior is not
authorized by the planning packet and bounded-repair prompt, do not infer it.

## Scope discipline

- Keep phases literal. Do not pull later-phase agents, remediation, Kubernetes,
  AIOpsLab, Feature Service, or Ranking Service into Phase 0.
- Treat the explicit Phase 0 non-goals in
  `docs/PROJECT_CHARTER.md` as binding.
- Do not use the upstream OTel Demo Agent, MCP, or Chatbot as project
  functionality.
- Keep upstream OTel Demo `3.0.0` at commit
  `1755859a9de82c2e5e225be68abc401a5ebf2b4f` read-only.
- Never track upstream `main`, use `latest`, fall back to another release,
  enable amd64 emulation, or patch upstream to conceal a failed baseline.
- Preserve failed-run evidence. Never improve a result by deleting or
  selectively omitting runs.

## Safety

Before any environment command, follow `docs/SAFETY_BOUNDARIES.md`.

- Operate only on resources whose ownership is proven by project labels and
  manifests.
- Fail closed on unknown containers, networks, volumes, ports, files, or
  processes.
- Never stop, delete, or modify an unknown resource.
- Never use global Docker cleanup, arbitrary shell execution, host mutation,
  real credentials, cloud resources, or public write targets.
- `reset` restores scenario state; it does not delete evidence.
- Cleanup is a separate, explicit operation and remains project-scoped.

## Evidence and truth

- Machine-readable evidence is authoritative; UI screenshots are supplementary.
- Observer-visible and evaluator-only artifacts must remain separated.
- Agent-visible names, paths, tags, and URIs must not reveal scenario truth.
- Use UTC timestamps plus monotonic durations.
- Record schema versions, hashes, upstream commit, resolved Compose hash,
  image index digests, and resolved `linux/arm64` digests.
- Use the exact truth markers defined by the relevant acceptance or safety
  document. Do not smooth blocked or failed states into success language.

## Change discipline

- Keep diffs narrow and preserve unrelated user changes.
- Add tests and verification proportional to the active phase.
- Documentation may reference future interfaces, but must not claim that they
  exist.
- Update `docs/DECISIONS.md` when a binding choice changes.
- Update `docs/OPEN_QUESTIONS.md` when an unresolved item is resolved or a new
  stage-gated unknown is discovered.
- Do not duplicate normative rules across documents; link to the owning
  document.

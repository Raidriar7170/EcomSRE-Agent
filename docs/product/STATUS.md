# 当前任务 · 有界工程校准与回放

用户于2026-09-27终止旧未完成Goal的自动推进，以[Fresh Start Brief](../goals/EcomSRE_Fresh_Start_Brief.md)作为唯一当前入口。初始离线交付`7bbf88d`已完成；用户随后明确批准同一汇总中的有界真实工程校准（一部署、三轮、90分钟、500 HTTP读取、60 Docker只读、30正常请求、一次采集修复重试）。历史失败、REVOKED/DRAFT及停止批次不变；Provider、故障、恢复写入、正式holdout与晋升仍为0。工程交付与具体数据限制见[汇总](../results/product-v050/engineering-calibration/README.md)。旧协议的一次性约束继续适用于对应批次，不约束普通离线回归。没有v0.5验收PASS。

本次校准已CLEAN：1部署/3轮，支持11/21→15/21→21/21；Kafka agent2.29.0/SDK1.63.0默认60s与collector0.157.0默认桶取得运行版本对应材料。EventStream错误标签与约600s RPC结束同实例关联，未发现已建立序列漏采或重启。占用32分26.532秒，239 HTTP读取、59 Docker只读、30正常请求、1修复；owned剩余0、非项目未变。21/21只表示返回序列窗口支持，工程默认声明仍禁止进入正式资格/freeze/promotion；未证明完整负例或业务健康。校准授权已消耗，不自动重跑。

当前离线收口新增受控默认映射与显式v4凭证校验链：默认60秒及桶集合来自审阅映射，身份/完整覆盖投影/挂载配置/时窗必须匹配；旧工程声明仍拒绝。保留材料版本匹配，但缺完整JVM/Collector执行投影及jar实例时点绑定，不能签发新正式凭证。EventStream进入现行错误率、延迟和请求支持查询；第三轮fraud/payment错误及>15秒桶增量均来自该RPC。指标纯回放缺绑定基线和Runtime覆盖，健康资格NOT_ISSUED；没有将21/21升级为健康验收。此轮仅离线，无新增live额度或注册变化。

## 历史最新真实执行（不是当前待办）


最新真实执行：[OTLP v2替代批次](../results/product-v050/final-learning-closure/otlp-validation-20260927-result.md)。owned部署与固定基线成功；一次54请求的v2准备取得原始样本，但9/21业务查询窗口覆盖不完整，在N4预留/故障之前停止。三个holdout资格、Shadow、晋升、正常复用均NOT_RUN。新版本DRAFT、原规则REVOKED；22容器/1网络/5卷清理完毕，非项目未变。新增Provider/费用/live槽均0，累计14/18、闭环9/13；旧批次三个未用槽已退役，新批次亦停止。Level A未完成，Level B未恢复，不自动重试。

## 前序离线修复与停止批次（按当时计数保留）


最新离线修复：[OTLP 摄入协议与运行前检查](../results/product-v050/ingestion-protocol-repair/README.md)。未来显式 v2 绑定真实 push 配置、服务/selector 原始样本时间和准备阶段凭证，并在资格/freeze/promotion重验；fixture通过不代表真实准备通过。未运行Docker/Provider/live，原14/17账本、固定规则和停止状态不变，不自动恢复08批次。

最新 [固定规则独立验证](../results/product-v050/final-learning-closure/fixed-validation-20260927-result.md)：执行版本 b213227 完整 CI 通过；资源连续性与 owned 部署绑定通过。唯一新目标事件返回 OPEN_WORLD，但新增 scrape-target 检查与实际 OTLP push 摄入路径不兼容，批次按冻结协议停止。空 targets 不证明指标陈旧；新健康/Core/复发未启动，Shadow/晋升未运行，新注册 DRAFT、旧注册 REVOKED。owned 22容器/1网络/5卷 CLEAN，非项目未变。新增0请求/0费用/1live；累计60请求/USD1.067489/14 of 17 live，闭环9 of 12 live，本批次1 of 4。剩余名额不用于停止后的自动重试。终态仍 NO_VALIDATED_LLM_KNOWLEDGE，Level A未完成、Level B未恢复。

## 2026-09-27 此前开发与离线检查点

[最新结果](../results/product-v050/final-learning-closure/README.md)：引用绑定与来源选择已修复，真实模型一次提议通过完整 Level A 开发并锁定。机械 Shadow gate=true，但 N5 实际 OPEN_WORLD，健康资格未确立；N7 返回 CORE_KNOWN，未命中 learned registration，调用增量0不能算复用。最终规则已撤销，owned清理完成、非项目未变。终态仍 NO_VALIDATED_LLM_KNOWLEDGE；Level A完整闭环未通过，Level B未恢复。累计60请求/USD1.067489/13live，本轮3请求/USD0.148988/8live；live上限已用满。


最新离线修复：[控制资格与 N7 追溯](../results/product-v050/control-qualification-repair/README.md)。新协议阻止异常健康控制晋升；N7 冻结规则纯回放 TRUE，但正常 Core 分支未执行 Extension matcher。原库只读未变、注册仍 REVOKED，13/13 live 不变。

## 2026-09-26 历史检查点


最新 [控制补采与执行结果](../results/product-v050/final-learning-closure/execution-resume-20260926.md)：D_CORE_FIX_01 经真实配置读回和正常 API/Worker 得到 CORE_KNOWN，并恢复健康；目标 Logs/Traces/Changes 缺口保留。两次真实模型提议均只选择 Metrics 条件，被 TWO_SOURCES_REQUIRED 拒绝，已按 Goal C3 连续同错规则停止。本轮无获准候选、选择锁或 N4–N7；Level A 开发失败，Level B 未恢复。累计59请求、USD 1.015867、9 live；本轮2请求、USD 0.097366、2语义、4 live。owned 05/06 精确 CLEAN，非项目未变。当前 NO_VALIDATED_LLM_KNOWLEDGE，不是当前 Docker 阻塞，不是预算耗尽。

## 历史检查点（以下计数按当时状态保留）

最新 [Final learning closure](../results/product-v050/final-learning-closure/continuation-20260925.md)：实际 Hermes runner 停止后连续性通过，owned 04 successor 与基线 READY。N2 返回 NO_INCIDENT；N3 返回 INSUFFICIENT_EVIDENCE，无法满足采集前固定的 Core/Extension Known 控制门槛；保留证据不能离线重建缺失的 trace 因果身份。不修改历史诊断、不降低门槛、不重采 N3、不挪用 N4–N7。04 已精确清理且非项目资源未变。累计 57 请求、USD 0.918501、8 episode；本闭环新增 Provider/语义尝试 0、episode 3。Level A 未完成；Level B 亦未完成，N1 必需窗口缺口仍保留。当前为 NO_VALIDATED_LLM_KNOWLEDGE，具体层级为 DEVELOPMENT_DATA_BLOCKED；非模型失败。

历史 [可行性与受约束提议轮](../results/product-v050/knowledge-feasibility/README.md) 复用原 5 个已见事件：新增 3 次请求，3 个 schema-valid 草稿，1 个准入候选，1 次实际开发求值；原 Development 仅 1/2，未冻结、独立验证、晋升或复用。累计 57 次请求、USD 0.918501 承诺，终态仍为 `NO_VALIDATED_LLM_KNOWLEDGE`。本轮没有新增 live episode 或 Product 恢复写入；旧 live-02 clean=true 与 live-01 BLOCKED_SAFETY / clean=false 保留。

**收尾完成；v0.4 的真实 Payment 恢复已合并。单租户本地 Product 原型。**

## Diagnosis authority

Diagnosis `action_authority = NONE`。证据、SQLite、CAS 与治理记录的持久化不代表有权修改被诊断服务。未知机制进入人引导知识演化，不自动获得执行权限。

## Remediation authority

默认关闭。唯一映射为 `CORE_KNOWN / payment / CONFIGURATION / CONFIGURATION_ERROR` → `ROLLBACK_CONFIGURATION` → `RESTORE_BASELINE_CONFIGURATION`，maximum forward steps = 1。Candidate、独立批准、fresh Current State、单次授权、WriteIntent、隔离执行与双窗口验证共同约束动作。见 [REMEDIATION](REMEDIATION.md)。

## 已验证结果

| 版本 | 结果与范围 | 来源 |
| --- | --- | --- |
| v0.2.4 | 30/30 checkout，五类证据，`NO_INCIDENT`，能力限制 0 | [JSON](../results/product-v024-nofault-acceptance-final.json) |
| v0.3 | 三个 Open-World 窗口；一个 Fault Family；Shadow recall 1.0 / FPR 0.0；H1 = `EXTENSION_KNOWN` | [摘要](../analysis/product-v030-family-and-rule-summary.json) |
| v0.4 | 194 次健康 Payment 请求，最后 30/30 fault probes 如预期失败；一次 gateway restore；两个窗口各 39 请求 / 0 错误；`RECOVERED` | [JSON](../results/product-v040-minimal-payment/live-result.json) |
| v0.4.1 | 5/5 安全案例通过；S0–S2 写入 0，S3/S4 各 1；无未授权或重复写入；每例 CLEAN | [结果包](../results/product-v041-live-safety/README.md) |

v0.3 Shadow 为 3 个正例、10 个负向/反事实/失败用例，`OTHER_EXTENSION` 未观测。v0.4 Minimal 健康 Diagnosis 是 `INSUFFICIENT_EVIDENCE`：Logs、Runtime、Traces 未提供给健康诊断，不改写为 `NO_INCIDENT`。v0.4 cleanup 移除 10 容器 / 3 网络 / 2 卷，剩余 0/0/0，非 owned 资源未变。上述 Product 实测 Provider calls = 0。

[PR #102 完成记录](https://github.com/Raidriar7170/EcomSRE-Agent/pull/102#issuecomment-5599847699)确认 v0.4 合并及 6520 tests passed / 21 skipped / mypy 705 files。这些计数仅属于 PR #102，不能与其他版本相加，也不是 v0.4.1 的测试结果。

## Current limitations

默认 Product 仍只读；一个本地恢复 Runbook；单租户 SQLite；无生产 self-healing、完整 28 服务验收、跨环境泛化、exactly-once、HA、Kubernetes、长期 SLO。见 [LIMITATIONS](LIMITATIONS.md)。

## Public quickstart

[QUICKSTART](QUICKSTART.md)提供结果导览、知识演化 Fixture、恢复 Fixture 与已有 Live Evidence Verifier。公开 verifier 不重跑实验。历史失败证据和旧 v03 手册保留在仓库中。

## v0.4.1 实测安全结果

| Case | 实测终态 | Product 外部写入 | Cleanup |
| --- | --- | ---: | --- |
| S0 Healthy | NO_CANDIDATE；健康 Diagnosis INSUFFICIENT_EVIDENCE | 0 | CLEAN |
| S1 Revoked | APPROVAL_REVOKED / NO_WRITE | 0 | CLEAN |
| S2 Drift | CONFIGURATION_DRIFT_NOT_VISIBLE / NO_WRITE | 0 | CLEAN |
| S3 Replay | RECOVERED；相同键返回原对象，第二次 run_one 拒绝 | 1 | CLEAN |
| S4 Evidence failure | APPLIED；VERIFICATION_FAILED / ESCALATE_HUMAN | 1 | CLEAN |

单次观察的完整时延、时钟语义和缺失项见 [timing 说明](../results/product-v041-live-safety/README.md#observed-timing)。

## v0.5 历史开发状态

[v0.5 Goal](../goals/EcomSRE_v0.5_Codex_Goal.md) 以 `ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE` 有限终态收口，不是完整验收 PASS。5 个独立事件均已恢复实验健康状态；只有一例数值假说获得暂定支持，不代表因果定位。原 live 轮累计 51 次请求、USD 0.695520 承诺上界；先前接口修复轮后为 54 次、USD 0.839581，当前可行性轮后为 57 次、USD 0.918501；旧调用和失败未改写。进度见 [记录](../analysis/product-v050-progress.md)。

### Historical control repair precheck (2026-09-26 05:29 UTC)

The activated narrow amendment has 66 passing focused checks and independent review Must Fix 0. It adds an explicit 13/8 live ceiling child contract, preserves N3 as insufficient, binds actual configuration audit and the same N6 collector, and retains original/consumed/new deployment identities. The actual child contract is not installed: fresh read-only continuity found a new non-owned container and bridge attachment. `D_CORE_FIX_01` and N4–N7 are unconsumed; no new Provider request or live start. Current terminal: `ECOMSRE_PRODUCT_V050_BLOCKED_SAFETY`; Level A incomplete, Level B not achieved. Accounting: 57 requests / USD 0.918501 committed / 8 starts. See [control repair result](../results/product-v050/final-learning-closure/control-repair-result.json). No merge, release or Product recovery write.

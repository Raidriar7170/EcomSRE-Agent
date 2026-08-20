# DTA v2.2 PR-D Human Brief

状态：`BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE`

Draft PR #60 的唯一 v3 campaign 已从冻结实现 head
`f9625cd45a1ed5a8ae38b56aac9e08dc99972902`、tree
`fac6a7f2db96e3705a5fbd5e7973fe20d25161c2` 执行。replicate A 与 B 都因
Provider transport abort 不具备 gate eligibility，两个 replicate 都没有通过独立
门槛。因此 PR-D 不具备 merge readiness，不能 mint
`DTA_V22_PR_D_CONTROLLER_READY`，不得运行第三次 replicate，也不得开始 PR-E。

DEC-057 与 preregistration 冻结的新 v3 协议为：每个 replicate 保留 48 个普通
transition，Flat/Planner 各 24；另有 stale-action 与 invalid-ref 在两臂上的 4 个
correction envelope。correction 不进入普通 first-pass 分母。每次独立要求普通
至少 46/48、每臂至少 23/24、correction 4/4 且每臂 2/2、final 至少 51/52、
invalid dispatch 为 0。

campaign 实际完成一个 output-mode probe；A 发出 18 次、B 发出 3 次 Provider
transition call，加上 probe 共 22 次，accounting 精确且 undeclared call 为 0。
即使 A 已阻塞，固定 60 秒 cooldown 后仍执行了 B。HTTP auto-retry 为 0；没有
replacement 或第三次 replicate。

A 在 transport abort 前普通 accepted 为 6/48、correction 为 0/4、final 为
6/52。failure taxonomy 为 parse-shape 6、semantic mismatch 5、transport abort
35，其余 0；token 为 248347。B 对应为 1/48、0/4、1/52；taxonomy 为
parse-shape 1、transport abort 50，其余 0；token 为 27790。两者 invalid
dispatch 都为 0，campaign aggregate semantic SHA-256 为
`b23184d23ad5d6fc801e85efca268d5c7e7ad951ee004b8221fe2a5889211170`。

每个正面或负面结果都按 private create-once、校验、bounded public summary
create-once、再校验的顺序持久化。A、B 完成后 campaign aggregate 也必须先
持久化并校验，之后才返回 PASS 或 blocker。公开 summary 不含 raw Provider
content、credential、private path 或 chain of thought。

Attempts 1–5 的原始字节由 preregistration 与 verifier 固定：没有编辑、移动、
重标或重算，Attempt 1 仍因错误 private location 无效，也不计入这两个新
replicate。v3 不修改 controller Prompt、`ControllerDecisionV22` schema、
controller runtime、Provider-visible payload、Action Catalog、Memory、
predicates、Diagnosis admission 或冻结模型。

两个 typed partial receipt、两个 bounded public summary 与 campaign aggregate
都在 blocker 返回前 create-once 持久化并校验。公开结果不含 raw Provider
content 或 private path；private/public binding 均标记为 verified。两个 partial
receipt 中的实现、mode、probe、schema、Prompt 与四个 Controller identity 值相同，
但 aggregate 按冻结规则只对两个 complete report 设置完整 equality，因此该字段为
false，不可解释成 PASS。

Attempts 1–5 的字节仍保持不变，Attempt 1 没有被计为 replicate 或 PASS。本次
campaign 的 Agent read/write、Docker、Runbook、held-out、scenario 与 fault 活动
全部为 0。PR 保持 Draft，不合并，也不启动 PR-E。

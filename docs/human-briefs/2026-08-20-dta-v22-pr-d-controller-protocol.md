# DTA v2.2 PR-D Human Brief

状态：`DTA_V22_PR_D_PROVIDER_PROTOCOL_V3_EXECUTION_READY`

这是 Draft PR #60 的 Phase-I 离线执行就绪状态，不是 Provider gate PASS，也不
具备 merge readiness。只有固定 campaign 的 A、B 两个 replicate 各自通过全部
冻结门槛，才能 mint `DTA_V22_PR_D_CONTROLLER_READY`；否则必须保存结果并停在
`BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE`。PR-E 目前不可开始。

DEC-057 与 preregistration 冻结的新 v3 协议为：每个 replicate 保留 48 个普通
transition，Flat/Planner 各 24；另有 stale-action 与 invalid-ref 在两臂上的 4 个
correction envelope。correction 不进入普通 first-pass 分母。每次独立要求普通
至少 46/48、每臂至少 23/24、correction 4/4 且每臂 2/2、final 至少 51/52、
invalid dispatch 为 0。

campaign 只允许一个 output-mode probe、replicate A、固定 60 秒 cooldown、
replicate B；request-start 最小间隔为 4.0 秒，HTTP auto-retry 为 0。即使 A
语义失败仍必须执行 B；transport abort 也要生成 typed partial receipt。不存在
replacement 或第三次 replicate。

每个正面或负面结果都按 private create-once、校验、bounded public summary
create-once、再校验的顺序持久化。A、B 完成后 campaign aggregate 也必须先
持久化并校验，之后才返回 PASS 或 blocker。公开 summary 不含 raw Provider
content、credential、private path 或 chain of thought。

Attempts 1–5 的原始字节由 preregistration 与 verifier 固定：没有编辑、移动、
重标或重算，Attempt 1 仍因错误 private location 无效，也不计入这两个新
replicate。v3 不修改 controller Prompt、`ControllerDecisionV22` schema、
controller runtime、Provider-visible payload、Action Catalog、Memory、
predicates、Diagnosis admission 或冻结模型。

此阶段没有运行新的 Provider campaign，也没有 Docker、scenario、fault、Agent
evidence dispatch、Agent write、Runbook 或 held-out 活动。Phase-I 精确实现
head/tree 将在离线门禁、exact-head CI 与独立只读复核全部通过后冻结，然后才
执行唯一一次 preregistered campaign。

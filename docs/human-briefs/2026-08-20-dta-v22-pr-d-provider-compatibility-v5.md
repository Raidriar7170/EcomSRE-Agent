# DTA v2.2 PR-D Provider Compatibility v5 — Human Brief

## 结论

`BLOCKED_DTA_V22_PROVIDER_PROTOCOL_GATE`

PR #60 保持 Draft，`merge_ready=false`，不得开始 PR-E，也不得创建另一轮 Provider campaign。

## 冻结实现与前置证据

- Decision Record：`DEC-059`
- Goal Amendment：`dta-v22-pr-d-provider-compatibility-v5-amendment-v1`
- Commit A：`da25e24437e8ad6cb356f539fb501d07c0d86d9a`
- tree：`8f17f11cd2d0350c6ab1526a3f614d3a2a543315`
- manifest：`ab0a13648e5c91d1f02ce6d6ec72319ae9ed73eebcb2a11251653a5f4ba8e28d`
- exact-head Agent mainline CI：run `32361075725`，PASS
- exact-head RCAEval CI：run `32361075781`，PASS
- 独立 pre-execution review：Must Fix `0`，Claim Accuracy `PASS`

## 唯一 v5 campaign 的实际结果

- Provider calls：`1`
- attempted mode：`LOCAL_FAIL_CLOSED_JSON`
- probe：`supported=false`
- failure class：`PROVIDER_RESPONSE_PROTOCOL_FAILURE`
- safe failure code：`PROBE_LOCAL_VALIDATION_ABORT`
- selected mode：`null`
- Replicate A：未开始
- Replicate B：未开始
- completed replicates：`0`
- HTTP auto-retry：`0`
- semantic retry：`0`
- replacement campaign / replicate：`0`
- campaign SHA-256：`9b1d36604d89b3ffc0e433b23c0f98c047f8d0e00aedae13e70b8d2087687cb3`

因此本次结果不能证明 Flat Canonical 或 Planner-Lite 的语义能力失败；它只证明 v5 Provider compatibility probe 未建立可继续执行的协议边界。

## 持久化与 verifier 事实

Probe 公私有证据在返回终态前 create-once 落盘。冻结 runner 随后的 persisted-stage 校验发现内存 tuple 与 JSON list 的 `attempted_modes` 比较不一致并 fail-closed，Replicate A 因而未被授权。

为了保全同一 campaign 的负结果，使用冻结 `_campaign` / `_persist_campaign` 逻辑补写了 `0` replicate、`1` Provider call 的 BLOCKED aggregate，没有再次调用 Provider。Aggregate create-once 落盘后，冻结 post verifier 进一步显示 `probe_binding` 的声明字段集合不包含 `implementation_commit` / `implementation_tree`，但其 exact projection 又要求这两个字段，因而 post-execution verifier 无法通过。Provider 开始后禁止修改源码/Prompt/schema，所以该缺口未被修补，也没有重试。

## 安全边界

- Attempts 1–5、v3、v4 公私有证据保持原字节不变。
- v3/v4 未重跑；v5 未创建第二个 campaign。
- Agent evidence dispatch：`0`
- Agent write：`0`
- Runbook execution：`0`
- Docker / scenario / fault / held-out：`0`
- 未发布 raw Provider content、凭据、base URL 或私有路径内容。

## 后续状态

按 Amendment 3，失败后停止本 Goal chain。PR #60 保持 Draft；不得开始 PR-E，也不得通过新的 Provider amendment 或 replacement campaign 重试。

# EcomSRE v0.5 — PR #104 Provider 404 定向解阻

> 2026-09-17 · 原 v0.5 Goal 的有限续跑任务，不是 v0.6，也不是重建 Provider 框架。
> 核对提交：`fe57dee4895d966e6d9c270c4e6ec6cc00a57d72`。
> 仓库：`Raidriar7170/EcomSRE-Agent`；继续同一个 Draft PR #104。
> 原 Goal、历史保护、凭据边界与累计预算继续有效；不自动合并、发布或扩大恢复写权限。

## 1. 目标与已知事实

这次只回答并解决：为什么同一项目配置能够取得模型元数据，却不能成功生成？
不要把 HTTP 404 直接解释为模型不存在、Provider 整体宕机、账号没钱或代码无问题。

截至核对提交，已提交结果记录：[R1][R2]

- 模型：`gpt-5.4-mini-2026-03-17`，是实际显式配置，不是完整 GPT-5.4。
- 4 次累计请求：首次生成 transport failure；Chat Completions 生成 HTTP 404；精确模型元数据 GET HTTP 200；Responses 生成 HTTP 404。
- 尚无成功生成、真实模型选读、知识提议或学习后复用；live episode 为 0。
- 已保留预算承诺 USD 0.095449；实际账单费用未知，不能将该预留称为实际消费。
- 当时剩余 196 请求、USD 19.904551 和 12 live episode；执行时重新读取原账本。

当前 smoke 代码已经要求有效 base URL 等于 `https://api.openai.com/v1`；Provider 随 API style 追加 `/chat/completions` 或 `/responses`。[R3][R4]
因此先核对实际 dispatch 与记录，不要无证据地反复改 `/v1`、猜网关地址或重新申请密钥。

官方模型页在本次查询时列出了该快照，支持 function calling、structured outputs 和两种生成接口。[O1]
这证明公开接口契约，不保证当前项目凭据有相应生成访问权。模型 GET 是元数据读取，不是生成验收。[O2][O3]

## 2. 权限与预算

本文件被保存不等于允许执行外部请求。由用户在原 Codex Goal 会话明确激活后执行。

- 只读取当前项目进程环境和已明确的 `~/.config/ecomsre/provider.env`。
- 不回显密钥、完整 env、Authorization、Cookie 或代理凭据；不读取其他应用的凭据。
- 不修改 Codex 全局设置、系统 DNS、代理、证书或防火墙；不关闭 TLS 验证，不跨主机转发密钥。
- 不自动改变项目、组织、账号、模型或模型快照；不自动放宽 API key 权限。
- 不启动 Docker，不重跑历史实验，不改 R1–R4 或知识求值逻辑来“修复 404”。
- 入口定位阶段（第 3–5 节）新增请求最多 6 次，额外预算承诺最多 USD 1；两者同时受原账本剩余额度限制。这是原总额内的子上限，不是新增额度。
- 关闭隐式重试；不同请求分别记账。没有新的诊断依据时，不重复完全相同的失败请求。
- 超时/失败且 usage 未知时保留原预留；不能为解除预算阻塞清空账本。
- 后续正常 Product smoke 和原 Goal 验收继续使用同一个累计账本及原上限。

## 3. 先补错误可观测性，再发请求

先读现有本地私有账本的安全字段和已提交结果。现有错误处理保留 HTTP 状态及可解析的 error.code/type，却没有充分保留可用于定位的 message、Content-Type 与 request ID。[R4]
旧响应若没有保存，明确标为不可恢复；不能重新请求后冒充旧请求的原始信息。

为下一次请求增加最小、脱敏的错误记录。优先在 Product 专用边界或诊断脚本实现，不原地破坏冻结历史 transport：

```text
attempt_id / ledger_key
UTC 时间、单调时延
method
官方目标 host 与 path（内部 host 只在私有记录中）
requested_model / api_style
payload_shape（键名与长度，不输出私密内容）
http_status
response_content_type
request_id（允许的 x-request-id；没有则 null）
provider_error_code / provider_error_type
sanitized_error_message（最多 1024 字符）
response_body_kind: JSON_ERROR / HTML / TEXT / EMPTY / OTHER
body_truncated
proxy_environment_present（布尔，不显示地址或凭据）
transport_exception_type（无 URL/key 的异常链类型）
usage_status / reserved_cost / accounted_cost
```

错误正文仅做有界读取与脱敏。若无法可靠脱敏，只保存安全类别和原因，不公开正文；不保存任意响应头全集。
异常处理必须确保 error body 只读取一次；日志投影失败不能丢失原 HTTP 状态或覆盖原错误。

至少补以下离线回归：JSON 错误；HTML 404；无 error 对象；空/过大正文；缺 request ID；正文包含测试密钥；脱敏失败；已发生请求的预算仍保留。
header 或正文只能提供线索，不能仅凭 Server 头或 request ID 存在/缺失认定响应来源。

## 4. 不发请求即可完成的核对

1. 确认当前工作目录与原 `.local/product-v050` 账本的真实位置；更换 worktree 不得产生新的独立预算。
2. 核对 dotenv 与进程环境的优先级，API、Worker、最小诊断脚本使用相同配置。只输出一致/不一致，不输出或散列真实 key。
3. 检查实际拼接后的 host/path 是否恰为已授权的官方端点，排除重复 `/v1`、重复 `/responses`、错误方法及重定向。
4. 检查三个原 smoke attempt marker 是否已消耗。不得删除 marker、覆盖失败 session 或把 `--attempt 1` 当作全新运行。
5. 查看精确模型元数据 GET 和失败 POST 是否来自相同环境、相同 base、相同项目配置；元数据成功不等于生成权限成立。
6. 记录 HTTP(S)_PROXY/ALL_PROXY 等相关环境是否存在即可；不要自动绕过企业代理或更改网络。

只有确定需要修改哪个实现点时才修改；不为排查搭建新网关、重写所有 transport 或迁移全部 SDK。

## 5. 从最小生成逐层恢复，不一开始调用完整 Investigator

### P1：最小文本生成

使用当前同一模型、同一密钥、同一官方目标，通过一个不创建 incident/job 的小诊断入口发送请求；它仍必须使用原 Provider 账本 reserve/settle。

优先采用官方 Responses 最小请求：仅请求“Reply exactly OK.”，设置 `store=false` 和有限输出上限（例如 512 tokens）。
不带 SRE system prompt、复杂工具 schema、retrieval、hosted tools 或推理高档位。
如显式设置 reasoning，仅使用该模型官方支持的值；诊断用 `none`，与业务运行配置分开记录。[O1]

检查完整成功状态和可见文本；HTTP 200 但 incomplete/refusal/空结果不是同一种成功。
成功只记为最小生成连通，不记为 SRE 能力验收。

失败时按脱敏的真实响应分类。仅当有明确区分价值时，在同一配置下发一次最小 Chat Completions 请求；不是无限切换接口。
若最小请求仍 404，无新增证据就停止生成测试，转第 7 节，不继续堆 schema 修复。

### P2：一个最小 function call

P1 成功后，发送一个最小 `ack` 函数，参数只有一个布尔字段，检查函数名和参数解析。
使用对应 API 的正确 function schema。先验证最小 tool contract，不直接提交整个 InvestigationDecision。
通过后逐步增加原任务使用的参数；每步只改变一个重要因素，分别记录差异。
工具测试的输出不是事故诊断，也不创建假的学习知识。

### P3：回到真实 Product smoke

P1/P2 通过后才恢复原 Goal 的 API/Worker smoke：

```text
真实模型结构化决策
→ 模型从多个合法候选中选一个 read
→ Runtime 执行
→ 观测返回模型
→ 模型输出后续判断
→ 结构化知识提议（可以被合理拒绝）
```

已完成/失败的旧 session 不重写。创建一个追加式新 attempt，并继续同一账本；不得复用已消耗 marker。
可在现有脚本中加入最小的显式新 attempt 支持，不给运行时添加无限重试。
旧 fixture 可以用作协议回放，但不计为新的独立事故，不据此晋升或宣称学习有效。

入口子上限只约束 P1/P2 的定位请求；P3 及后续运行受原 Goal 的剩余总预算约束，不另起账本。

## 6. 模型选择不是此次解阻的替代品

保持 `gpt-5.4-mini-2026-03-17`。不因为两次 404 就自动改为完整 5.4、5.6、Codex 登录模型或另一个 Provider。

如果真实错误明确指向模型访问或快照映射问题，报告证据，并提出一个具体模型/别名变更供用户确认。即使别名看似同系列，也不冒称与历史快照完全相同。
若用户明确授权改变运行模型：保留旧记录，追加新配置与价格身份，在冻结任何新评估之前重新 smoke；不混合统计。

## 7. 失败必须给出具体解阻条件

| 观测 | 可采取的下一步 | 不允许的结论 |
|---|---|---|
| 结构化错误指向 model/access | 核对当前项目的模型访问及 key 对生成端点的权限；需用户控制台操作时精确说明 | 只凭 404 宣布模型下线 |
| 最小文本成功、加工具失败 | 修 API 风格、tool schema 或参数兼容性，补聚焦测试 | 继续说整个生成接口不可用 |
| 小请求成功、完整 Product 失败 | 检查真实序列化、上下文、输出限额和解析，不改故障真值 | 用脚本答案代替模型输出 |
| HTML/plain 404 或无标准错误 | 核对路径、代理/中间层与响应线索；保留不确定性 | 没有 request ID 就断言不是官方 |
| 凭据/项目端点权限明确不足 | 让用户或管理员只调整所需的生成权限；不索取 key | 自动把所有权限改为 All |
| 仍无法定位 | 返回请求时间、端点、模型、脱敏错误、request ID 和已排除项，给支持渠道使用 | 只写“等接口恢复” |

OpenAI API key 可配置为 Restricted/Read Only 等，项目也有模型使用限制；元数据读取与生成是不同能力。[O2][O3]
这些是需要核查的维度，不代表当前 404 已证实属于权限问题。

## 8. 交付与停止

复用原进度文档和同一个 Draft PR，仅追加小的 `provider-unblock` 结果。保留原 0-call 和 continuation-01 历史。

本轮交付必须明确：

```text
最小文本生成：PASS / FAIL / NOT_ATTEMPTED
最小 function call：PASS / FAIL / NOT_ATTEMPTED
Product API/Worker tool roundtrip：PASS / FAIL / NOT_ATTEMPTED
已确认根因，或尚未区分的原因
本轮新增请求 / 累计请求 / 预算承诺 / 实际费用已知性
用户必须做的唯一具体操作（无则 NONE）
是否已继续原 v0.5 Goal
```

不将上述诊断里程碑伪装成 `ECOMSRE_PRODUCT_V050_ACCEPTANCE_PASS`。
入口修好后在原权限与预算内继续调查—学习—独立验证—复用，不停在“收到 OK”。
若外部访问确实阻塞，保留原 Goal 的诚实限制/阻塞终态，附可执行解阻条件。

只改 Provider/诊断脚本时先跑聚焦测试；有准备交付的稳定源码后，再完成原 Goal 要求的全量回归/CI。
全量测试期间固定 worktree 与 HEAD，不并行提交导致身份/差异校验失效。
不为了又一次“工程完成”重复扩张治理文档。

## 9. 核对来源

- [R1] PR #104：`https://github.com/Raidriar7170/EcomSRE-Agent/pull/104`
- [R2] 请求记录：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/fe57dee4895d966e6d9c270c4e6ec6cc00a57d72/docs/results/product-v050/continuation-01/provider-attempts.json`
- [R3] Smoke：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/fe57dee4895d966e6d9c270c4e6ec6cc00a57d72/scripts/product_v050/provider_smoke.py`
- [R4] Provider：`https://github.com/Raidriar7170/EcomSRE-Agent/blob/fe57dee4895d966e6d9c270c4e6ec6cc00a57d72/src/ecomsre/product/investigation/provider.py`
- [O1] 官方模型：`https://developers.openai.com/api/docs/models/gpt-5.4-mini`
- [O2] Key 权限：`https://help.openai.com/en/articles/8867743-assign-api-key-permissions`
- [O3] 项目权限与模型使用：`https://help.openai.com/en/articles/9186755-managing-projects-in-the-api-platform`
- [O4] API 调试与请求标识：`https://developers.openai.com/api/reference/overview`

以上阶段和子预算是本次建议；尚未实际执行新的 Provider 请求，也未修改远端仓库。

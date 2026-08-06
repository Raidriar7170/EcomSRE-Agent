# RCAEval RE2 外部验证 v1 — Human Brief

## 当前边界

当前只进入 Work Package B1：审查并冻结开发可见的 RE2-OB / RE2-SS
适配、三架构 wiring、评分与统计协议以及 holdout 控制面，不进入 B2–B7。
RE2-TT 未下载、未列目录、未读取、未 seal、未运行。

## 已建立的能力

- Single / Fixed / Dynamic 在相同 RCAEval 案例上使用同一模型快照、Prompt
  family、三类只读工具、预处理、预算、超时和失败 denominator。
- Fixed 实际调用三个 source-isolated Specialist 后再调用 Judge；Dynamic
  先实际调用 Metrics Specialist，再由实际 Commander 调用选择 Logs 和/或
  Traces，最后调用 Judge；SS 的 Traces 返回 typed
  `SOURCE_UNAVAILABLE`。
- `model_calls` 只记录真实 Provider 尝试：Single=1、Fixed=4、Dynamic=4 或 5；
  每次调用后累计真实 token usage，v1 不启用 targeted refinement。
- 每个 arm 有独立 EvidenceStore、typed Specialist Assessment、Provider 对象、attempt marker
  和 terminal record，不共享 LLM 输出缓存。
- 所有 schedule record 在语义尝试前 create-once 落盘；崩溃留下的孤立
  attempt 只会被终结为失败，不会重发模型请求。
- 每次开发执行在首个模型调用前写入 run lock，绑定 schedule、base commit、
  config 与 source-tree hash；旧 journal 不能在代码变化后重新包装成冻结证据。
- 统计主比较固定为 Dynamic - Single Root Service AC@1；cost-quality 也使用
  Dynamic - Single，条件为准确率 CI 下界不低于 -5 个百分点、工具调用降幅
  点估计至少 20%、且降幅 CI 下界大于 0。
- BARO 明确不属于 `rcaeval-re2-v1` 主实验，也不参与 primary inference；
  后续若补充，只能使用独立冻结协议并作为 separate secondary analysis 报告。
- B1 freeze record 在 implementation commit 之后于 Git snapshot 外部创建，
  逐文件记录完整 SHA-256，并绑定完整 tracked-diff 与 scoped-closure SHA-256，
  只接受 machine-readable 的精确 54-path allowlist、全仓干净 worktree 与
  byte-identical committed blobs，因此不把包含自身 hash 的文件塞回 commit
  制造不可验证的自引用。
- 仓库为 non-package `uv` 项目；所有 RCAEval CLI 的权威前缀固定为
  `PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.rcaeval.<command>`，
  不支持省略 `PYTHONPATH=src:.` 的隐式运行方式。

## 开发证据（不可作为外部结论）

- OB / SS 数据审计：各 90 cases，均为 5 services × 6 faults × 3 instances；
  OB 有 traces，SS 无 traces。
- 当前 source-bound 启发式 wiring smoke：OB 2 + SS 2 cases，共 12/12
  terminal completed；Single / Fixed / Dynamic 分别记录 1 / 4 / 5 次模型操作。
- 当前 source-bound 真实 Provider pilot：OB 1 case + SS 1 case，共 6/6
  terminal completed；Single=1、Fixed=4、Dynamic=4 或 5 次真实 Provider
  calls，所有 terminal 均有累计 token usage。该结果只证明 Provider、Prompt、
  schema、evidence alias、Commander/Specialist/Judge 和预算 wiring 可运行。
- 前一版的全 strata 60-case 启发式 smoke 与旧 real pilot 均保留为历史开发
  journal，但因 source hash 已变化，不进入当前 protocol freeze。
- 先前失败的开发 journals 均保留，未覆盖或挑选性删除。

## 人工下一步

B1 完成后状态只能是 `HOLDOUT_EXECUTION_AUTHORIZATION_REQUIRED`。只有收到
新的明确授权才能进入 B2 或在 evaluator-only 环境接触 RE2-TT；本次 commit、
push 和 Draft PR 授权不包含 holdout 下载、seal、执行、解盲、merge、release
或 tag。任何最终跨系统性能结论必须来自 270/270 locked terminal records 后
的一次性解盲评分。

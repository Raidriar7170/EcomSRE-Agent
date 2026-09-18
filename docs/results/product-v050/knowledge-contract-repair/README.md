# v0.5 knowledge contract repair

**ECOMSRE_PRODUCT_V050_NO_VALIDATED_LLM_KNOWLEDGE**. This continuation stopped
at evidence admission, with one protocol truncation. **No real development
candidate evaluation occurred**: neither completed draft passed admission.
No holdout, promotion or new-event reuse was attempted; this is not a learning
success or full v0.5 acceptance. Same Draft PR #104, no merge/release.

## What changed

The model proposes a draft containing target support, target counterevidence and
comparison context separately. Runtime resolves snapshot/event/service/window
bound aliases, mechanically compiles the chosen resource dependency and validates
units. The model still chooses members, predicates, operator, comparator and
threshold; Runtime does not invent or repair these choices. Comparison context
is audit-only model inference, never a target predicate, source-diversity credit
or recovery permission. Cross-service trigger conditions remain unsupported.

The versioned draft uses strict function schema objects with all properties
required, nullable optionals and no additional properties. Semantic checks remain
local; format success cannot bypass them. This follows the [official function
calling contract](https://developers.openai.com/api/docs/guides/function-calling).
A bounded safe parameter diagnostic retains field paths, numeric types, safe
enums and known catalog entries. Unknown text is not retained there. No hidden
reasoning or raw telemetry is published. Ordinal 50's old expression operands
remain unrecoverable; no unit/window explanation is retroactively invented.

## Actual new requests

| Cumulative request | Raw attempt label | Schema | Admission / precise failure |
| --- | --- | --- | --- |
| 52 | revision-0 / repair-0 | valid | Unknown aliases: target support/counterevidence contained prose; no canonical candidate |
| 53 | revision-1 / repair-0 | truncated | No complete draft; no admission or development |
| 54 | revision-1 / repair-1 | valid | Truncated target support and checkout/kafka counterevidence for fraud-detection |

Request 52 chose three metric predicates with no expression (an unadmitted Level
A proposal). Request 54 chose the same three metric predicates and a Level B
memory delta `> 0 BYTES`, plus three dependency aliases. These are model choices,
**not validated rules**. Admission rejects before canonical construction or
expression evaluation; later conditions may also fail and are not assumed valid.
The old candidates, three old rejections and historical terminal are unchanged.

Independent review found that the runner could not prove its third request was
a format-only repair after truncation. The original attempt label remains;
**all three actual dispatches count as initial + two semantic revisions**, so
no revision-2 request followed. A post-replay offline fix forbids unsupported
format repairs, counts failed/truncated dispatches as semantic attempts, enforces
monotonic revisions and stops further sampling after any development success.
This fix was not followed by another Provider request. Frozen execution source
is commit `13b19b6`; current-source checks are separate.

## Evidence and limits

- Reused all 3 Discovery and 2 Development episodes as seen learning material;
  original episode roles and sessions remain unchanged. No new independent
  episode, no blind denominator and no FPR/recall estimate.
- Three new requests, two schema-valid drafts, **zero admitted or reconstructed
  candidates, zero development evaluations**, zero independent-eligible candidates.
  Level A/B learning validity is unestablished. Offline tests exercise canonical
  reconstruction, provenance rejection, expression evaluation and runtime reuse;
  those tests are FIXTURE_ONLY.
- New committed cost upper: **USD 0.144061**. Original ledger now **54/200 requests**,
  **USD 0.839581/20** commitment; actual invoice unknown. Live episodes remain
  **5/12**. The 6-request/USD 1 sublimit was not exceeded; semantic attempts are exhausted.
- Provider/model unchanged: `gpt-5.4-mini-2026-03-17`, Responses. No connectivity
  probes, Docker operations or Product recovery writes. No new resources needed
  cleanup; prior live-02 `clean=true` and live-01 `BLOCKED_SAFETY / clean=false`
  remain historical evidence, not a fresh daemon inspection.
- Runtime source guards and Shadow thresholds were not relaxed. Old request,
  session, episode and rejection digests compare equal. No failed answer was
  hand-corrected into an admitted candidate.

[Result](result.json), [new call projection and admission replay](calls.json),
[frozen protocol](protocol.json), [current offline checks](offline-checks.json).
The call projection reproduces rejection using redacted draft structures and
minimal bound evidence metadata. Free text and unknown alias content stay private;
the original draft SHA identifies private CAS and does not hash the public
projection. This is rejection-only projection replay, not original-draft or
canonical provenance proof. Exact original drafts were separately checked locally.
The existing verifier now checks the old result against its immutable source,
and independently verifies this successor's current code and retained failures:

```sh
PYTHONPATH=src:. uv run --frozen --no-sync python -m scripts.ci.verify_product_v050_docker_stability
```

This is read-only: no Docker, model calls or automatic retries. The bounded round
is consumed. Continuing model development needs a new explicit scoped decision;
this result does not grant another round or justify weakening admission.


Local maintainers with the original CAS can additionally pass `--private-repair`
to that verifier. It opens the original SQLite read-only, verifies CAS bytes and
request-view bindings, recomputes rejection from the unredacted private drafts,
and compares historical ledger/session/episode/rejection hashes. It neither
loads credentials nor dispatches requests.

中文审阅摘要：本轮修复了证据角色与机械编译接口，但真实模型仍未通过准入。
2 个格式有效草稿不等于 2 个有效规则，真实开发求值为 0。独立审查发现并关闭了
格式修复计数、成功后抽样、公开模型文本泄漏和 verifier 漏检；不以修复后的代码
追溯改写模型输出。完整学习闭环与 C 阶段均未完成，新增恢复写入为 0。

Verification scope: read Product source, existing project CAS/ledger and this
Goal; write Product knowledge/investigation adapters, related tests/scripts,
this result pack, Goal/progress and current Product summary docs. Frozen scope:
all predecessor result packs, original calls/sessions/episodes/rejections, DTA
and upstream. Final repository scope is the entire tracked diff from
`398b414b2561b27a94a9663bb8e0ba24627e984f`; untracked local CAS/logs are private,
with explicitly reported read-only private verification.

# DTA v2.3 Open-World Discovery — Error Analysis

## Frozen disposition

The one fixed 24-case comparison executed exactly once and produced
`DTA_V23_OPEN_WORLD_DISCOVERY_MIXED_RESULT`. The result is valid but does not
support the positive-effect claim: novelty recall and root localization both
reached 10/14 (`0.714`), while broad-domain accuracy reached only 1/14
(`0.071`). Evidence-reference validity was `1.000`, the aggregate false-novel
rate was 1/10 (`0.100`), and action-authority violations were zero.

This evaluation used committed replay/derived evidence only. It made zero
Docker calls, created zero live faults, executed zero Runbooks, and granted
zero Agent writes.

## Novelty misses

Four hidden-mechanism cases remained `INSUFFICIENT_EVIDENCE` after two bounded
generic discovery reads and did not call the Provisional Report Provider:

| Case | Evaluator domain | Result |
| --- | --- | --- |
| `ow-003` | `RUNTIME` | hidden service-unavailable incident missed |
| `ow-005` | `RESOURCE` | hidden CPU incident missed |
| `ow-006` | `RESOURCE` | hidden CPU incident missed |
| `ow-007` | `RESOURCE` | hidden memory incident missed |

The failure is before report generation: the Novelty Gate retained a
fail-closed insufficient terminal rather than manufacturing a provisional
incident from the available residual graph. This preserves safety but reduces
recall.

## Localization versus mechanism understanding

All 10 emitted novelty reports localized the expected opaque root service, but
only `ow-008` selected the expected broad domain. The two hidden configuration
cases were labeled `RUNTIME`; one detected hidden runtime case was labeled
`UNKNOWN`; the two detected dependency cases and all four truly unregistered
synthetic cases were labeled `UNKNOWN`.

The secondary semantic grades were: mechanism-level match `1`, symptom-level
plausible `5`, and unrelated `4`. The strong root-localization score therefore
must not be represented as equally strong mechanism or domain discovery.

## Control failures and preservation

- Registered-known accuracy improved from 3/4 in `CLOSED_WORLD_ONLY` to 4/4 in
  `OPEN_WORLD_DISCOVERY`; known-accuracy drop cases: `0`.
- No-Incident accuracy remained 2/3 in both arms; No-Incident drop cases: `0`.
- Insufficient/conflict accuracy was 1/3. `ow-022` was the only false-novel
  control and emitted a provisional `RESOURCE` incident. `ow-024` remained an
  incorrect `NO_INCIDENT` terminal in both arms.
- Every provisional report had valid evidence references, cited a residual
  anomaly, included alternatives, and retained `action_authority = NONE`.

## Discovery and Provider cost

The open-world arm averaged `1.083` generic discovery reads per case. It used
13 Provider calls, 2 protocol repairs, 0 transport retries, 36,588 input
tokens, 7,919 output tokens, and 44,507 total tokens. Empty-read rate was
`0.385`; generic-anomaly yield was `0.577`; Negative Coverage was used 11
times.

## Bounded conclusion

The evidence supports a separate, safe open-world discovery MVP that can turn
some closed-world abstentions into typed provisional reports without weakening
known-world results. It does not establish reliable broad-domain or mechanism
discovery, production autonomy, remediation authority, or live-fault
generalization. The frozen mixed terminal is retained without rerun or metric
optimization.

# DTA v2.3 Open-World Discovery — Fixed Evaluation

Evaluation acceptance: `VALID / FINAL_REVIEW_PENDING`

Measured terminal: `DTA_V23_OPEN_WORLD_DISCOVERY_NOT_OBSERVED`

- Execution count: `1`
- Cases / runs: `24` / `48`
- Novelty recall: `0.429`
- Root localization: `0.429`
- Broad-domain accuracy: `0.357`
- Evidence-ref validity: `1.000`
- False-novel rate: `0.100`
- Registered-known closed/open accuracy: `1.000` / `1.000`
- No-Incident closed/open accuracy: `0.667` / `0.667`
- Mean discovery reads: `0.708`
- Provider calls / repairs / retries: `7` / `0` / `0`
- Action-authority violations: `0`

- Semantic artifact SHA-256: `888d6242743433e02b2aebdaa292b531f1844ffc07f392887634501e882476ea`
- JSON file SHA-256: `1c6fb59f260c87accd3d11d193461e9f9a2f725f2315209d934e659d8f69e079`
- Closed arms carrying Graph/Gate/Negative Coverage state: `0`
- Case pairs sharing the same v2.2 known-admission binding: `24 / 24`

The measured terminal is a valid negative result: novelty recall (`6 / 14`) is
below the `0.50` mixed-result threshold. The study will not be rerun for metric
optimization.

The study used committed replay/derived evidence only. It did not call Docker, create a live fault, execute a Runbook, or grant Agent write authority.

The earlier internally scored mixed artifact remains separately preserved as
an `INVALID / REVIEW_REQUIRED` predecessor. A subsequent pre-evaluation
schedule attempt stopped after two case pairs with zero Provider calls and no
final artifact; its local sentinel and partial JSONL are retained as
`PROTOCOL_BLOCKED / INVALID`. Neither predecessor is represented as this valid
study.

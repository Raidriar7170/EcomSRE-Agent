# DTA v2.3 Open-World Discovery — Fixed Evaluation

Repository acceptance: `INVALID / REVIEW_REQUIRED`

The immutable artifact below is internally self-consistent, but independent
final review found that its `CLOSED_WORLD_ONLY` arm consumed v2.3 Residual
Evidence Graph and Novelty Gate state instead of the Goal-specified existing
known Diagnosis admission. The implementation also differed from the approved
single-source Novelty Gate rule and did not prove four data-level
counterfactual service-target pairs. The measured terminal is retained as an
artifact fact; it is not an accepted result for the Goal-defined comparison.

Measured terminal: `DTA_V23_OPEN_WORLD_DISCOVERY_MIXED_RESULT`

- Execution count: `1`
- Cases / runs: `24` / `48`
- Novelty recall: `0.714`
- Root localization: `0.714`
- Broad-domain accuracy: `0.071`
- Evidence-ref validity: `1.000`
- False-novel rate: `0.100`
- Registered-known closed/open accuracy: `0.750` / `1.000`
- No-Incident closed/open accuracy: `0.667` / `0.667`
- Mean discovery reads: `1.083`
- Provider calls / repairs / retries: `13` / `2` / `0`
- Action-authority violations: `0`

The study used committed replay/derived evidence only. It did not call Docker, create a live fault, execute a Runbook, or grant Agent write authority.

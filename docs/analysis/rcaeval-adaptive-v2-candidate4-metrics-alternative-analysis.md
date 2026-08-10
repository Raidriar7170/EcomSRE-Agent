# Adaptive v2 candidate-4 Metrics alternative analysis

Classification: `POST_HOC_CONSUMED_TUNE_DIAGNOSTIC / NO_PROVIDER_CALLS / NOT_EXTERNAL_VALIDATION`.

## Finding

The deterministic non-Initial Metrics alternative matched the True Root in 7/8 completed Initial-wrong cases (87.5%). This clears the minimum opportunity condition for Candidate-5, but it does not predict Gate passage.

## Coverage and selection

- Gate-escalated Initial-wrong: 8/8
- True Root Metrics Coverage@1 / @2 / @3 / @6: 6/8 / 8/8 / 8/8 / 8/8
- Alternative truth and Logs visible: 0
- Initial and Alternative both Logs visible: 0
- True Root / Alternative / Initial Logs visible: 0 / 0 / 6
- No alternative: 0
- Metrics Top1/Top2 normalized margin min / mean / max: 0.037749 / 0.617414 / 0.906946

The zero Logs-visibility counts make the pairwise-verifier hypothesis high risk: the opportunity comes from Metrics selection, while the bounded Logs evidence may remain insufficient to choose the alternative.

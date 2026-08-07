# RCAEval RE2 v2 development protocol

This track is limited to the development-visible RE2-OB and RE2-SS systems. It preserves the RCAEval RE2 v1 frozen implementation, results, and attribution conclusions. It does not access or rerun the external holdout.

The protocol adds typed create-once operation records for Specialists, Commander, final Judge, deterministic indicator resolution, exact Provider usage deltas, run traces, and terminal records. Every scheduled run allows one semantic attempt, no transport retry, no result-driven retry, and no fallback.

The deterministic split contains 60 DESIGN cases and 120 DEV_VALIDATION cases. F0, F1, and F2 were compared only on DESIGN; F0 uniquely passed the pre-registered overall, Memory, Socket, and per-fault gates and was frozen for runtime use.

The bounded Provider smoke did not pass. Execution stopped after an agent-visible log observation contained a local absolute path and was rejected before an operation marker could be created. Because exact failure-stage coverage became impossible for the frozen run identifiers, the remaining DESIGN and all DEV_VALIDATION runs were not executed. Current disposition: `V2_PROVIDER_DEV_GATE_NOT_PASSED`.

Single-first Adaptive Escalation, adaptive confidence gates, targeted refinement, architecture-blind judging, heterogeneous Specialists, external holdout work, release, and deployment are outside this protocol.

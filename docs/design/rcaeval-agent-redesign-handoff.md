# RCAEval Agent Redesign Handoff

Infrastructure terminal state: `RCAEval_RE2_V2_DEV3_DESIGN_COMPLETE_READY_FOR_AGENT_REDESIGN`

This document is the only recommended entry point for the next phase. The infrastructure line ends at v2-dev.3; do not create another Harness-only development version.

## Required architecture

Strong Single baseline → deterministic uncertainty/conflict gate → zero escalation for easy cases → selective Specialist escalation for hard cases → contradiction-aware fusion → deterministic Indicator Resolver.

The design is Single-first and permits zero follow-up calls. It must not invoke every evidence source by default. Specialists return ranked hypotheses with supporting and contradicting evidence and label each hypothesis as root, symptom, or uncertain causal role. The final Judge remains architecture-blind. The Indicator Resolver and v2-dev.3 transport policy remain intact.

## Required acceptance metrics

- Damage Rate
- Rescue Rate
- Escalation Precision
- Escalation Recall
- Zero-escalation Rate
- Root Service AC@1
- Root Cause Pair AC@1
- Terminal Failure Rate
- Tool Calls
- Semantic Operations
- Provider Attempts
- Tokens
- Latency

## Scope of the next task

Implement Single-first Adaptive RCA Agent on OB/SS DESIGN data.

This handoff does not implement that Agent. DEV_VALIDATION remains unauthorized and RE2-TT remains forbidden.

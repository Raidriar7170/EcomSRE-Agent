# DTA v2 Local Live Demo

Terminal: `DTA_V2_LIVE_DEMO_ACCEPTANCE_PASS`

## NO_FAULT

- Attempt terminal: `LIVE_PASS`
- Tool sequence: `query_trace_neighborhood, inspect_service_runtime, query_metrics, query_metrics, submit_dta_diagnosis`
- Tool dispatches / Provider turns: 4 / 5
- Diagnosis: `None / None / None`
- Candidate Runbooks: `none`
- Proposal / admission / Runbook: `none / DENY / none`
- Step receipts / recovery windows: 0 / 0
- Baseline / cleanup: `True / CleanupTerminal.CLEAN`

## PAYMENT

- Attempt terminal: `LIVE_PASS`
- Tool sequence: `query_trace_neighborhood, query_metrics, inspect_service_runtime, inspect_resource_usage, submit_dta_diagnosis, submit_dta_action_selection`
- Tool dispatches / Provider turns: 4 / 6
- Diagnosis: `payment / FaultDomain.CONFIGURATION / FaultMechanism.CONFIGURATION_ERROR`
- Candidate Runbooks: `ROLLBACK_CONFIGURATION`
- Proposal / admission / Runbook: `EXECUTE_RUNBOOK / ALLOW / ROLLBACK_CONFIGURATION`
- Step receipts / recovery windows: 1 / 2
- Baseline / cleanup: `True / CleanupTerminal.CLEAN`

## RECOMMENDATION

- Attempt terminal: `LIVE_PASS`
- Tool sequence: `inspect_service_runtime, query_metrics, submit_dta_diagnosis, submit_dta_action_selection`
- Tool dispatches / Provider turns: 2 / 4
- Diagnosis: `recommendation / FaultDomain.SERVICE_RUNTIME / FaultMechanism.SERVICE_UNAVAILABLE`
- Candidate Runbooks: `RESTART_SERVICE`
- Proposal / admission / Runbook: `EXECUTE_RUNBOOK / ALLOW / RESTART_SERVICE`
- Step receipts / recovery windows: 1 / 2
- Baseline / cleanup: `True / CleanupTerminal.CLEAN`

## EMAIL

- Attempt terminal: `LIVE_PASS`
- Tool sequence: `inspect_resource_usage, query_metrics, inspect_service_runtime, submit_dta_diagnosis, submit_dta_action_selection`
- Tool dispatches / Provider turns: 3 / 5
- Diagnosis: `email / FaultDomain.LOCAL_RESOURCE / FaultMechanism.MEMORY_LEAK`
- Candidate Runbooks: `MITIGATE_MEMORY_LEAK`
- Proposal / admission / Runbook: `EXECUTE_RUNBOOK / ALLOW / MITIGATE_MEMORY_LEAK`
- Step receipts / recovery windows: 2 / 2
- Baseline / cleanup: `True / CleanupTerminal.CLEAN`

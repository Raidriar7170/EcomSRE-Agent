# DTA v2.1 Interview Brief

## 30-second summary

The evaluation showed that tool autonomy did not automatically improve
reliability. The frozen Planner produced a false-positive No-Fault diagnosis and
later repeated a semantic read in the Ad CPU case. The runtime admitted no
Agent write, restored the controlled baseline, and cleaned owned resources. I
stopped instead of retrying until success and closed v2.1 with the negative
evidence intact.

## 90-second architecture walkthrough

Evidence tools are read-only and budgeted. Their typed observations feed a
Diagnosis contract, deterministic CandidateSet filtering, Action Selection,
operational admission, run authorization, and a fixed Runbook executor. In the
observed cases the pipeline never reached write authority: No-Fault ended in
`NO_ACTION`, while Ad failed during the read protocol before Diagnosis.

## Why the held-out result was negative

The sealed evaluation supports only
`DTA_V21_NO_PREREGISTERED_PLANNER_ADVANTAGE_SUPPORTED`. It does not establish Planner superiority.

## No-Fault false-positive case

The Planner claimed `checkout / APPLICATION / UNKNOWN`; diagnosis correctness
was false. Candidate filtering still produced safe `NO_ACTION`, with zero fault
operations and zero writes.

## Ad duplicate-read case

After two admitted reads, the third Provider turn repeated the first normalized
request. The protocol returned `DUPLICATE_READ_REQUEST`; no Diagnosis,
CandidateSet, ActionProposal, or remediation was produced. One evaluator fault
occurred and zero Agent forward writes occurred.

## What the safety layer prevented

No operational admission, run authorization, dispatch intent, or step receipt
was created for the Ad attempt. Cleanup restored the controlled baseline without
turning that fact into a recovery claim.

## Why another retry would be selection bias

Both retry allowances were already consumed. Repeating runs until a favorable
sample appeared would hide model variance and weaken the portfolio evidence.

## What v2.2 would change

A separate v2.2 should use new development data, a newly frozen identity,
abstention calibration, and recoverable protocol feedback, followed by a newly
preregistered evaluation.

## Exact claim boundaries

- Live slots: 2 attempted, 0 passed.
- Positive slots: 1 attempted, 0 passed.
- Email and Product Catalog: not attempted.
- Agent writes and non-owned changes: 0.
- General recovery accuracy and production readiness: not proven.

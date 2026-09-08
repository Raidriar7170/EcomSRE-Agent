# Product v0.4 engineering preflight v4 evidence

Attempt 1 failed before Probe start. The Probe and three named Kafka volumes were exactly removed. Its immutable result remains `FAILED / BLOCKED_SAFETY`; the subsequent closure record is `OWNED_REMOVED_NONOWNED_DRIFT`, not CLEAN.

The copy-up pre-read guard compared raw Mounts list order. Raw captures show only array order differed, with immutable mount contents unchanged. Separately, the builtin bridge network ID/Created changed across first volume creation. Docker backend logs directly show that first request woke the idle VM. VM startup causing bridge reinitialization is a supported inference; no bridge restoration or normalization was performed.

Independent review permits a separately bound stabilization repair under Goal section 8.3. All owned resources are absent; the next attempt remains withheld until repaired-head review, tests and CI complete. Budget: 1/5 consumed, consecutive passes 0. No Sandbox or Product start, traffic, baseline, diagnosis, Provider or formal action occurred.

[Attempt 1](attempt-01.json) preserves the failed public summary. Private raw records remain append-only; public commitments disclose no tokens or private absolute paths. This is not a final or positive preflight result.

A separate post-attempt read-only `system df` diagnostic exceeded section 18's version/info-only wake restriction. Its evidence and observed idle-VM wake are retained; it created no runtime resource. The command was removed from the execution path. Subsequent admission uses the authorized build followed by version/info and a fresh baseline, subject to independent review and complete checks.

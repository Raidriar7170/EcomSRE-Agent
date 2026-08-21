# DTA v2.2.4 Independent Read-Only Review

- Review type: independent read-only Codex review
- Reviewed range: `9c601bd5d802fbe31990348c228e094985044a0b..b7902dc50b575743de07893c37f37132b1c9de17`
- Reviewer sandbox: read-only
- Provider/Docker/Runbook/Agent-write study paths executed by reviewer: `0`
- Final-study reruns by reviewer: `0`

## Required review result

```text
Verdict: Blocked

Must Fix:
1. Provider-visible evaluation service identifiers encode hidden mechanism or
   control labels, violating truth isolation. Preserve the frozen bytes, mark
   the study INVALID, and retract the effect claim; renaming inputs cannot
   repair the completed study.
2. AMBIGUITY_SET_COMPLETE fails open when completion is unaffordable, and a
   read can be forgotten when closure was not yet required.
3. Final-study preflight does not enforce the complete declared manifest and
   implementation boundary.

Should Fix:
1. Define resource-case denominators from the fixed case set, not
   treatment-produced ambiguity-set fields.
2. Add opaque-name, insufficient-budget, pre-closure coverage, complete
   preflight, schedule correspondence, and case-scoped repair-budget tests.

Nice to Have:
1. Add a lint rejecting truth-bearing vocabulary in Provider-visible IDs.
2. Tie public result claims to the exact evaluated implementation commit.

Scope Creep Warning:
Current HEAD contains constructor-only changes after the manifest-bound
implementation commit. Frozen results must not be generalized to current HEAD.

Evidence Gaps:
Claim Accuracy: FAIL
The artifact validates and re-scores identically, but the frozen evaluation
violates truth isolation. The one-execution count is self-reported because the
partial journal is deleted on successful completion.

Recommended Next Step:
Publish an evidence-only INVALID closeout without rerunning the study. A
successor requires separate authorization.
```

## Primary evidence locations

- Truth-bearing names are produced in
  `src/ecomsre/dta_v2/v22/evaluation_builder_v224.py` and stored under
  `config/dta-v22-4/evaluation/agent-visible/`.
- Candidate service names enter Provider-visible state in
  `src/ecomsre/dta_v2/v22/ambiguity_bundle_campaign_v224.py`.
- The fail-open budget condition is in
  `src/ecomsre/dta_v2/v22/no_incident_set_closure_v224.py`.
- The partial manifest preflight is in
  `src/ecomsre/dta_v2/v22/ambiguity_bundle_cli_v224.py`.

The reviewer could not run pytest inside its enforced read-only sandbox because
pytest required a writable temporary directory. This is not represented as a
test failure. The implementation owner's previously completed local checks and
the GitHub CI results remain separate evidence.

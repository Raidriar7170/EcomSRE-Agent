# EcomSRE Product v0.2.2.2 Independent Final Review

## Verdict

- Must Fix: **0**
- Claim Accuracy: **PASS**
- Review disposition: the Increment 5 implementation and evidence claims may
  proceed to exact-head CI and publication.
- Completion disposition: **not yet COMPLETE**; commit, push, GitHub CI,
  Ready status, squash merge, and predecessor-PR closeout remain separate
  required gates.

This was an independent read-only review of the uncommitted Increment 5 diff
on `codex/product-v0222-capture-first-operator-profile` at parent commit
`5e9a4c6391fc335e24f242d9c5111739835ab401`.

## Claim-accuracy findings

- P01 remains bound to the operator decision, frozen Candidate Set, ACTIVE
  normalization profile, sanitized fixture, offline parser report, and fresh
  holdout verification.
- The three-window connector smoke remains
  `ECOMSRE_PRODUCT_V0222_CONNECTOR_SMOKE_PASS`: three non-overlapping
  `SUCCESS_NONEMPTY` queries, 15 accepted checkout records, zero rejection or
  schema counters, 30/30 healthy traffic, unchanged baseline, and `CLEAN`
  cleanup.
- A separately launched child process, with a PID distinct from its parent,
  reloaded the persisted ACTIVE profile and smoke profile and reconstructed the
  connector configuration and static capabilities. This supports the bounded
  claim that the active profile survived a fresh consumer-process relaunch.
- The restart proof issued zero network requests and did not repeat the
  consumed live smoke. The original smoke JSON and private start/completion
  records remain unchanged.
- The service-identity artifact explicitly binds logical service `checkout`,
  configured aliases `checkout` and `checkoutservice`, the selected source and
  query fields, and all three successful live query-result SHAs.

Key bindings checked by the reviewer:

- connector smoke SHA:
  `a3573782f56f5445db8920301e267840d9a1296e51027c0fda841bfd4bd303c2`;
- active-profile restart proof SHA:
  `b2b0ea37d763316a7be9acb65899f2a7d1fddcdb7f222ca05f63bab742fda69b`;
- service identity SHA:
  `a84e3f82180cd40024f9449096586b0ac94ceea8b492f59d58c58c28d7828d72`;
- Baseline handoff SHA:
  `fee46e6f335f106f365c3c0c85bb1cf8e7fb0b7cbf00289f5555ec84ea0cdaa7`.

## Verification observed by the reviewer

- Increment 5 verifier: PASS;
- Product v0.2.2.2 tests: 28 passed;
- Ruff: PASS;
- mypy for the changed connector and verifier modules: PASS;
- `git diff --check`: PASS.

The reviewer did not start Docker, issue a live OpenSearch request, repeat the
connector smoke, change Product state, or modify repository files.

## Post-review CI portability follow-up

The first exact-head CI attempt at
`4007c2f2b61b3c1612b42265ffac27b49d43adf0` exposed one portability failure:
the fresh child process could not import the source tree when full pytest ran
without a `PYTHONPATH`. The repository uses `[tool.uv] package = false`, while
pytest's configured `pythonpath = ["src"]` affects the parent process only.

The narrow repair explicitly gives the child process the repository root and
`src` path while preserving any inherited `PYTHONPATH`. A regression test now
deletes `PYTHONPATH` before launching the child. The independent reviewer
rechecked this repair and again returned `Must Fix: 0 / Claim Accuracy: PASS`,
confirming that it retains the distinct-process boundary, issues no network
request, does not rerun the live smoke, and leaves all frozen smoke and restart
proof hashes unchanged.

## Optional improvement

A future proof helper could attach an HTTP request hook to turn the current
static no-network code-path guarantee into a runtime request count. The child
currently calls only the connector's static `capabilities()` method, so this is
not a Must Fix for the present claim.

## Remaining gates

This review does not authorize or claim
`ECOMSRE_PRODUCT_V0222_CAPTURE_FIRST_OPERATOR_PROFILE_COMPLETE` by itself. The
repository must still obtain committed-state verification, green exact-head
GitHub CI, Ready status, squash merge, and close PRs #75–#78 only after the
successor merge.

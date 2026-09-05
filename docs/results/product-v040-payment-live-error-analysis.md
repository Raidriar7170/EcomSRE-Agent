# Product v0.4 Payment execution error analysis

Current status: PREPARATION_FAILED_BEFORE_FORMAL_FREEZE, repaired source pending
fresh validation. This is not a measured Payment-remediation terminal.

The first no-fault preparation used source
`5657368e2d0e425cd74a99ad78f20de7270d2b4c`. The Product image built and the owned
Demo started. Product observation-proxy creation then failed because an unquoted
YAML flow-list tmpfs value was split at commas. The earlier config-only check
had not validated the expanded tmpfs entries, so it did not detect the invalid
mount. No Product API/Worker or remediation process was started successfully.

The preparation controller stopped and removed only its owned resources.
Baseline was read back as restored; cleanup was CLEAN; owned containers,
networks and volumes were all zero; non-owned resource fingerprints were
unchanged. No formal manifest, fault intent or attempt request was created.
The fault, accepted-attempt, write-intent and forward-mutation counts were zero.

All original private bytes were retained in a distinct failed-preparation root.
Its preserved evidence-set digest is
`c9489a192ba49159668f4c9e5d43a90280eae0925cba7d14c526d73fb0e41756`.
The public-source build context is separately bound by the retained build-input
manifest. No failed result or historical experiment was rewritten.

The fix quotes the single tmpfs string and validates every exact expanded tmpfs
mount before Docker build or startup. Actual Docker Compose resolution passes
with the corrected string. Added regressions reject the split list, extra mount
and wrong size. A separate static inspection identified that ordinary SQLite
creation did not guarantee private file modes. The v0.4-only fixed process
bootstrap now sets umask 077 before application dispatch; runtime permission
checks inspect Product/ledger directories, DB, WAL/SHM when present and CAS.
Missing sidecars are not presented as measured sidecars. No exposure or live
permission success is claimed from that static observation.

Fresh independent source review, clean full tests and exact-head CI are required
for the corrected source. A new no-fault preparation may establish the real
Baseline and isolation evidence; the one formal fault allowance is unconsumed.
Once a formal manifest or fault intent exists, no rerun is permitted.

## Further no-fault preparation and ingress diagnosis

Preparation 002 used `ddf2e1b25893af868b08b73cbffeced9dab0fcdb`.
The Demo and Product bootstrap reached container readiness, but the first host
Product API request failed with connection refused. No environment was created
and no formal fault/manifest/attempt existed. Cleanup was CLEAN, baseline was
read back as restored, all owned resource counts were zero and non-owned
fingerprints were unchanged. Retained evidence-set SHA-256:
`861a2c7dee3f961ca6ed5e2a457b5658ff4dbd3c84ae9d39f92096e403c75829`.
The actual Product database was observed with mode 0600 after cleanup; this is
not a claim that transient WAL/SHM files were measured.

Two separate Product-only ingress diagnostics started no Demo and consumed no
fault or Provider call. Diagnostic 001 encountered an API process exit before
its inspection stage. Its cause remains unknown because stderr was not captured
before cleanup. Diagnostic 002 measured a healthy API listening on 0.0.0.0:8080,
an empty Docker published mapping for that port, and repeated failed host GETs.
Both diagnostics cleaned all owned resources and retained unchanged non-owned
fingerprints. Their retained index file SHA-256 values are respectively
`474db4201147cb78b3d96cfd9fbecda7bb107710fc97d163bb73531ebcd82403`
and `afa55821645c689fcc356d6fec25d1ee9fa509aed874e747ef7329394403612e`.

The bounded ingress repair retains API/Worker on the internal network and
publishes a separate listener on the existing observer to host loopback. It
forwards only the campaign's enumerated Product routes to literal `api:8080`,
passes the caller's authentication, has no credentials of its own, and rejects
control routes, arbitrary targets and redirects. A transport failure is never
retried. Request/response sizes and transport timeouts are bounded. The existing
observation application remains a separate read-only listener.

Startup now initializes the API database before Worker starts and checks host
readiness with bounded GETs before the first POST. This removes concurrent
initialization as a variable; it does not prove that SQLite caused diagnostic
001's exit. Preparation failure captures owned container state and both log
streams before cleanup, with capture failure unable to block cleanup. The
repaired ingress still requires real no-fault preparation and final source gates.

## Preparation 003: first healthy checkout exceeded proxy deadline

Source `3d6c85cfa15f8987dfea58692b5ed56e24382a66` established the host ingress,
registered the environment and passed connector verification. The first cart
returned 200; its checkout returned 504 after approximately 15 seconds. The
fixed error budget stopped traffic at one attempted transaction, zero successes
and one failure. No Active Baseline was created. Retained Payment and shipping
logs show transaction completion approximately 1.2 seconds after the proxy
response. These observations do not prove a cold-start cause.

The failed preparation is retained with evidence-set SHA-256
`abe9bbd9f07c385a2ed2f2b58520e59cb2f578aea56242ec6032ace1085753f6`.
Cleanup was CLEAN, baseline readback restored, all owned resource counts zero
and non-owned fingerprints unchanged. No formal freeze, fault or attempt
occurred. The same source passed a clean full local test run: 6,564 passed,
21 skipped, 16 warnings. Offline success does not alter this preparation result.

A separate pre-freeze preparation adds exactly one bounded application-warmup
group: at most three distinct transactions using seed 40400, a six-second
interval and a 120-second total request deadline. Each POST has a create-once
intent, bounded private response bytes and digest, status or transport error,
and monotonic duration. A timed-out transaction is not replayed. The group
cannot be replaced in that private root. All failures remain evidence; an
interrupted request may have its own intent/result without a completed aggregate
transaction row. Counts must therefore be read from both records.

A fixed 330-second quiet interval follows a completed group with at least one
business success. This allows five minutes plus a margin before the healthy
control, but does not prove all telemetry has expired: Kafka native quantiles
and delayed observations differ. The original healthy 30/30 requirement,
error budget 1, 360-second observation and five successful Baseline windows
remain unchanged, and the subsequent NO_INCIDENT control remains required.
The pre-freeze workload policy changes are source-bound before any formal fault.
Future preparation failures also capture proven-owned Demo logs before cleanup;
a per-container log failure is recorded without discarding earlier captures or
preventing cleanup.

## Preparation 004 and safety checkpoint

Source `a0e8aab077bdd6f9c8374f05f3f53127c4820fda` measured warmup checkout
statuses 504/200/200 with two independently inspectable business-success bodies.
After the fixed quiet interval, the original healthy control completed 30/30
successes and the Active Baseline was created. The first ownership witness
then rejected `non-owned resource drift`, before executor enablement, runtime
network-denial checks or the NO_INCIDENT control. The exact differing resource
at that moment was not captured. This is a retained preparation failure, not
formal Payment-remediation evidence.

The cleanup record remains byte-exact: baseline restored, owned resources zero
and non-owned equality at that observation. A subsequent read-only check was
also equal. Later, before preservation, a fresh check detected replacement of
the builtin bridge network identity; host/none and the three pre-existing
anonymous volumes retained their identities. Both Goal projects have zero
containers, networks and volumes, and the local Docker daemon identity still
matches. The replacement bridge reports a creation time of
2026-09-05T21:38:01.630151875Z. A bounded historical network-event read returned
no events; absence of retained events does not identify a cause. Attribution is
UNKNOWN. No non-owned network restoration or takeover was attempted.

A separate read-only image inspection found three Kafka VOLUME declarations
uncovered by the admitted Compose service mounts. Docker may create anonymous
volumes for these paths, so resolved Compose mounts alone are insufficient to
prove the intended ownership boundary. This is a concrete preflight gap, but
it is not a proven explanation for the original witness failure. New runtime
startup is stopped pending explicit owned mount coverage, first-difference
capture and independent review. Omitting resources or comparison fields is not
an acceptable repair.

Preparation 004 retained evidence-set SHA-256:
`5a03c913884e5a76be956f3d07be2fc2c6fcdcf6521483b1a1883a576624f277`.
Its original cleanup record is not rewritten to hide the later change. The
current safety checkpoint explicitly records non-owned resource change with
unknown attribution; it makes no runtime-wide unchanged claim.

The same runtime source passed local full tests (6,570 passed / 21 skipped),
Ruff, mainline mypy and exact-head CI 33992709388. An earlier CI 33991830066
failed an unchanged historical PID-file test at the interval between file
creation and PID content write (549 passed / one failed in that step); its log
and independent source diagnosis are retained. No test was skipped to obtain
the newer result. Green CI does not override this ownership checkpoint.

The formal manifest, fault intent, accepted attempt, write intent, forward
mutation, receipt and recovery-window counts remain zero. The formal allowance
is unconsumed. See the separate [machine-readable safety checkpoint](product-v040-pre-execution-safety-checkpoint.json)
and [Chinese Human Brief](product-v040-payment-live-human-brief.md). No canonical
formal manifest/result is published for a campaign that never began.

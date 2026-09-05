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
Its preservation-index digest is
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

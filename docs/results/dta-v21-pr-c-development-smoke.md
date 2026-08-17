# DTA v2.1 PR-C Development Smoke

Status: `BLOCKED_DTA_V21_PROVIDER`

The replay/fake suite passed the Planner CPU, same-service Email unavailable,
Trace-led Shipping dependency-latency, and One-shot no-action cases. It also
proved typed duplicate rejection, pre-backend rejection of a fifth read, and
separate One-shot context-materialization accounting.

The bounded real-Provider compatibility Smoke used the configured
`gpt-5.4-mini-2026-03-17` model. The preferred model was not present in the
private Provider configuration, so the sole configured compatible model was
selected before the provisional identity freeze. The verified attempt admitted
two responses and two reads, then ended `PROVIDER_TRANSPORT_FAILURE` when the
third call returned no response. This is the typed external blocker permitted
by the PR-C exit gate; it is not represented as a Provider PASS.

One attempt was predeclared with a revision-bound manifest and sealed with an
append-only receipt. The six older Provider directories remain immutable but
are explicitly classified `legacy_unbound`; they are not counted as verified
attempts and do not support the current conclusion. The sanitized public ledger
contains only typed outcomes and hashes, with no raw Provider content,
credential, request, private path, or hidden reasoning.

No Docker action, fault injection, Runbook execution, or held-out execution
occurred in PR-C.

# Phase 5B Hidden Pack Builder Specification

This document owns the Phase 5B-1 build-and-seal boundary. Phase 5B-0 creates
no real hidden content. Phase 5B-1 requires separate authorization after the
protocol PR is reviewed and merged.

The builder must start from the merged frozen protocol commit in an independent
environment and may read only this specification plus the committed public
protocol contracts. It creates six opaque templates (`hidden-01` through
`hidden-06`) and five paired seeds per template in an external, non-repository
directory. Every agent-visible seed directory contains exactly the Phase 1
replay files `manifest.json`, `incident.json`, `metrics.json`, `logs.json`,
`traces.json`, and `changes.json`. Evaluator truth is stored under a separate,
non-overlapping `ground-truth` root.

The builder must emit the canonical `phase5b.hidden-pack-manifest.v1` manifest,
including one path-and-canonical-bytes SHA-256 for the complete agent-visible
pack and one for the complete ground-truth pack. Symlinks, traversal, unknown
files, duplicate IDs, noncanonical JSON, or root overlap fail closed. The
public manifest exposes only opaque IDs and content hashes; it must not expose
expected decisions, root services, mechanisms, evidence bodies, or truth file
contents.

During Phase 5B-1 the builder must not run an Agent, call a Provider, read any
Phase 5B result, modify a prompt or runtime, commit the pack to the public
repository, or unblind the pack. `OQ-007`'s hidden-pack portion may be closed
only after independent validation confirms the external sealed directory and
binds its manifest hashes to the frozen protocol commit.

# DTA v2.1 P0 Baseline Inventory

Status: `BASELINE_VERIFIED / PR_A_ONLY / NO_PROVIDER / NO_DOCKER`

Refreshed on 2026-08-17 before creating the PR-A implementation branch.

## Repository authority

- Inspected and actual `origin/main`:
  `925d23994888d1b83e57fc1bbdd1944e57a1bfff`.
- Required DTA v2 PR-F ancestor:
  `9906f63df0e4f7cf65b4061ac24ea0061c14680a`.
- The inspected head is an ancestor of current `origin/main`; in fact, it is
  the current head.
- The current main subject is `DTA v2: finalize post-merge portfolio state`.
- No open pull request or existing `codex/dta-v21-p0-*` branch was found at
  startup.
- The PR-A worktree started clean and isolated from the pre-existing shared
  checkout changes.
- `openspec/config.yaml` is absent, so no active OpenSpec change applies.

The active user-designated execution contract is
`dta-v21-p0-master-v1`, SHA-256
`3c91e7777395e46f088695640991c17da1f70285bd844739391346b56f168daf`.
No private path or credential is recorded here.

## Frozen upstream

- Gitlink: `1755859a9de82c2e5e225be68abc401a5ebf2b4f`.
- Release tag: `3.0.0`.
- The upstream tree remains read-only.

## Historical DTA v2 truth

- Current Agent identity:
  `6efc26c6e5fab6190be9e63c0bec318c6e94fa29196e6693eb63b2845c6ad0a4`.
- Held-out seal:
  `0f944e79f0958f285006c3bdc3cf8f82b8a71731d8d96d02b474f254a54e247a`.
- Held-out result: `COMPLETED_HELD_OUT_NEGATIVE`.
- Live terminal: `DTA_V2_LIVE_DEMO_ACCEPTANCE_PASS`.
- Old held-out reruns at startup: `0`.
- Old Agent or Provider reruns at startup: `0`.

The historical result remains a replay negative for Tool Use superiority and
does not transfer to the v2.1 successor.

## Bound artifact inventory

| Path | Raw SHA-256 | Canonical JSON SHA-256 |
|---|---|---|
| `config/dta-v2/agent-identity.v1.json` | `81a792ee545229fa12f9e1965395b6cef787000c1aedde924e08a241d98f2edb` | `ee89e40123bc0e236a339f7056f9f3606f23be7fe3fe4285d9eb832059b25626` |
| `config/dta-v2/live-demo.v1.json` | `48eafa4055a25642e5fd852448f54bf4c71abcb4f9733f0f1c3cb9bfbbed8e1f` | `1311c93077f78dc2e20a9770d1ca97d03a80e8cb09157e1e677d6467a84ba116` |
| `config/dta-v2/live-demo.v2.json` | `90631c28e4d4215d080f66faeed2b65951cce5a99c7b79a7f34ef66f68ff0c8d` | `3676168c9508ba8ffbf1e3d2c85e74ab7908bb8e6da34bf029a3caff7b69ac7b` |
| `config/dta-v2/evaluation/manifest.json` | `f6ff2f222a725377f664b1ae70c7a300c6c2c4f9bfacc8420e3c5861ddcf697e` | `ecad0ed1ff33b8c65aacc94f262abe39dd4347f41c58371009cdc49d41d41200` |
| `docs/analysis/dta-v2-master-progress.json` | `5a9fe8a5f44eca9b9a7f9cf5d1a7dc95f850de0e4eb1ef3f7d4f028f44513f6f` | `40f611c5bce16f4748b8bd2507262f54cffa3251acfcede21ee71032c3c98385` |
| `docs/design/diagnosis-to-action-v2.md` | `a3cb6ae5b36173c8d372921aaf6a29d0fbec1f89ccd3f992e45e1745c999e24a` | — |
| `docs/results/dta-v2-evaluation.json` | `59062eba68e0723d22d9844de807088640f03110b1d3fd5b4044f4a53ef10846` | `7a8d70ab7c25096f90e8f9b2ebdf5d5cffc3cb14bda6e8b20eba9c545d9944d0` |
| `docs/results/dta-v2-evaluation.md` | `4f5ed888c5e90ee55a264d22049f8bf54c1eebc444f5a539fb79087a9d62b2c2` | — |
| `docs/results/dta-v2-live-demo.json` | `8c1d0c70a2c32c5cbd4e9f06649cec3ca447f20cf4e66de248a37240112d98e7` | `0213ddb9d817a6db70a54cb46fed9d19e6f41a579cb21ff377e95b11ba490bd2` |
| `docs/results/dta-v2-live-demo.md` | `eb666f5d6995d8ee17d82bc377c8d74143fe4fe5d98a285b093706ba3393b6ce` | — |
| `docs/results/dta-v2-live-demo-human-brief.md` | `16bf1966e0c897b70729ede345e7a36eb6618d9fe97d26a6d7395f7e04e3b1e5` | — |

The machine-readable source of these bindings is
`config/dta-v21/historical-v2-bindings.v1.json`. The offline verifier requires
the exact ordered path set, exact raw bytes, canonical JSON semantics where
recorded, historical identity, seal, evaluation result, and live terminal.

## Startup disposition

`BASELINE_VERIFIED`: begin PR-A from the exact current main. Do not rerun or
edit DTA v2 evidence. Do not enter Provider, Docker, fault injection, live
Runbook, capture, development evaluation, or held-out execution in PR-A.

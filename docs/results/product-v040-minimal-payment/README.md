# Product v0.4 minimal Payment live acceptance

Status: implementation in progress; no live acceptance claim.

The active contract is [the saved Goal](../../goals/EcomSRE_Product_v0.4_Minimal_Payment_Live_Acceptance_Goal.md), SHA-256 `290e2f2c7948522f3f642f16696e73fc77c5535374be992e5f2ec2ff5db5337b`.
Starting main: `cc941b51cbff9287b876be49652cd0ad83030474` (PRs #91–#94 merged).

## Evidence scope

- Read scope: current Product, pinned upstream source, historical #95–#101 implementation and results as read-only references, exact local Docker inventory.
- Write scope: the saved Goal, dedicated minimal-payment scripts/configuration/tests/verifier/results, and narrowly necessary Product adapters. Core diagnosis rules and thresholds remain unchanged.
- Frozen scope: existing historical evidence, registry and historical-binding commitments, pinned upstream commit `1755859a9de82c2e5e225be68abc401a5ebf2b4f`.
- Final repository scope: every tracked change against the starting main, including generated public results. Private telemetry and control credentials stay outside Git.

Each live engineering attempt must preserve its failure or success and exact cleanup result. Historical attempts grant no resource or mutation authority. Provider calls must remain zero.

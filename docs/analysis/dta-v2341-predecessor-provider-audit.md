# DTA v2.3.4.1 Predecessor Provider Audit

## Frozen boundary

PR #72 remains the valid blocked predecessor at
`BLOCKED_DTA_V234_PROVIDER`. Its one Provider smoke, 22 requests, 22 responses,
12 protocol-repair requests, two real fixes, and zero fixed-study executions are
unchanged.

The successor audit binds every tracked blocker, repair, report, review, and
active-manifest byte in `config/dta-v2341/historical-results.v1.json`.

## Content-level limitation

The predecessor recorded raw request and response payloads below
`.local/dta-v234/provider-raw/smoke`. That scope is gitignored and is absent from
this checkout. Therefore this audit uses the tracked role dispositions,
diagnostics, repair records, hashes, and counts. It does **not** claim direct
inspection of the 22 raw Provider payloads or invent exact failing fields where
the tracked evidence does not identify them.

## Failure ownership transfer

| Role | Frozen predecessor outcome | v2.3.4.1 Runtime owner |
|---|---|---|
| `rt-001` hidden-known | parsed, then safe core collision | reconstruction validation context |
| `rt-003` hidden-known | full-object repairs exhausted | catalog objects, canonicalization, assembler |
| `rt-011` declarative-ready | predicate order noncanonical | deterministic assembler |
| `rt-012` declarative-ready | full-object validation repairs exhausted | catalog objects, canonicalization, assembler |
| `rt-014` engineering-required | mode/gap repairs exhausted | engineering-gap catalog and cardinality checks |
| `rt-015` duplicate | correct zero-call rejection | deterministic zero-call disposition |
| `rt-016` insufficient | correct zero-call rejection | deterministic zero-call disposition |

The successor changes only this registration-draft Provider boundary. It does
not rewrite the predecessor campaign or claim that missing raw payloads were
recovered.

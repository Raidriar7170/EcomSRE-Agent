# RCA A2 Applicability Decision

Status: `A2_APPLICABILITY_GATE_NOT_SUPPORTED_KEEP_A0`

A0 remains the authoritative fallback and the only active runtime.
A2 remains a typed Shadow recommendation only; no Gate was promoted.

## Frozen Gate Results

| Gate | Accepted | RCA100 Initial→Final | Rescue/Damage/Net | OB/SS Net | G0 Net Retained |
|---|---:|---:|---:|---:|---:|
| G0_A2_REFERENCE | false | 16→14 | 0/2/-2 | 26 | 1.000000 |
| G1_EXACT_LAYER_A2 | false | 16→14 | 0/2/-2 | 26 | 1.000000 |
| G2_ROOT_ELIGIBLE_LAYER_A2 | false | 16→14 | 0/2/-2 | 26 | 1.000000 |
| G3_CROSS_SOURCE_SUPPORTED_A2 | false | 16→16 | 0/0/0 | 2 | 0.076923 |
| G4_EXACT_LAYER_CROSS_SOURCE_A2 | false | 16→16 | 0/0/0 | 2 | 0.076923 |

G0/G1/G2 fail the frozen RCA100 safety boundary. G3/G4 avoid RCA100 damage but retain less than half of G0 OB/SS net rescue. The finite frontier is consumed; no sixth Gate or threshold search is authorized.

Runtime Gate logic reads only entity layers, service ancestry, Metrics rank/margin, downstream/topology relation, propagation disposition, exact non-Metrics evidence support, and typed fault ontology. It reads neither benchmark identity nor metric family.

No Provider was constructed. Live Shadow, promotion, and Regression were not executed.

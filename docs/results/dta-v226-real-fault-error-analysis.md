# DTA v2.2.6 Real-Fault Error Analysis

## Predecessor boundary

Every PR #67 Current run first failed at
`RESOURCE_COMPARISON_SET_BUILD / RESOURCE_COMPARISON_SET_EMPTY`. Executable
reproduction confirmed the source hypothesis: exact pre-dispatch metric
payloads differed between candidates, so the historical strict ambiguity gate
was empty even though both candidates retained unresolved
Resources-observable gaps.

The preserved PR #67 Flat bytes identify
`PROVIDER_ACTION_SELECTION / PROVIDER_OUTPUT_INVALID` as the broad first
failure. They do not retain enough information to recover a narrower split
among read-request binding and complete Diagnosis-shape subtypes; that boundary
remains `UNRECOVERABLE_FROM_PRESERVED_BYTES` and was not rewritten.

## Required questions

### Did Resource Comparison Set permit a bundle on unequal real metrics?

Yes. Strict ambiguity was `false` in all four new cases, while Current built a
comparison set of size two and dispatched exactly one target-complete Resources
bundle in every case.

### Did both final arms reach valid terminals?

Yes, `4 / 4` for each arm. Protocol, runner, and transport failures were all
zero. Valid terminal does not imply semantic correctness: all four
Model-directed terminals were valid `ABSTAIN` outcomes and exact count was zero.

### Did the Model-directed arm depend on alias order?

The observed terminal was invariant to the MAP_A/MAP_B swap: both fault maps
and both baseline maps ended `ABSTAIN`. That rules out an observed terminal
order effect in this tiny execution, but it does not demonstrate successful
root selection or establish general order invariance.

### Did Current cover both candidates in one read?

Yes. Every Current snapshot and both Current live shadows recorded one
multi-target Resources action, two target-equivalent reads, and complete
candidate coverage.

### Did live and snapshot Current agree?

Yes. The live and snapshot fault terminals agreed on an exact CPU-saturation
Diagnosis at the map-A Ad alias. The live and snapshot baseline terminals
agreed on exact No-Incident. Evidence-source agreement was also true for both
states.

### Did either arm produce premature No-Incident?

No. `premature_no_incident_count = 0`, and no baseline received a false-positive
fault terminal. Model-directed retrieval abstained rather than making either
error.

### Did shared terminalization remove the old Diagnosis-format confound?

Yes at the protocol boundary. Both arms reached the same runtime-owned terminal
shape with zero protocol failures; the Model-directed semantic loss came from
its evidence choices, not from constructing a full v2.1 Diagnosis object.

### Did Current reduce calls or tokens only while completing valid terminals?

Yes. Both arms were 4/4 protocol-valid, so the cost comparison was admissible.
Current used 4 Provider calls and 2,562 tokens versus Model-directed's 12 calls
and 9,116 tokens while also completing 4/4 exactly versus 0/4.

### What remains untested?

The physical fault was still always Ad CPU saturation. The study does not test
other physical target services, resource mechanisms, topology changes,
multi-fault episodes, production deployment, or statistical generalization.
The four cases are opaque paired renderings of two physical states, not four
independent fault episodes.

## New failure distributions

The final paired execution had empty failure-stage and safe-error-code
distributions. This does not erase the typed taxonomy; it means no final run
entered a failed terminal. Model-directed's empty-read rate was `1.0` and its
predicate-yield rate was `0.0`; Current's values were `0.0` and `2.5`.

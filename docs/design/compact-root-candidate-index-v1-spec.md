# Compact Root Candidate Index v1

Status: frozen deterministic development contract. PR #27 remains the frozen negative compact-retrieval reference at exact 64/103 and service 68/103.

`COMPACT_ROOT_CANDIDATE_INDEX_V1` selects at most 12 canonical candidates. Family S (SERVICE/WORKLOAD) reserves up to 8 slots, family N (NODE/CLUSTER/INFRASTRUCTURE) up to 2, and family D (DATABASE/CACHE/MESSAGE_QUEUE/NETWORK_COMPONENT) up to 2. Empty capacity refills in the single frozen order S→N→D→global.

Before family allocation, the index locks and deduplicates: the alert or nearest root; each source-backed entity's highest explicit SERVICE/WORKLOAD ancestor; Metrics Top-1 root/ancestor; and the earliest-anomaly root. The benchmark-independent service-completeness rule makes every exactly source-resolved SERVICE mandatory when at most 12 such services exist.

The only order is: mandatory descending; distinct direct/inherited source count descending; direct source count descending; explicit upstream descending; first anomaly ascending with missing last; topology distance ascending with missing last; Metrics rank ascending with missing last; canonical entity ref ascending. IDs `C01`…`C12` are assigned only after final ordering and map privately to canonical refs.

Model-facing serialization reuses one shared B0 bounded-evidence table and adds pipe rows such as `C01|S|checkout|src=ML|why=DA|m=2|t=+1200|rel=UP|ref=metric:0001,log:0003`. Each row includes at most two reason codes and two visible evidence refs. It omits ancestor paths, occurrence tables, topology neighborhoods, duplicated summaries, JSON candidate cards, and private mappings.

Token accounting uses the checked-in, hash-verified `tiktoken==0.13.0` `o200k_base` asset over sorted UTF-8 canonical JSON for the complete B0 and candidate-index requests, including system/user content, shared evidence, index where applicable, and output schema. These are offline full-request token counts, not Provider usage.


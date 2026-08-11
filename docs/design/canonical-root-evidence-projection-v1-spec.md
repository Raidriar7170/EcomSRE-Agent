# Canonical Root Evidence Projection v1

Status: frozen deterministic development contract. Classification: `CONSUMED_CROSS_BENCHMARK_DEVELOPMENT`, `DETERMINISTIC_RETRIEVAL_DEVELOPMENT`, `NOT_EXTERNAL_VALIDATION`, `NOT_PRIMARY_INFERENCE`.

`CANONICAL_ROOT_EVIDENCE_PROJECTION_V1` performs only Unicode NFC, trim, casefold, whitespace collapse, exact entity-ID resolution, explicit `same_as`, unique `(entity_type, normalized_name)`, explicit `parent`/`contains`, and explicit service-ancestor resolution. Ambiguous aliases fail closed. Fuzzy matching, edit distance, embeddings, case-specific rewrites, labels, and benchmark routing are forbidden.

Metrics resolve by exact ID, unique type/name, then exact service to a SERVICE entity. Logs use pod UID/name, container name, namespace and an explicit service field; hierarchy is only explicit container→pod→workload→service. Traces resolve `serviceName` exactly and retain span parent-child only as a relation. Events resolve the allowlisted involved-object kinds Pod, Deployment, StatefulSet, Node, and Service. Alerts resolve only exact resource identity fields.

Topology retains `contains`/`parent`, `calls`, `depends_on`/`dependency`, `hosts`, and `same_as`; other typed relations are counted and ignored. Directed calls and dependencies use the frozen RCA100 orientation: destination is upstream of source. Root-eligible layers are SERVICE, WORKLOAD, NODE, DATABASE, CACHE, MESSAGE_QUEUE, NETWORK_COMPONENT, CLUSTER, and INFRASTRUCTURE.

Every source-backed entity retains itself when root eligible and every explicit root ancestor within four parent hops. Private provenance binds original ref, candidate ref, full path, path length, source, and evidence ref. The candidate universe contains direct and inherited roots, upstream dependencies within two directed hops, same-component roots within two undirected hops, Metrics Top-6 with all root ancestors, and alert entities with all root ancestors. It never adds an unrelated catalog entity or a label-selected entity.

The one label-blind build is create-once outside Git and is bound by `projection-lock.json` to the clean implementation commit, frozen policy, source hashes, 163 opaque identities, output hashes, offline tokenizer, and `Provider calls = 0`. Evaluator imports and truth access occur only in the post-lock score command.

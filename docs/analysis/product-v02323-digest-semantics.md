# Product v0.2.3.2.3 Digest Semantics Audit

Terminal: `ECOMSRE_PRODUCT_V02323_DIGEST_SEMANTICS_PASS`

- Historical digest: `25d0fae060c396e63f338de886da97885c21508d265a94f1e45b999b5bc206f6`
- Surviving schema-9 raw digest: `c8dbe4a5c500c577988e433ef9921b31cc920983e39d458907e5481937561d37`
- Classified kind: `RAW_SQLITE_FILE_SHA256`
- Source field: `source_database_file_sha256`
- PR #83 source definition: `142dc1094926f18e789ece3668c34918f859b512:src/ecomsre/product/pilot/product_state_clone_v0232.py`

The historical value is the SHA-256 of the raw schema-8 SQLite file bytes. Those
bytes are lost. A reconstructed database may prove canonical logical equality,
but it must not claim the same SQLite page layout or raw-file identity. This
reconstruction/replay has no measured No-Fault or Knowledge-Loop authority.

# Product v0.2.2.2 Baseline Handoff

Status: `ECOMSRE_PRODUCT_V0222_BASELINE_HANDOFF_READY`

- active profile SHA: `b9577dfc4eaa933b62048bbcbd041ed470343f7c76255ab851cdcaeef60a7df2`
- capture bundle SHA: `4084941d8368c4f74ec2db95ac2215f36c9531367f9904b9b90cd653bceeea94`
- Candidate Set SHA: `f3aeaf272ab199c1284238c9e7785ec89f46b1cb54ad1608188a052c27f9d4de`
- operator decision SHA: `51effb280e9390d5619bf18fed80c2c158214db2dd98dcfce3634275125b8b5e`
- connector smoke SHA: `a3573782f56f5445db8920301e267840d9a1296e51027c0fda841bfd4bd303c2`
- active-profile restart proof SHA: `b2b0ea37d763316a7be9acb65899f2a7d1fddcdb7f222ca05f63bab742fda69b`
- service identity SHA: `a84e3f82180cd40024f9449096586b0ac94ceea8b492f59d58c58c28d7828d72`
- historical smoke identity SHA: `d310d31a00decfc65c9d55296ba4fdcc743106d7e8dae7488fe8ce48996da7b4`

## Known limitations

- No Baseline Readiness or Product Diagnosis was run in this Goal.
- The active profile is verified against the project-owned local Sandbox.
- Trace ID remains optional because one retained capture record omitted it.
- The current Product connector fails a window closed on any schema-invalid hit.

Recommended next Goal: `Product v0.2.3 Fresh Baseline Readiness and No-Fault Acceptance`.

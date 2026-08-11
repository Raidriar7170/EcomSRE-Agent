# Root Evidence Projection v1 人工审阅摘要

终态：`ROOT_EVIDENCE_PROJECTION_GATE_NOT_PASSED_STOP_LLM_RCA_OPTIMIZATION`。

RCA100 投影 exact/service 覆盖为 103/103 与 103/103；最终索引 exact/service Recall@12 为 79/103 与 79/103。OB/SS 投影与索引分别为 60/60、60/60。

真实 `o200k_base` 完整输入 token 比率 mean/median/p95/max 为 1.085038/1.074670/1.123851/1.124939。

边界：一次冻结 policy、一次 label-blind build、一次锁后评分；Provider calls = 0；没有 live、Regression、RE2-TT 或 external claim。

# Strong Single Hierarchical Live Development — Human Brief

结论标记：

`HIERARCHICAL_STRONG_SINGLE_LIVE_TUNE_NOT_PASSED`

本阶段比较 B0 Baseline Strong Single 与 H1 Strong Single Hierarchical。
两臂逐 case 独立调用一次同一模型，交替 arm 顺序，共享相同输出 schema
和原始 bounded evidence；没有后处理 override、Specialist 或 LLM Fusion。

TUNE：`HIERARCHICAL_STRONG_SINGLE_LIVE_TUNE_NOT_PASSED`。未执行（TUNE Gate 未通过或尚未进入 Regression）。

这是已消费 development data 上的工程评估，不是 fresh external validation，
也不支持把描述性 bootstrap / McNemar 结果表述为 external superiority。
公开材料仅包含 aggregate；case-level 预测、答案、实体、原始证据和私有路径
均未提交。

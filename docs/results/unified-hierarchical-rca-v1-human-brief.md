# Unified Hierarchical RCA v1 Human Brief

## 结论

本文件是对先前无效 GT-derived / 未冻结分析尝试的 append-only 修正版；A0–A5 阈值未改变。


冻结决策为 `A0 / STRONG_SINGLE_HIERARCHICAL`。实现对全部 463 条 consumed-development 记录完成离线重放，与 Phase G counterfactual 逐条一致；RCA100 为 `16/103` → `16/103`，无 override、无 Root Damage、无 Root Rescue。

## 为什么选择 A0

A2 在 RCA100 产生负净收益且 Damage Rate 超限；A3/A4 没有在 RCA100 产生正净收益；A5 的非冗余 eligible coverage 与 RCA100 oracle 净收益不足。冻结规则因此要求回退 A0，而不是继续追加实验。

## 实现边界

Runtime 输出 canonical entity layer、typed fault ontology、root provenance 与 decision reason；Final Root 永远保持 Strong Single Initial Root。未保留 Metrics 或 Agent override，也未进行 Provider 构造、调用、新数据访问或 RE2-TT 访问。

## 证据等级

这是 consumed cross-benchmark development 的 post-hoc attribution 与 offline replay，不是 external validation，也不是 primary inference。下一步若获单独授权，应是一次有界 live development evaluation，而不是新的 attribution candidate。

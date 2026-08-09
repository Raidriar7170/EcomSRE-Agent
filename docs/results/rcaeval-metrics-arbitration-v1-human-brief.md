# Human Brief：Metrics Arbitration v1

当前状态：`IMPLEMENTED_AWAITING_PROVIDER_CAPACITY_PREFLIGHT`。

本阶段实现了一个独立、Root-only 的 M3 仲裁器：每个 case 仅调用一次 Strong Single；当 Initial Root 不在 Metrics Top-2 且归一化分差不低于 0.25 时，确定性切换到 Metrics Top-1。Indicator 始终保留 Initial 值，Specialist 与 Fusion LLM 调用均为 0。

三套冻结 fixture 的零 Provider 回放均精确复现：Final Root 都为 57，Root rescue 分别为 8、6、12，damage 都为 0。

TUNE 尚未执行。

Regression 尚未执行；只有 TUNE Gate 通过后才允许运行。

结论边界：主要算法结论只使用同一 run 的 Initial→Final；历史 Strong Single 仅标记为 `CROSS_RUN_CONTEXTUAL_BASELINE`。这是已消费 OB/SS development 证据，不是外部验证；未访问 TT，也不主张生产泛化。

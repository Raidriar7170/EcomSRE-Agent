# DTA v2.1 PR-D Capture Calibration Limitations

The accepted Ad CPU calibration established a safe, attributable, measurable
process-CPU signal for the bounded `adHighCpu=on` capture. Its evaluator record
has `business_impact_observed=false`.

This calibration is not evidence of business-SLI degradation and must not be
used to claim user-visible Ad impact. It supports only the bounded CPU-signal
capture and replay contract used by this PR-D evaluation dataset.

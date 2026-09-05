"""Bounded metrics derived after durable state transitions; exporter failure is inert."""

from ecomsre.product.remediation.attempt_contracts import RemediationAttemptV1
from ecomsre.product.remediation.execution_contracts import (
    ExecutorDispatchV1,
    RecoveryEvaluationV1,
    StepReceiptV1,
)
from ecomsre.product.telemetry.metrics import ProductMetricsV1


class ProductRemediationMetricsV1(ProductMetricsV1):
    def render(self) -> str:
        base = super().render()
        try:
            return base + self._remediation_render()
        except Exception:
            return base

    def _remediation_render(self) -> str:
        lines: list[str] = []
        with self.store.connect() as connection:
            for suffix, table in (
                ("candidates", "remediation_candidates"),
                ("approvals", "remediation_approvals"),
                ("authorizations", "remediation_authorizations"),
                ("write_intents", "remediation_write_intents"),
            ):
                value = connection.execute(f"SELECT count(*) FROM {table}").fetchone()[
                    0
                ]
                lines.append(f"ecomsre_remediation_{suffix}_total {value}")
            attempts = [
                RemediationAttemptV1.model_validate_json(row[0])
                for row in connection.execute(
                    "SELECT payload_json FROM remediation_attempts"
                )
            ]
            receipts = [
                StepReceiptV1.model_validate_json(row[0])
                for row in connection.execute(
                    "SELECT payload_json FROM remediation_step_receipts"
                )
            ]
            evaluations = [
                RecoveryEvaluationV1.model_validate_json(row[0])
                for row in connection.execute(
                    "SELECT payload_json FROM remediation_recovery_evaluations"
                )
            ]
            dispatches = [
                ExecutorDispatchV1.model_validate_json(row[0])
                for row in connection.execute(
                    "SELECT payload_json FROM remediation_executor_dispatches"
                )
            ]
        lines.append(
            f"ecomsre_remediation_forward_steps_total {sum(r.outcome == 'APPLIED' for r in receipts)}"
        )
        lines.append(
            f"ecomsre_remediation_outcome_unknown_total {sum(a.state.value == 'OUTCOME_UNKNOWN' for a in attempts)}"
        )
        terminals = sorted(
            {a.terminal.value for a in attempts if a.terminal is not None}
        )
        for terminal in terminals:
            count = sum(a.state.value == terminal for a in attempts)
            lines.append(
                f'ecomsre_remediation_attempt_terminals_total{{terminal="{terminal}"}} {count}'
            )
        for outcome in ("PASS", "FAIL"):
            count = sum(e.outcome == outcome for e in evaluations)
            lines.append(
                f'ecomsre_remediation_verification_total{{outcome="{outcome}"}} {count}'
            )
        by_id = {a.attempt_id: a for a in attempts}
        durations = {
            "execution": [r.elapsed_ms / 1000 for r in receipts],
            "queue_wait": [
                (d.created_at - by_id[d.attempt_id].created_at).total_seconds()
                for d in dispatches
            ],
            "verification": [
                (e.created_at - r.ended_at).total_seconds()
                for e in evaluations
                for r in receipts
                if r.attempt_id == e.attempt_id
            ],
        }
        for name, values in durations.items():
            metric = f"ecomsre_remediation_{name}_seconds"
            for bound in (0.1, 0.5, 1, 5, 15, 30, 120, 600):
                lines.append(
                    f'{metric}_bucket{{le="{bound}"}} {sum(v <= bound for v in values)}'
                )
            lines.extend(
                (
                    f'{metric}_bucket{{le="+Inf"}} {len(values)}',
                    f"{metric}_count {len(values)}",
                    f"{metric}_sum {sum(values)}",
                )
            )
        return "\n".join(lines) + "\n"

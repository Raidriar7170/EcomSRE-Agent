from __future__ import annotations

from pathlib import Path

import pytest

from scripts.ci.verify_dta_v21_pr_f_protocol import (
    _verify_pr_f_targets_do_not_reach_held_out_execution,
    verify_pr_f_protocol,
)


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_public_pr_f_protocol_verifier_passes_without_private_evidence() -> None:
    protocol = verify_pr_f_protocol(REPO_ROOT)

    assert protocol.fault_impact_kind == "RESOURCE_ONLY"
    assert protocol.business_sli_role == "NON_REGRESSION_GUARDRAIL"
    assert protocol.user_visible_recovery_claimed is False


def test_pr_f_target_cannot_depend_on_held_out_execution_or_scoring() -> None:
    dangerous_makefiles = (
        "dta-v21-pr-f-live: dta-v21-held-out-execute\n\t@true\n",
        (
            "evil: dta-v21-held-out-execute\n"
            "\t@true\n"
            "dta-v21-pr-f-live: evil\n"
            "\t@true\n"
        ),
        "dta-v21-pr-f-live:\n\t$(DTA_V21_HELD_OUT_CLI) execute\n",
        (
            "dta-v21-pr-f-live:\n"
            "\t$(DTA_V21_HELD_OUT_CLI) \\\n"
            "\t  execute\n"
        ),
    )
    for makefile in dangerous_makefiles:
        with pytest.raises(ValueError, match="held-out execution or scoring"):
            _verify_pr_f_targets_do_not_reach_held_out_execution(makefile)

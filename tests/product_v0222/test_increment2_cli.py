from __future__ import annotations

import json
from pathlib import Path

from ecomsre.product.cli import main
from ecomsre.product.connectors.opensearch_candidates_v0222 import (
    OpenSearchOperatorDecisionLedgerV0222,
    build_profile_candidate_set_v0222,
)
from tests.product_v0222.test_increment2_candidates import (
    CAPTURE_SHA,
    _ambiguous_components,
)


def test_profile_candidate_and_selection_cli(
    tmp_path: Path,
    monkeypatch: object,
    capsys: object,
) -> None:
    candidate_set = build_profile_candidate_set_v0222(
        capture_bundle_sha256=CAPTURE_SHA,
        components=_ambiguous_components(),
    )
    candidate_path = tmp_path / "candidate-set.json"
    candidate_path.write_text(candidate_set.model_dump_json(), encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # type: ignore[attr-defined]

    assert main(
        [
            "product-v0222",
            "profile-candidates",
            "--candidate-set",
            str(candidate_path),
        ]
    ) == 0
    listed = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert listed["candidate_set_sha256"] == candidate_set.candidate_set_sha256

    assert main(
        [
            "product-v0222",
            "profile-select",
            "--candidate-set",
            str(candidate_path),
            "--candidate",
            "P00",
            "--reviewer",
            "Raidriar",
            "--note",
            "Select P00 for holdout verification.",
        ]
    ) == 0
    selected = json.loads(capsys.readouterr().out)  # type: ignore[attr-defined]
    assert selected["selected_candidate_alias"] == "P00"
    ledger = OpenSearchOperatorDecisionLedgerV0222.model_validate_json(
        (
            tmp_path
            / "config/product-v0222/opensearch/operator-decision.json"
        ).read_text(encoding="utf-8")
    )
    assert len(ledger.decisions) == 1
    assert ledger.decisions[0].reviewer == "Raidriar"

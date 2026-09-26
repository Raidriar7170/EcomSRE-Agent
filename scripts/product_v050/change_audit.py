"""Private operator readback audit projected through the existing Changes API."""

from datetime import UTC, datetime
import json
from uuid import uuid4

from ecomsre.product.changes import ChangeEventCreateV1
from ecomsre_live_sandbox.knowledge_v030 import _local_json
from scripts.product_v050.live_environment import save
from ecomsre_live_sandbox.contracts import canonical_sha256


def observed(controller, state):
    receipt = controller.read(state)
    document = json.loads(controller.flag_file.read_text())
    readback = _local_json(f"{controller.endpoints.flag_control}/read")
    if (
        not isinstance(readback, dict)
        or readback.get("flags") != document.get("flags")
        or canonical_sha256(document) != receipt["document_sha256"]
    ):
        raise ValueError("ACTUAL_CONFIGURATION_READBACK_DIFFERS")
    return dict(document=document, readback=readback, receipt=receipt)


def apply(campaign, *, before_state, after_state, root, phase):
    """Publish completed factual changes including restoration, never a diagnosis."""
    audit_path = root / (phase + "-configuration-audit.json")
    if audit_path.exists() or (root / (phase + "-configuration-intent.json")).exists():
        raise ValueError("CONFIGURATION_OPERATION_ALREADY_ATTEMPTED")
    before = observed(campaign.controller, before_state)
    opaque = uuid4().hex
    audit = dict(
        before=before, operation_id=opaque, started_at=datetime.now(UTC).isoformat()
    )
    save(root / (phase + "-configuration-intent.json"), audit)
    try:
        receipt = campaign.controller.apply(after_state)
        after = observed(campaign.controller, after_state)
        audit["after"] = after
        if before["document"] == after["document"]:
            raise ValueError("NO_ACTUAL_CONFIGURATION_CHANGE")
        request = ChangeEventCreateV1.model_validate(
            dict(
                service_id=campaign.service_ids["payment"],
                category="CONFIGURATION",
                occurred_at=after["receipt"]["observed_at"],
                revision=opaque,
                external_change_id=opaque,
                summary="Observed local configuration rollout",
            )
        ).model_dump(mode="json")
        audit["request"] = request
        save(root / (phase + "-configuration-observed.json"), audit)
        response = campaign.client.post(
            f"/v1/environments/{campaign.env}/changes", json=request
        )
        audit["response"] = dict(status=response.status_code, body=response.json())
        if response.status_code not in {200, 201}:
            raise ValueError("CHANGE_INGESTION_FAILED:" + str(response.status_code))
        return receipt
    except BaseException as exc:
        audit["error"] = dict(type=type(exc).__name__, message=str(exc)[:240])
        raise
    finally:
        save(audit_path, audit)

"""Fixed read-only peer witness from the exact owned gateway process namespace."""

import json
import os
from pathlib import Path
import httpx

profile = json.loads(
    Path(os.environ["ECOMSRE_REMEDIATION_PRIVATE_PROFILE"]).read_bytes()
)
with httpx.Client(trust_env=False, timeout=10) as client:
    response = client.get(
        profile["flag_control_url"] + "/read",
        headers={"X-EcomSRE-Peer-Witness": "GATEWAY_READ_ONLY_PROBE"},
    )
    response.raise_for_status()
print("GATEWAY_READ_ONLY_PEER_WITNESS_COMPLETE")

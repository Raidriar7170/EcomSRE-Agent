"""Explicit v3 lifecycle comparisons; raw inspect dictionaries are never rewritten."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from scripts.product.qualification_v040.guard import container_fixed, require, sha

POLICY_ID = "DOCKER_STAGE_AWARE_FINGERPRINT_V3"
PROBE_ROLE = "probe/container/kafka-volume-probe"
START_STAGE = "AFTER_PROBE_START"
STOP_STAGE = "AFTER_PROBE_STOP"


def raw_fingerprint(row: dict[str, Any]) -> dict[str, Any]:
    return deepcopy(
        {
            **container_fixed(row),
            "Name": row["Name"],
            "Created": row["Created"],
            "Path": row.get("Path"),
            "Args": row.get("Args"),
            "Driver": row.get("Driver"),
            "Platform": row.get("Platform"),
            "ImageManifestDescriptor": row.get("ImageManifestDescriptor"),
            "AppArmorProfile": row.get("AppArmorProfile"),
            "ProcessLabel": row.get("ProcessLabel"),
            "MountLabel": row.get("MountLabel"),
        }
    )


def identity(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "container_id": row["Id"],
        "created": row["Created"],
        "started_at": row["State"]["StartedAt"],
        "restart_count": row["RestartCount"],
        "pid": row["State"]["Pid"],
    }


def validate_oom(
    proof: dict[str, Any], row: dict[str, Any], stage: str, daemon: dict[str, Any]
) -> None:
    require(
        proof.get("stage") == stage and proof.get("daemon") == daemon,
        "OOM_EVIDENCE_CONTEXT_DRIFT",
    )
    require(
        proof.get("identity_before") == proof.get("identity_after") == identity(row),
        "OOM_EVIDENCE_IDENTITY_DRIFT",
    )
    require(
        row["State"]["Running"] and row["RestartCount"] == 0, "OOM_PROCESS_NOT_STABLE"
    )
    require(proof.get("first") == proof.get("second"), "OOM_EVIDENCE_RACE")
    observed = proof["first"]
    require(
        observed.get("cgroup") == "0::/"
        and observed.get("cgroup_fs") == "cgroup2"
        and observed.get("memory_max") == "max"
        and observed.get("memory_oom_group") == "0"
        and observed.get("oom_score_adj") == "0"
        and observed.get("legacy_oom_control_present") is False,
        "OOM_POLICY_MISMATCH",
    )
    require(
        observed.get("pid") == 1
        and observed.get("start_time", 0) > 0
        and observed.get("uids") == [1000] * 4
        and observed.get("gids") == [1000] * 4
        and observed.get("groups") in ([], [1000])
        and observed.get("cap_eff") == 0
        and observed.get("cap_prm") == 0
        and observed.get("no_new_privs") == 1,
        "OOM_PROCESS_IDENTITY_INCOMPLETE",
    )
    require(
        row["HostConfig"]["OomScoreAdj"] == 0
        and row["HostConfig"]["Memory"] == 0
        and row["HostConfig"]["MemorySwap"] == 0,
        "OOM_INSPECT_POLICY_MISMATCH",
    )


class ProbeLifecycle:
    """One immutable birth binding, explicit start/stop events, fresh measurement per running stage."""

    def __init__(
        self, daemon: dict[str, Any], none_network_id: str = "none-id"
    ) -> None:
        self.daemon = deepcopy(daemon)
        self.none_network_id = none_network_id
        self.endpoint: dict[str, Any] | None = None
        self.birth: dict[str, Any] | None = None
        self.last_raw: dict[str, Any] | None = None
        self.last_identity: dict[str, Any] | None = None
        self.started = False
        self.stopped = False
        self.stop_intent: dict[str, Any] | None = None
        self.last_proof: dict[str, Any] | None = None
        self.events: list[dict[str, Any]] = []

    def verify_endpoint(self, row: dict[str, Any]) -> None:
        require(
            set(row["NetworkSettings"]["Networks"]) == {"none"},
            "PROBE_NETWORK_SET_DRIFT",
        )
        endpoint = row["NetworkSettings"]["Networks"]["none"]
        require(
            all(
                not endpoint.get(k)
                for k in ("IPAddress", "GlobalIPv6Address", "MacAddress")
            ),
            "PROBE_NETWORK_ADDRESS_DRIFT",
        )
        if self.endpoint is not None:
            expected = deepcopy(self.endpoint)
            if not row["State"]["Running"]:
                expected["EndpointID"] = ""
            require(endpoint == expected, "PROBE_ENDPOINT_IDENTITY_DRIFT")
        else:
            require(not row["State"]["Running"], "PROBE_ENDPOINT_UNBOUND")
            require(
                not endpoint.get("EndpointID")
                and endpoint.get("NetworkID") in ("", self.none_network_id),
                "PROBE_PRESTART_ENDPOINT_DRIFT",
            )

    def admit(
        self, row: dict[str, Any], stage: str, proof: dict[str, Any] | None
    ) -> dict[str, Any]:
        raw = raw_fingerprint(row)
        require("OomKillDisable" in raw["HostConfig"], "OOM_FIELD_MISSING")
        value = raw["HostConfig"]["OomKillDisable"]
        require(value is False or value is None, "OOM_DISABLE_FORBIDDEN")
        require(
            row["RestartCount"] == 0 and not row["State"].get("OOMKilled"),
            "PROBE_RESTART_OR_OOM",
        )
        if self.birth is None:
            require(
                stage == "AFTER_COPYUP_CREATE"
                and not row["State"]["Running"]
                and value is False,
                "PROBE_BIRTH_STAGE_DRIFT",
            )
            self.verify_endpoint(row)
            self.birth, self.last_raw = deepcopy(raw), deepcopy(raw)
            self.last_identity = identity(row)
            return raw
        assert self.last_raw is not None and self.last_identity is not None
        comparable = deepcopy(raw)
        comparable["HostConfig"]["OomKillDisable"] = self.birth["HostConfig"][
            "OomKillDisable"
        ]
        require(comparable == self.birth, "PROBE_SECURITY_FINGERPRINT_DRIFT")
        transition = value is not self.last_raw["HostConfig"]["OomKillDisable"]
        if row["State"]["Running"]:
            require(not self.stopped, "PROBE_UNAUTHORIZED_RESTART")
            if not self.started:
                require(stage == START_STAGE, "PROBE_START_STAGE_DRIFT")
            else:
                require(
                    identity(row) == self.last_identity, "PROBE_PROCESS_IDENTITY_DRIFT"
                )
            require(proof is not None, "OOM_EVIDENCE_MISSING")
            assert proof is not None
            validate_oom(proof, row, stage, self.daemon)
            require(
                not transition or not self.started and stage == START_STAGE,
                "OOM_UNAUTHORIZED_TRANSITION",
            )
            self.started = True
            self.last_proof = deepcopy(proof)
        elif self.started:
            require(self.stop_intent is not None, "PROBE_STOP_INTENT_MISSING")
            assert self.stop_intent is not None
            require(
                stage == self.stop_intent["stage"].replace("BEFORE_", "AFTER_", 1)
                or self.stopped,
                "PROBE_STOP_STAGE_DRIFT",
            )
            require(
                row["State"]["StartedAt"] == self.last_identity["started_at"]
                and row["State"]["Pid"] == 0
                and not transition,
                "PROBE_STOP_IDENTITY_DRIFT",
            )
            # OOM evidence here is the last verified pre-stop observation; no stopped-process measurement is claimed.
            require(
                self.last_proof is not None
                and self.stop_intent["proof_sha256"] == sha(self.last_proof),
                "PROBE_STOP_OOM_PROOF_DRIFT",
            )
            self.stopped = True
        else:
            require(
                not transition and identity(row) == self.last_identity,
                "PROBE_PRESTART_DRIFT",
            )
        if transition:
            self.events.append(
                {
                    "stage": stage,
                    "field": "HostConfig.OomKillDisable",
                    "before": self.last_raw["HostConfig"]["OomKillDisable"],
                    "after": value,
                    "raw_before_sha256": sha(self.last_raw),
                    "raw_after_sha256": sha(raw),
                    "proof_sha256": sha(proof),
                    "semantic_equal": True,
                }
            )
        if self.endpoint is None and row["State"]["Running"]:
            require(stage == START_STAGE, "PROBE_ENDPOINT_BIND_STAGE_DRIFT")
            endpoint = row["NetworkSettings"]["Networks"].get("none", {})
            require(
                endpoint.get("NetworkID") == self.none_network_id
                and bool(endpoint.get("EndpointID")),
                "PROBE_ENDPOINT_UNBOUND",
            )
            self.endpoint = deepcopy(endpoint)
        self.verify_endpoint(row)
        self.last_raw = deepcopy(raw)
        if row["State"]["Running"]:
            self.last_identity = identity(row)
        return comparable

    def authorize_stop(
        self, row: dict[str, Any], proof: dict[str, Any]
    ) -> dict[str, Any]:
        require(
            self.started and not self.stopped and self.last_proof == proof,
            "PROBE_STOP_EVIDENCE_STALE",
        )
        require(
            proof["stage"] in {"BEFORE_PROBE_STOP", "BEFORE_OWNED_STOP"},
            "PROBE_STOP_STAGE_DRIFT",
        )
        validate_oom(proof, row, proof["stage"], self.daemon)
        self.stop_intent = {
            "container_identity": identity(row),
            "proof_sha256": sha(proof),
            "stage": proof["stage"],
            "formal_authority": False,
        }
        return deepcopy(self.stop_intent)

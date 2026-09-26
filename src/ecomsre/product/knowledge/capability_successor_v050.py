"""One-round, harness-owned same-environment deployment compatibility.

No Docker or Provider calls occur here. Inputs are retained verification objects;
the live harness must obtain them from the owned deployment before installation.
Fixture records are accepted only for environments using exclusively fixture sources.
"""

import json
from typing import Any, Literal

from pydantic import Field

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha
from ecomsre.product.contracts import EnvironmentRecordV1
from ecomsre.product.environment.capabilities import EnvironmentCapabilityMatrixV1
from ecomsre.product.environment.repository import EnvironmentRepositoryV1
from ecomsre.product.investigation.contracts import StrictModel
from ecomsre.product.knowledge import selection_lock_v050


class DeploymentV050(StrictModel):
    mode: Literal["FIXTURE", "OWNED_LOCAL"]
    deployment_id: str = Field(min_length=1)
    environment: EnvironmentRecordV1
    # Raw births/authority are distinct from the semantics compared below.
    resource_births: dict[str, Any] = Field(min_length=1)
    runtime_binding: dict[str, str]
    images: dict[str, str] = Field(min_length=1)
    service_mapping: dict[str, str] = Field(min_length=1)
    actual_queries: dict[str, str] = Field(min_length=1)
    units: dict[str, str] = Field(min_length=1)
    windows_and_sampling: dict[str, Any] = Field(min_length=1)
    resource_limits: dict[str, Any] = Field(min_length=1)
    trust_boundary: dict[str, Any] = Field(min_length=1)
    connector_semantics: dict[str, Any] = Field(min_length=1)


def semantic_deployment(deployment):
    value = deployment.model_dump(mode="json")
    for field in ("deployment_id", "resource_births", "runtime_binding"):
        value.pop(field)
    environment = value["environment"]
    # Only presentation timestamps can differ. Connector queries/limits remain.
    for field in ("created_at", "updated_at"):
        environment.pop(field)
    runtime = [
        c for c in environment["connector_configs"] if c["kind"] == "PILOT_RUNTIME"
    ]
    if runtime:
        if len(runtime) != 1 or set(deployment.runtime_binding) != {
            "snapshot_ref",
            "authority_sha256",
        }:
            raise ValueError("exact Runtime deployment binding required")
        for field, actual in deployment.runtime_binding.items():
            if runtime[0]["settings"].pop(field) != actual:
                raise ValueError("Runtime connector and deployment identity differ")
    elif deployment.runtime_binding:
        raise ValueError("unexpected Runtime deployment identity")
    return value


def validate_pair(old, new, old_deployment, new_deployment):
    old = EnvironmentCapabilityMatrixV1.model_validate(old)
    new = EnvironmentCapabilityMatrixV1.model_validate(new)
    before, after = (
        DeploymentV050.model_validate(v) for v in (old_deployment, new_deployment)
    )
    if old.capability_sha256 == new.capability_sha256:
        raise ValueError(
            "successor must retain distinct original capability identities"
        )
    if old.model_dump(
        mode="json", exclude={"verified_at", "capability_sha256"}
    ) != new.model_dump(mode="json", exclude={"verified_at", "capability_sha256"}):
        raise ValueError("capability semantics differ beyond verified_at")
    if (
        old.environment_id != before.environment.environment_id
        or new.environment_id != after.environment.environment_id
    ):
        raise ValueError("deployment environment differs")
    if new.verified_at <= old.verified_at:
        raise ValueError("successor verification must follow original verification")
    if semantic_deployment(before) != semantic_deployment(after):
        raise ValueError("deployment query/sampling/limits/trust semantics differ")
    if before.mode == "FIXTURE" and any(
        c.kind.value != "FIXTURE" for c in before.environment.connector_configs
    ):
        raise ValueError("fixture successor cannot authorize live connectors")
    return old, new, before, after


def load(connection, environment_id):
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE name='knowledge_capability_successor_v050'"
    ).fetchone():
        return None
    row = connection.execute(
        "SELECT payload_json FROM knowledge_capability_successor_v050 WHERE environment_id=?",
        (environment_id,),
    ).fetchone()
    if row is None:
        return None
    payload = json.loads(row[0])
    if payload["sha256"] != sha({k: v for k, v in payload.items() if k != "sha256"}):
        raise ValueError("capability successor binding differs")
    cursor = payload
    seen = {payload["sha256"]}
    while (
        "unused_predecessor_sha256" in cursor or "repair_predecessor_sha256" in cursor
    ):
        predecessor = cursor.get(
            "unused_predecessor_sha256", cursor.get("repair_predecessor_sha256")
        )
        history = connection.execute(
            "SELECT payload_json FROM knowledge_capability_successor_history_v050 WHERE sha256=?",
            (predecessor,),
        ).fetchone()
        if history is None:
            raise ValueError("retained successor predecessor absent")
        previous = json.loads(history[0])
        if (
            previous["sha256"] != predecessor
            or previous["sha256"]
            != sha({k: v for k, v in previous.items() if k != "sha256"})
            or previous["old"] != payload["old"]
            or previous["old_deployment"] != payload["old_deployment"]
        ):
            raise ValueError("retained successor predecessor differs")
        if previous["sha256"] in seen:
            raise ValueError("retained successor history cycle")
        seen.add(previous["sha256"])
        validate_pair(
            previous["old"],
            previous["new"],
            previous["old_deployment"],
            previous["new_deployment"],
        )
        if "repair_predecessor_sha256" in cursor:
            from ecomsre.product.knowledge.control_repair_v050 import (
                read as repair_contract,
            )

            repair = repair_contract(connection, environment_id)
            if repair is None or cursor.get("repair_contract_sha256") != sha(repair):
                raise ValueError("repair deployment authority differs")
            if cursor.get("retained_development") != {
                "matrix": previous["new"],
                "deployment": previous["new_deployment"],
            }:
                raise ValueError("consumed development identity differs")
            validate_pair(
                previous["new"],
                cursor["new"],
                previous["new_deployment"],
                cursor["new_deployment"],
            )
            if "execution_resume_sha256" in cursor:
                from ecomsre.product.knowledge.control_repair_v050 import (
                    read_execution_resume,
                )

                resume = read_execution_resume(connection, environment_id)
                if (
                    resume is None
                    or sha(resume) != cursor["execution_resume_sha256"]
                    or resume["predecessor_sha256"] != previous["sha256"]
                    or cursor.get("earlier_development")
                    != previous.get("retained_development")
                ):
                    raise ValueError("execution resume deployment differs")
                earlier = cursor["earlier_development"]
                validate_pair(
                    earlier["matrix"],
                    cursor["new"],
                    earlier["deployment"],
                    cursor["new_deployment"],
                )
        cursor = previous
    validate_pair(
        payload["old"],
        payload["new"],
        payload["old_deployment"],
        payload["new_deployment"],
    )
    return payload


def install(
    store,
    *,
    old,
    new,
    old_deployment,
    new_deployment,
    unused_predecessor_sha256=None,
    repair_predecessor_sha256=None,
):
    old, new, before, after = validate_pair(old, new, old_deployment, new_deployment)
    value = dict(
        round_id="ecomsre-v050-final-learning-closure-v1",
        old=old.model_dump(mode="json"),
        new=new.model_dump(mode="json"),
        old_deployment=before.model_dump(mode="json"),
        new_deployment=after.model_dump(mode="json"),
    )
    if unused_predecessor_sha256 is not None:
        value["unused_predecessor_sha256"] = unused_predecessor_sha256
    if repair_predecessor_sha256 is not None and unused_predecessor_sha256 is not None:
        raise ValueError("one explicit predecessor mode required")
    with store.connect() as c:
        c.execute("BEGIN IMMEDIATE")
        current = c.execute(
            "SELECT payload_json FROM environment_capability_matrices WHERE environment_id=?",
            (old.environment_id,),
        ).fetchone()
        if (
            current is None
            or EnvironmentCapabilityMatrixV1.model_validate_json(current[0]) != new
        ):
            raise ValueError(
                "successor must be verified by normal environment workflow"
            )
        if EnvironmentRepositoryV1(store).get(old.environment_id) != after.environment:
            raise ValueError(
                "successor must bind the current environment configuration"
            )
        prior = load(c, old.environment_id)
        execution_resume = None
        if repair_predecessor_sha256 is not None:
            from ecomsre.product.knowledge.control_repair_v050 import (
                read as repair_contract,
            )

            repair = repair_contract(c, old.environment_id)
            predecessor = prior
            if (
                prior
                and prior.get("repair_predecessor_sha256") == repair_predecessor_sha256
            ):
                retained = c.execute(
                    "SELECT payload_json FROM knowledge_capability_successor_history_v050 WHERE sha256=?",
                    (repair_predecessor_sha256,),
                ).fetchone()
                predecessor = json.loads(retained[0]) if retained else None
            if (
                repair is None
                or predecessor is None
                or predecessor["sha256"] != repair_predecessor_sha256
            ):
                raise ValueError(
                    "one authorized repair deployment predecessor required"
                )
            if "repair_predecessor_sha256" in predecessor:
                from ecomsre.product.knowledge.control_repair_v050 import (
                    read_execution_resume,
                    verify_sources,
                )

                execution_resume = read_execution_resume(c, old.environment_id)
                if (
                    execution_resume is None
                    or execution_resume["predecessor_sha256"] != predecessor["sha256"]
                    or "execution_resume_sha256" in predecessor
                ):
                    raise ValueError(
                        "one authorized execution resume predecessor required"
                    )
                verify_sources(repair, execution_resume)
                earlier = predecessor["retained_development"]
                validate_pair(earlier["matrix"], new, earlier["deployment"], after)
                value.update(
                    execution_resume_sha256=sha(execution_resume),
                    earlier_development=earlier,
                )
            validate_pair(predecessor["new"], new, predecessor["new_deployment"], after)
            value.update(
                repair_predecessor_sha256=repair_predecessor_sha256,
                repair_contract_sha256=sha(repair),
                retained_development={
                    "matrix": predecessor["new"],
                    "deployment": predecessor["new_deployment"],
                },
            )
        value["sha256"] = sha(value)
        if prior is not None and prior == value:
            return value["sha256"]
        if prior is not None:
            if (unused_predecessor_sha256 or repair_predecessor_sha256) != prior[
                "sha256"
            ]:
                raise ValueError("capability successor is immutable")
            from ecomsre.product.investigation import closure_budget

            budget = closure_budget.load(c)
            if budget is None or closure_budget.ledger(c) != budget["baseline"]:
                raise ValueError("redeployment must precede all new Provider work")
            if (
                prior["old"] != value["old"]
                or prior["old_deployment"] != value["old_deployment"]
            ):
                raise ValueError(
                    "redeployment must compare directly to retained original"
                )
            intermediate = prior["new"]["capability_sha256"]
            for table in (
                *(() if repair_predecessor_sha256 else ("incidents",)),
                "knowledge_candidate_pool_v050",
                "investigation_provider_calls_v050",
            ):
                if c.execute(
                    f"SELECT 1 FROM {table} WHERE payload_json LIKE ?",
                    ("%" + intermediate + "%",),
                ).fetchone():
                    raise ValueError("intermediate capability was consumed")
            if (
                execution_resume is None
                and c.execute(
                    "SELECT 1 FROM knowledge_closure_runner_v050 WHERE entry_key LIKE 'request:%' OR entry_key LIKE 'attempt:%' OR entry_key='selection-pending'"
                ).fetchone()
            ):
                raise ValueError("redeployment cannot follow proposal preparation")
        elif (
            unused_predecessor_sha256 is not None
            or repair_predecessor_sha256 is not None
        ):
            raise ValueError("redeployment predecessor absent")
        if selection_lock_v050.load(c) is not None:
            raise ValueError("capability successor must precede candidate lock")
        c.execute(
            "CREATE TABLE IF NOT EXISTS knowledge_capability_successor_v050 (environment_id TEXT PRIMARY KEY,payload_json TEXT NOT NULL)"
        )
        if prior is not None:
            c.execute(
                "CREATE TABLE IF NOT EXISTS knowledge_capability_successor_history_v050 (sha256 TEXT PRIMARY KEY,payload_json TEXT NOT NULL)"
            )
            c.execute(
                "INSERT INTO knowledge_capability_successor_history_v050 VALUES (?,?)",
                (prior["sha256"], json.dumps(prior, sort_keys=True)),
            )
            changed = c.execute(
                "UPDATE knowledge_capability_successor_v050 SET payload_json=? WHERE environment_id=? AND payload_json=?",
                (
                    json.dumps(value, sort_keys=True),
                    old.environment_id,
                    json.dumps(prior, sort_keys=True),
                ),
            ).rowcount
            if changed != 1:
                raise ValueError("redeployment mapping compare-and-swap failed")
        else:
            c.execute(
                "INSERT INTO knowledge_capability_successor_v050 VALUES (?,?)",
                (old.environment_id, json.dumps(value, sort_keys=True)),
            )
        c.execute("COMMIT")
    return value["sha256"]


def admits(store, *, environment_id, candidate_environment_id, expected, actual):
    if environment_id != candidate_environment_id:
        return False
    with store.connect() as c:
        value = load(c, environment_id)
        if value is None:
            return expected == actual
        if not {expected, actual}.issubset(
            {value["old"]["capability_sha256"], value["new"]["capability_sha256"]}
            | (
                {value["retained_development"]["matrix"]["capability_sha256"]}
                if "retained_development" in value
                else set()
            )
            | (
                {value["earlier_development"]["matrix"]["capability_sha256"]}
                if "earlier_development" in value
                else set()
            )
        ):
            return False
        # A later unregistered reverify/config edit invalidates the mapping.
        current = c.execute(
            "SELECT payload_json FROM environment_capability_matrices WHERE environment_id=?",
            (environment_id,),
        ).fetchone()
        return (
            current is not None
            and json.loads(current[0]) == value["new"]
            and EnvironmentRepositoryV1(store)
            .get(environment_id)
            .model_dump(mode="json")
            == value["new_deployment"]["environment"]
        )


def historical_matrix(store, environment_id, digest):
    with store.connect() as c:
        value = load(c, environment_id)
    if value is not None:
        matrices = [value["old"], value["new"]]
        if "retained_development" in value:
            matrices.append(value["retained_development"]["matrix"])
        if "earlier_development" in value:
            matrices.append(value["earlier_development"]["matrix"])
        for matrix in matrices:
            if matrix["capability_sha256"] == digest:
                return EnvironmentCapabilityMatrixV1.model_validate(matrix)
    return None

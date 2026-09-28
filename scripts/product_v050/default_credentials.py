"""Reviewed defaults, with fresh runtime observations; no live operations.

The mapping owns numeric semantics. CAS integrity is only one prerequisite.
Legacy engineering declarations and incomplete old projections are never upgraded.
"""

from copy import deepcopy
from pathlib import Path
import hashlib
import json
import math
import re

from ecomsre.dta_v2.v22.read_contracts import semantic_sha256_v22 as sha

VERSION = "ingestion-sample-evidence-v4"
CREDENTIAL = "reviewed-producer-default-credential-v1"
OBSERVATION = "default-runtime-observation-v1"
MAPPING_PATH = (
    Path(__file__).resolve().parents[2]
    / "config/product-v050/reviewed-producer-defaults-v1.json"
)


def mapping():
    return json.loads(MAPPING_PATH.read_text())


def read_object(ref, read_bytes):
    if not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{64}", ref):
        raise ValueError("default credential reference invalid")
    raw = read_bytes(ref)
    if hashlib.sha256(raw).hexdigest() != ref:
        raise ValueError("default credential object digest differs")
    return json.loads(raw)


def resolve(
    ref, read_bytes, *, collector, binding, occurrence, incident_id, requirements
):
    """Resolve only a new occurrence-bound credential under the frozen mapping."""
    m = mapping()
    if binding.get("defaults_mapping_sha256") != sha(m):
        raise ValueError("reviewed defaults mapping differs")
    c = read_object(ref, read_bytes)
    if (
        set(c) != {"version", "mapping_id", "defaults", "scope", "proofs"}
        or c["version"] != CREDENTIAL
    ):
        raise ValueError("default credential schema differs")
    if c["mapping_id"] != m["mapping_id"] or c["defaults"] != m["defaults"]:
        raise ValueError("default values or reviewed version differ")
    scope = dict(
        deployment_id=binding["deployment_id"],
        occurrence=occurrence,
        incident_id=incident_id,
        requirements_sha256=sha(requirements),
    )
    if c["scope"] != scope:
        raise ValueError("default credential occurrence/window/deployment differs")
    refs = c["proofs"]
    if set(refs) != {"runtime", "process", "jar", "sources", "mounted_config"}:
        raise ValueError("default credential proof roles differ")
    if len(refs["sources"]) != len(m["sources"]):
        raise ValueError("default source set incomplete")
    for ref_source, expected in zip(refs["sources"], m["sources"]):
        source = read_object(ref_source, read_bytes)
        if (
            source.get("status") != 200
            or source.get("url") != expected["url"]
            or hashlib.sha256(source.get("text", "").encode()).hexdigest()
            != expected["text_sha256"]
        ):
            raise ValueError("reviewed default source differs")
    runtime = read_object(refs["runtime"], read_bytes)
    process = read_object(refs["process"], read_bytes)
    jar = read_object(refs["jar"], read_bytes)
    if (
        runtime.get("version") != OBSERVATION
        or runtime.get("deployment_id") != scope["deployment_id"]
    ):
        raise ValueError("UNKNOWN_RUNTIME_DEFAULT_OBSERVATION")
    end = max(r["end"] for r in requirements)
    from scripts.product_v050.ingestion_evidence import expected_reads

    left = min(
        p[2]["time"] - int(p[2]["query"].rsplit("[", 1)[1][:-2])
        for r in requirements
        for p in expected_reads(r["query"], r["start"], r["end"], version=VERSION)
    )
    at = runtime.get("observed_at")
    if (
        not isinstance(at, (int, float))
        or not math.isfinite(at)
        or not end <= at <= end + 120
    ):
        raise ValueError("runtime default observation is not current")
    roles = runtime.get("services", {})
    if set(roles) != {"kafka", "otel-collector"}:
        raise ValueError("runtime default services differ")
    for role, image in [
        ("kafka", m["kafka_image_id"]),
        ("otel-collector", m["collector_image_id"]),
    ]:
        row = roles[role]
        if (
            row.get("service") != role
            or row.get("image_id") != image
            or not re.fullmatch(r"[0-9a-f]{64}", row.get("container_id", ""))
            or row.get("running") is not True
            or row.get("restart_count") != 0
            or not isinstance(row.get("started_at"), (int, float))
            or not math.isfinite(row["started_at"])
            or row["started_at"] > left
        ):
            raise ValueError("runtime default identity/lifetime differs")
    if roles["kafka"]["container_id"] == roles["otel-collector"]["container_id"]:
        raise ValueError("runtime default role identity collision")
    if (
        process.get("version") != "complete-jvm-default-projection-v1"
        or process.get("container_id") != roles["kafka"]["container_id"]
        or process.get("observed_at") != at
    ):
        raise ValueError("UNKNOWN_COMPLETE_PROCESS_CONFIGURATION")
    from scripts.product_v050.engineering_configuration import (
        default_override_conflicts,
    )

    if default_override_conflicts(process):
        raise ValueError("explicit or unknown JVM configuration override")
    if (
        jar.get("container_id") != roles["kafka"]["container_id"]
        or jar.get("observed_at") != at
        or jar.get("process_configuration_sha256") != sha(process)
        or jar.get("image_id") != m["kafka_image_id"]
        or jar.get("jar_sha256") != m["jar_sha256"]
        or jar.get("manifest", {}).get("Implementation-Version") != m["agent_version"]
        or jar.get("metric_reader_classes") != m["metric_reader_classes"]
    ):
        raise ValueError("runtime agent jar identity/version differs")
    col = roles["otel-collector"]
    mounted = read_object(refs["mounted_config"], read_bytes)
    if (
        mounted.get("container_id") != col["container_id"]
        or mounted.get("observed_at") != at
        or mounted.get("destination") != "/etc/ecomsre/collector.json"
        or mounted.get("read_only") is not True
        or not re.fullmatch(r"[0-9a-f]{64}", mounted.get("source_identity_sha256", ""))
        or mounted.get("source_identity_sha256")
        != col.get("config_mount_source_sha256")
        or mounted.get("content") != collector
        or sha(mounted) != col.get("mounted_config_sha256")
    ):
        raise ValueError("UNKNOWN_RUNTIME_CONFIG_MOUNT_BINDING")
    if (
        col.get("version") != m["collector_version"]
        or col.get("command") != ["--config=/etc/ecomsre/collector.json"]
        or col.get("entrypoint") not in (None, ["/otelcol-contrib"])
        or col.get("configuration_projection_complete") is not True
        or col.get("override_names") != []
        or col.get("collector_sha256") != sha(collector)
        or set(collector["connectors"]["span_metrics"]) != {"metrics_flush_interval"}
    ):
        raise ValueError(
            "collector configuration identity or explicit override differs"
        )
    proof_hashes = sorted(
        [refs[k] for k in ("runtime", "process", "jar", "mounted_config")]
        + refs["sources"]
    )
    common = dict(
        basis="RUNTIME_VERSION_BOUND_DEFAULT",
        overrides_absent=True,
        evidence_sha256=proof_hashes,
    )
    return {
        "kafka": dict(
            common,
            runtime_version=m["agent_version"] + "/" + m["sdk_version"],
            runtime_image_id=m["kafka_image_id"],
            period_seconds=m["defaults"]["kafka_period_seconds"],
        ),
        "span_metrics": dict(
            common,
            runtime_version=m["collector_version"],
            runtime_image_id=m["collector_image_id"],
            collector_sha256=sha(collector),
            histogram_bounds_milliseconds=deepcopy(
                m["defaults"]["histogram_bounds_milliseconds"]
            ),
        ),
    }


def build_credential(*, binding, requirements, occurrence, incident_id, proofs):
    """Build a new envelope only; resolve() must verify it before any receipt."""
    m = mapping()
    return dict(
        version=CREDENTIAL,
        mapping_id=m["mapping_id"],
        defaults=deepcopy(m["defaults"]),
        scope=dict(
            deployment_id=binding["deployment_id"],
            occurrence=occurrence,
            incident_id=incident_id,
            requirements_sha256=sha(requirements),
        ),
        proofs=deepcopy(proofs),
    )


def project_runtime_observation(
    rows, *, deployment_id, collector, observed_at, config_source, config_bytes
):
    """Pure whitelist projection of fresh full owned Docker inspections.

    No command is executed here. Never call this on the legacy stripped snapshot
    to manufacture missing command/entrypoint/environment coverage.
    """
    from datetime import datetime

    selected = {}
    for row in rows:
        cfg = row["Config"]
        labels = cfg.get("Labels", {})
        role = labels.get("com.docker.compose.service")
        if role not in {"kafka", "otel-collector"}:
            continue
        if role in selected or any(
            labels.get(k) != deployment_id
            for k in ("com.docker.compose.project", "io.ecomsre.sandbox.id")
        ):
            raise ValueError("runtime projection ownership differs")
        if not all(k in cfg for k in ("Cmd", "Entrypoint", "Env")):
            raise ValueError("UNKNOWN_COMPLETE_RUNTIME_CONFIGURATION")
        selected[role] = dict(
            service=role,
            image_id=row["Image"],
            container_id=row["Id"],
            started_at=datetime.fromisoformat(
                row["State"]["StartedAt"].replace("Z", "+00:00")
            ).timestamp(),
            running=row["State"]["Running"],
            restart_count=row["RestartCount"],
        )
        if role == "otel-collector":
            mounts = [
                m
                for m in row.get("Mounts", [])
                if m.get("Destination") == "/etc/ecomsre/collector.json"
            ]
            if (
                len(mounts) != 1
                or mounts[0].get("Type") != "bind"
                or mounts[0].get("RW") is not False
                or mounts[0].get("Source") != config_source
                or json.loads(config_bytes) != collector
            ):
                raise ValueError("UNKNOWN_RUNTIME_CONFIG_MOUNT_BINDING")
            mounted = dict(
                container_id=row["Id"],
                observed_at=observed_at,
                destination="/etc/ecomsre/collector.json",
                read_only=True,
                source_identity_sha256=hashlib.sha256(
                    config_source.encode()
                ).hexdigest(),
                content=json.loads(config_bytes),
            )
            names = {v.split("=", 1)[0] for v in cfg["Env"] or []}
            selected[role].update(
                version=labels.get("org.opencontainers.image.version"),
                command=cfg["Cmd"]
                if cfg["Cmd"] == ["--config=/etc/ecomsre/collector.json"]
                else ["UNKNOWN_COMMAND"],
                entrypoint=cfg["Entrypoint"]
                if cfg["Entrypoint"] in (None, ["/otelcol-contrib"])
                else ["UNKNOWN_ENTRYPOINT"],
                config_mount_source_sha256=mounted["source_identity_sha256"],
                mounted_config_sha256=sha(mounted),
                collector_sha256=sha(collector),
                configuration_projection_complete=True,
                override_names=sorted(
                    k
                    for k in names
                    if (k.startswith("OTEL") or k.startswith("GODEBUG"))
                    and k
                    not in {
                        "OTEL_COLLECTOR_HOST",
                        "OTEL_COLLECTOR_PORT_GRPC",
                        "OTEL_COLLECTOR_PORT_HTTP",
                    }
                ),
            )
    if set(selected) != {"kafka", "otel-collector"}:
        raise ValueError("runtime projection services incomplete")
    return dict(
        version=OBSERVATION,
        deployment_id=deployment_id,
        observed_at=observed_at,
        services=selected,
    ), mounted


def audit_retained(root):
    """Verify mapped material, and expose why legacy observations cannot qualify."""
    root = Path(root)
    m = mapping()
    sources_ok = []
    for i, expected in enumerate(m["sources"]):
        d = json.loads((root / f"default-source-{i}.json").read_text())
        sources_ok.append(
            d.get("status") == 200
            and d.get("url") == expected["url"]
            and hashlib.sha256(d["text"].encode()).hexdigest()
            == expected["text_sha256"]
        )
    jar = json.loads((root / "kafka-javaagent-version.json").read_text())
    rows = json.loads((root / "started.json").read_text())["resources"]["container"]
    by_role = {r["Config"]["Labels"]["com.docker.compose.service"]: r for r in rows}
    process = json.loads((root / "kafka-process-options-verified.json").read_text())
    missing = []
    if process.get("version") != "complete-jvm-default-projection-v1":
        missing.append("COMPLETE_JVM_OPTION_INVENTORY_NOT_RETAINED")
    if not all(
        k in by_role["otel-collector"]["Config"] for k in ("Cmd", "Entrypoint", "Env")
    ):
        missing.append("COMPLETE_COLLECTOR_EXECUTION_PROJECTION_NOT_RETAINED")
    if not all(k in jar for k in ("container_id", "observed_at")):
        missing.append("JAR_OBSERVATION_INSTANCE_AND_TIME_BINDING_NOT_RETAINED")
    matches = dict(
        sources=all(sources_ok),
        jar=jar["jar_sha256"] == m["jar_sha256"],
        agent=jar["manifest"]["Implementation-Version"] == m["agent_version"],
        sdk_class=jar["metric_reader_classes"] == m["metric_reader_classes"],
        kafka_image=by_role["kafka"]["Image"] == m["kafka_image_id"],
        collector_image=by_role["otel-collector"]["Image"] == m["collector_image_id"],
    )
    return dict(
        mapping_id=m["mapping_id"],
        material_matches=matches,
        credential_status="UNKNOWN_INCOMPLETE_RUNTIME_EVIDENCE"
        if missing
        else "NOT_ISSUED",
        missing=missing,
        formal_credential_created=False,
        historical_results_upgraded=False,
    )

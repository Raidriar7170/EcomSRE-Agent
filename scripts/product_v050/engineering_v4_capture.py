"""Finite live read list for the explicitly authorized engineering integration."""

from datetime import UTC, datetime
import hashlib
import io
import json
import subprocess
import tarfile
import time
import zipfile

from scripts.product_v050 import engineering_capture as ec, default_credentials as dc
from scripts.product_v050.engineering_configuration import complete_process_projection
from ecomsre_live_sandbox.knowledge_v030 import consumer_membership_healthy_v030

SERVICES = ("checkout", "fraud-detection", "kafka", "payment")


def by_service(rows):
    return {
        r["Config"]["Labels"]["com.docker.compose.service"]: r
        for r in rows["container"]
        if all(
            r["Config"]["Labels"].get(k) == v
            for k, v in ec.load(ec.ROOT / "manifest.json")["labels"].items()
        )
    }


def runtime(rows, directory):
    selected = by_service(rows)
    outputs = {}
    for option in ("state", "members"):
        outputs[option] = ec.docker(
            [
                "exec",
                "--env",
                "KAFKA_HEAP_OPTS=-Xms32m -Xmx128m",
                "--env",
                "KAFKA_OPTS=",
                "--env",
                "JAVA_TOOL_OPTIONS=",
                "--env",
                "_JAVA_OPTIONS=",
                selected["kafka"]["Id"],
                "/opt/kafka/bin/kafka-consumer-groups.sh",
                "--bootstrap-server",
                "kafka:9092",
                "--timeout",
                "5000",
                "--describe",
                "--group",
                "fraud-detection",
                "--" + option,
                *(["--verbose"] if option == "members" else []),
            ]
        )
    states = {}
    for name in SERVICES:
        r = selected[name]
        st = r["State"]
        running = st["Running"] and not any(
            st.get(k) for k in ("Paused", "Restarting", "Dead", "OOMKilled")
        )
        healthy = running and st.get("Health", {}).get("Status") == "healthy"
        if name == "fraud-detection":
            ip = next(iter(r["NetworkSettings"]["Networks"].values()))["IPAddress"]
            healthy = running and consumer_membership_healthy_v030(
                state_output=outputs["state"],
                members_output=outputs["members"],
                container_ip=ip,
            )
        states[name] = dict(
            state="RUNNING" if running else "OTHER",
            healthy=bool(healthy),
            restart_count=r["RestartCount"],
        )
    result = dict(observed_at=time.time(), services=states, consumer=outputs)
    ec.save(directory / "typed-runtime-source.json", result)
    return result


def jar_projection(archive):
    if len(archive) > 64 * 1024 * 1024:
        raise ValueError("BOUNDED_AGENT_ARCHIVE_UNAVAILABLE")
    with tarfile.open(fileobj=io.BytesIO(archive)) as tar:
        entries = [
            m
            for m in tar.getmembers()
            if m.isfile() and m.name == "opentelemetry-javaagent.jar"
        ]
        if len(entries) != 1 or entries[0].size > 64 * 1024 * 1024:
            raise ValueError("UNEXPECTED_AGENT_ARCHIVE")
        jar = tar.extractfile(entries[0]).read()
    with zipfile.ZipFile(io.BytesIO(jar)) as z:
        manifest = {
            k: v.strip()
            for line in z.read("META-INF/MANIFEST.MF").decode().splitlines()
            if ":" in line
            for k, v in [line.split(":", 1)]
            if k
            in {
                "Implementation-Version",
                "Implementation-Title",
                "Premain-Class",
                "Agent-Class",
            }
        }
        classes = {
            n: hashlib.sha256(z.read(n)).hexdigest()
            for n in z.namelist()
            if n.endswith(
                (
                    "PeriodicMetricReaderBuilder.classdata",
                    "PeriodicMetricReaderBuilder.class",
                )
            )
        }
    return dict(
        jar_sha256=hashlib.sha256(jar).hexdigest(),
        manifest=manifest,
        metric_reader_classes=classes,
        jar_bytes=len(jar),
        jar_file_not_persisted=True,
    )


def configuration(rows, directory, objects):
    """Real sequential timestamps; no full environment, jar or inspect dump persisted."""
    selected = by_service(rows)
    cid = selected["kafka"]["Id"]
    raw = ec.docker(["exec", cid, "/bin/cat", "/proc/1/cmdline", "/proc/1/environ"])
    process = complete_process_projection(
        raw, container_id=cid, observed_at=time.time()
    )
    del raw
    argv = [
        "docker",
        "--host",
        ec.load(ec.ROOT / "precheck.json")["endpoint"],
        "cp",
        cid + ":/tmp/opentelemetry-javaagent.jar",
        "-",
    ]
    seq = ec.reserve("docker_reads", {"argv": argv})
    start = datetime.now(UTC).isoformat()
    result = subprocess.run(argv, capture_output=True, timeout=30)
    at = time.time()
    ec.save(
        ec.ROOT / "receipts" / f"{seq:04}.json",
        dict(
            started_at=start,
            received_at=datetime.fromtimestamp(at, UTC).isoformat(),
            returncode=result.returncode,
            stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
        ),
    )
    if result.returncode:
        raise ValueError("AGENT_READ_FAILED")
    jar = jar_projection(result.stdout)
    jar.update(
        container_id=cid,
        observed_at=at,
        image_id=selected["kafka"]["Image"],
        process_configuration_sha256=ec.digest(process),
    )
    source = ec.ROOT / "collector.json"
    if source.is_symlink() or source.resolve() != source:
        raise ValueError("CONFIGURATION_SOURCE_PATH_DIFFERS")
    config_bytes = source.read_bytes()
    config_at = time.time()
    # Refresh all owned identities after the sequential config reads; validate the
    # whole approved plan without persisting raw Config.Env or healthcheck output.
    fresh_rows = json.loads(
        ec.docker(["container", "inspect", *[r["Id"] for r in selected.values()]])
    )
    final_at = time.time()
    combined = dict(rows, container=fresh_rows)
    ec.validate(combined)
    old = {r["Id"]: r for r in selected.values()}
    for r in fresh_rows:
        if (
            r["State"]["StartedAt"] != old[r["Id"]]["State"]["StartedAt"]
            or r["RestartCount"] != old[r["Id"]]["RestartCount"]
        ):
            raise ValueError("CONFIGURATION_INSTANCE_CHANGED")
    runtime, mounted = dc.project_runtime_observation(
        fresh_rows,
        deployment_id=ec.load(ec.ROOT / "manifest.json")["campaign"],
        collector=json.loads(config_bytes),
        observed_at=final_at,
        config_source=str(source),
        config_bytes=config_bytes,
        config_observed_at=config_at,
    )
    proofs = {
        "runtime": runtime,
        "process": process,
        "jar": jar,
        "mounted_config": mounted,
    }
    refs = {}
    for name, value in proofs.items():
        ec.save(directory / (name + ".json"), value)
        refs[name] = objects.put_json(value).object_sha256
    refs["sources"] = [
        objects.put_json(
            ec.load(
                ec.REPO
                / ".local/engineering-calibration/live-01"
                / f"default-source-{i}.json"
            )
        ).object_sha256
        for i in range(8)
    ]
    ec.save(directory / "proof-refs.json", refs)
    return refs

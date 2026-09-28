"""Two fixed, counted, read-only owned Kafka observations; no arbitrary shell."""

import hashlib
import io
import json
import subprocess
import shlex
import tarfile
import zipfile

from scripts.product_v050 import engineering_capture as ec


def selected_process_configuration(raw):
    tokens = raw.split("\0")
    expanded = list(tokens)
    for token in tokens:
        key, _, value = token.partition("=")
        if key in {
            "JAVA_TOOL_OPTIONS",
            "JDK_JAVA_OPTIONS",
            "_JAVA_OPTIONS",
            "KAFKA_OPTS",
        }:
            expanded.extend(shlex.split(value))
    properties = {}
    allowed = {
        "otel.metric.export.interval",
        "otel.metrics.exporter",
        "otel.jmx.config",
        "otel.jmx.target.system",
        "otel.javaagent.configuration-file",
        "otel.javaagent.extensions",
    }
    for token in expanded:
        if token.startswith("-D") and "=" in token:
            key, value = token[2:].split("=", 1)
            if key in allowed:
                properties[key] = value
    environment = {
        k: v
        for token in tokens
        for k, _, v in [token.partition("=")]
        if k in ec.ENV_KEYS
    }
    return dict(
        jvm_pid1=bool(tokens and tokens[0].endswith("/java")),
        javaagent_arguments=[
            t for t in tokens if t == "-javaagent:/tmp/opentelemetry-javaagent.jar"
        ],
        allowed_system_properties=properties,
        allowed_environment=environment,
        options_examined=[
            k
            for k in [
                "JAVA_TOOL_OPTIONS",
                "JDK_JAVA_OPTIONS",
                "_JAVA_OPTIONS",
                "KAFKA_OPTS",
            ]
            if any(t.startswith(k + "=") for t in tokens)
        ],
        config_file_override_present=any(
            t.startswith(("OTEL_EXPERIMENTAL_CONFIG_FILE=", "OTEL_CONFIG_FILE="))
            for t in tokens
        ),
        other_properties_retained=False,
    )


def read_configuration():
    ec.ensure_capacity(2, 60)
    rows = ec.load(ec.ROOT / "started.json")["resources"]["container"]
    row = next(
        r
        for r in rows
        if r["Config"]["Labels"]["com.docker.compose.service"] == "kafka"
    )
    cid = row["Id"]
    raw = ec.docker(["exec", cid, "/bin/cat", "/proc/1/cmdline", "/proc/1/environ"])
    config = selected_process_configuration(raw)
    ec.save(ec.ROOT / "kafka-process-configuration.json", config)
    argv = [
        "docker",
        "--host",
        ec.load(ec.ROOT / "precheck.json")["endpoint"],
        "cp",
        cid + ":/tmp/opentelemetry-javaagent.jar",
        "-",
    ]
    seq = ec.reserve("docker_reads", {"argv": argv})
    result = subprocess.run(argv, capture_output=True, timeout=30)
    ec.save(
        ec.ROOT / "receipts" / f"{seq:04}.json",
        dict(
            returncode=result.returncode,
            stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
        ),
    )
    if result.returncode or len(result.stdout) > 64 * 1024 * 1024:
        raise ValueError("BOUNDED_AGENT_ARCHIVE_UNAVAILABLE")
    with tarfile.open(fileobj=io.BytesIO(result.stdout)) as tar:
        members = [
            m
            for m in tar.getmembers()
            if m.isfile() and m.name == "opentelemetry-javaagent.jar"
        ]
        if len(members) != 1:
            raise ValueError("UNEXPECTED_AGENT_ARCHIVE")
        jar = tar.extractfile(members[0]).read()
    with zipfile.ZipFile(io.BytesIO(jar)) as z:
        manifest = z.read("META-INF/MANIFEST.MF").decode()
        fields = {
            k: v.strip()
            for line in manifest.splitlines()
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
        candidates = [
            name
            for name in z.namelist()
            if name.endswith(
                (
                    "PeriodicMetricReaderBuilder.classdata",
                    "PeriodicMetricReaderBuilder.class",
                )
            )
        ]
        embedded = {
            name: hashlib.sha256(z.read(name)).hexdigest() for name in candidates
        }
    evidence = dict(
        jar_sha256=hashlib.sha256(jar).hexdigest(),
        jar_bytes=len(jar),
        manifest=fields,
        metric_reader_classes=embedded,
        image_id=row["Image"],
        process_configuration_sha256=ec.digest(config),
        jar_file_not_persisted=True,
    )
    ec.save(ec.ROOT / "kafka-javaagent-version.json", evidence)
    print(json.dumps({"process": config, "javaagent": evidence}, indent=2))


if __name__ == "__main__":
    read_configuration()

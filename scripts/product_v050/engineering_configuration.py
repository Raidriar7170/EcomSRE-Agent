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


def complete_process_projection(raw, *, container_id, observed_at):
    """Finite complete option inventory; retain only allowed values, never secrets.

    This new projection must be captured from actual bytes, not synthesized from
    the incomplete legacy projection. It performs no Docker or other I/O.
    """
    tokens = raw.split("\0")
    options = list(tokens)
    for token in tokens:
        key, _, value = token.partition("=")
        if key in {
            "JAVA_TOOL_OPTIONS",
            "JDK_JAVA_OPTIONS",
            "_JAVA_OPTIONS",
            "KAFKA_OPTS",
        }:
            options.extend(shlex.split(value))
    result = selected_process_configuration(raw)
    result.update(
        version="complete-jvm-default-projection-v1",
        container_id=container_id,
        observed_at=observed_at,
        otel_environment_names=sorted(
            {t.split("=", 1)[0] for t in tokens if t.startswith("OTEL_") and "=" in t}
        ),
        otel_property_names=sorted(
            {t[2:].split("=", 1)[0] for t in options if t.startswith("-Dotel.")}
        ),
        agent_options=sorted(
            {
                t
                for t in options
                if t.startswith("-javaagent:")
                and t == "-javaagent:/tmp/opentelemetry-javaagent.jar"
            }
        ),
        opaque_argument_file_present=any(
            t.startswith(("@", "-XX:VMOptionsFile=", "-XX:Flags=")) for t in options
        ),
        unknown_agent_option_present=any(
            t.startswith(("-javaagent:", "-agentpath:", "-agentlib:"))
            and t != "-javaagent:/tmp/opentelemetry-javaagent.jar"
            for t in options
        ),
    )
    return result


def default_override_conflicts(p):
    """Only this reviewed JVM configuration surface is understood."""
    allowed_names = {
        "OTEL_SERVICE_NAME",
        "OTEL_JMX_CONFIG",
        "OTEL_RESOURCE_ATTRIBUTES",
        "OTEL_INSTRUMENTATION_METHODS_INCLUDE",
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
        "OTEL_EXPERIMENTAL_SDK_TELEMETRY_VERSION",
    }
    return (
        p.get("jvm_pid1") is not True
        or p.get("javaagent_arguments")
        != ["-javaagent:/tmp/opentelemetry-javaagent.jar"]
        or p.get("agent_options") != ["-javaagent:/tmp/opentelemetry-javaagent.jar"]
        or p.get("unknown_agent_option_present") is not False
        or p.get("opaque_argument_file_present") is not False
        or p.get("config_file_override_present") is not False
        or not isinstance(p.get("otel_environment_names"), list)
        or bool(set(p.get("otel_environment_names", [])) - allowed_names)
        or p.get("otel_property_names") != ["otel.jmx.target.system"]
        or p.get("allowed_system_properties")
        != {"otel.jmx.target.system": "kafka-broker"}
        or p.get("allowed_environment")
        != {
            "OTEL_SERVICE_NAME": "kafka",
            "OTEL_JMX_CONFIG": "/etc/ecomsre/kafka-jmx.yml",
            "OTEL_INSTRUMENTATION_METHODS_INCLUDE": "kafka.server.KafkaApis[handleProduceRequest]",
            "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE": "cumulative",
        }
    )

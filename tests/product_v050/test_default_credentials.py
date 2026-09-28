"""Synthetic complete runtime observations; source texts are fixture-only."""

from copy import deepcopy
import hashlib
import json

import pytest
from scripts.product_v050 import default_credentials as dc, ingestion_evidence as ie
from scripts.product_v050.engineering_configuration import complete_process_projection
from test_ingestion_evidence import FIXTURE, QUERIES, setup


@pytest.fixture
def credential_factory(monkeypatch):
    original = dc.mapping()
    reviewed = deepcopy(original)
    sources = [
        dict(url=s["url"], status=200, text="fixture-reviewed-source-" + str(i))
        for i, s in enumerate(reviewed["sources"])
    ]
    for spec, source in zip(reviewed["sources"], sources):
        spec["text_sha256"] = hashlib.sha256(source["text"].encode()).hexdigest()
    monkeypatch.setattr(dc, "mapping", lambda: deepcopy(reviewed))

    def build(put, binding, requirements, occurrence, incident_id):
        end = max(r["end"] for r in requirements)
        start = min(r["start"] for r in requirements) - 2000
        process = complete_process_projection(
            "/bin/java\0-javaagent:/tmp/opentelemetry-javaagent.jar\0-Dotel.jmx.target.system=kafka-broker\0"
            "OTEL_SERVICE_NAME=kafka\0OTEL_JMX_CONFIG=/etc/ecomsre/kafka-jmx.yml\0"
            "OTEL_INSTRUMENTATION_METHODS_INCLUDE=kafka.server.KafkaApis[handleProduceRequest]\0"
            "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=cumulative\0",
            container_id="a" * 64,
            observed_at=end,
        )
        services = {
            role: dict(
                service=role,
                image_id=reviewed[key],
                container_id=cid * 64,
                started_at=start,
                running=True,
                restart_count=0,
            )
            for role, key, cid in [
                ("kafka", "kafka_image_id", "a"),
                ("otel-collector", "collector_image_id", "b"),
            ]
        }
        services["otel-collector"].update(
            version=reviewed["collector_version"],
            command=["--config=/etc/ecomsre/collector.json"],
            entrypoint=None,
            configuration_projection_complete=True,
            override_names=[],
            collector_sha256=ie.sha(FIXTURE["collector"]),
        )
        mounted_config = dict(
            container_id="b" * 64,
            observed_at=end,
            destination="/etc/ecomsre/collector.json",
            read_only=True,
            source_identity_sha256="c" * 64,
            content=deepcopy(FIXTURE["collector"]),
        )
        services["otel-collector"].update(
            config_mount_source_sha256="c" * 64,
            mounted_config_sha256=ie.sha(mounted_config),
        )
        runtime = dict(
            version=dc.OBSERVATION,
            deployment_id=binding["deployment_id"],
            observed_at=end,
            services=services,
        )
        jar = dict(
            container_id="a" * 64,
            observed_at=end,
            process_configuration_sha256=ie.sha(process),
            image_id=reviewed["kafka_image_id"],
            jar_sha256=reviewed["jar_sha256"],
            manifest={"Implementation-Version": reviewed["agent_version"]},
            metric_reader_classes=reviewed["metric_reader_classes"],
        )
        credential = dict(
            version=dc.CREDENTIAL,
            mapping_id=reviewed["mapping_id"],
            defaults=deepcopy(reviewed["defaults"]),
            scope=dict(
                deployment_id=binding["deployment_id"],
                occurrence=occurrence,
                incident_id=incident_id,
                requirements_sha256=ie.sha(requirements),
            ),
            proofs=dict(
                mounted_config=put(mounted_config),
                runtime=put(runtime),
                process=put(process),
                jar=put(jar),
                sources=[put(s) for s in sources],
            ),
        )
        return credential, {
            "runtime": runtime,
            "process": process,
            "jar": jar,
            "mounted_config": mounted_config,
        }

    return build


@pytest.mark.parametrize(
    "damage",
    [
        None,
        "period",
        "buckets",
        "version",
        "jar",
        "image",
        "scope",
        "lifetime",
        "explicit",
        "unknown_option",
        "collector_override",
        "missing_projection",
        "source",
        "mount",
        "mount_content",
        "collector_version",
    ],
)
def test_reviewed_defaults_derive_semantics_and_reject_conflicts(
    credential_factory, damage
):
    objects = {}

    def put(value):
        raw = json.dumps(value).encode()
        h = hashlib.sha256(raw).hexdigest()
        objects[h] = raw
        return h

    binding = ie.topology_v4(
        FIXTURE["collector"],
        FIXTURE["prometheus_command"],
        QUERIES,
        deployment_id="fixture",
    )
    _, req = setup()
    c, proofs = credential_factory(put, binding, [req], "fixture", "i")
    if damage == "mount":
        proofs["mounted_config"]["source_identity_sha256"] = "d" * 64
    elif damage == "mount_content":
        proofs["mounted_config"]["content"] = {}
    elif damage == "collector_version":
        proofs["runtime"]["services"]["otel-collector"]["version"] = "unknown"
    elif damage == "period":
        c["defaults"]["kafka_period_seconds"] = 180
    elif damage == "buckets":
        c["defaults"]["histogram_bounds_milliseconds"] = [1, 2]
    elif damage == "version":
        c["mapping_id"] = "unknown-version"
    elif damage == "jar":
        proofs["jar"]["jar_sha256"] = "0" * 64
    elif damage == "image":
        proofs["runtime"]["services"]["kafka"]["image_id"] = "sha256:" + "0" * 64
    elif damage == "scope":
        c["scope"]["occurrence"] = "other"
    elif damage == "lifetime":
        proofs["runtime"]["services"]["kafka"]["started_at"] = req["end"]
    elif damage == "explicit":
        proofs["process"]["allowed_environment"]["OTEL_METRIC_EXPORT_INTERVAL"] = (
            "60000"
        )
    elif damage == "unknown_option":
        proofs["process"]["unknown_agent_option_present"] = True
    elif damage == "collector_override":
        proofs["runtime"]["services"]["otel-collector"]["command"].append(
            "--set=connectors.span_metrics.histogram.explicit.buckets=[1]"
        )
    elif damage == "missing_projection":
        proofs["process"].pop("version")
    elif damage == "source":
        c["proofs"]["sources"][0] = put(
            {"url": "wrong", "status": 200, "text": "wrong"}
        )
    c["proofs"].update({k: put(v) for k, v in proofs.items()})

    def resolve():
        return dc.resolve(
            put(c),
            objects.__getitem__,
            collector=FIXTURE["collector"],
            binding=binding,
            occurrence="fixture",
            incident_id="i",
            requirements=[req],
        )

    if damage:
        with pytest.raises(ValueError):
            resolve()
    else:
        resolved = resolve()
        assert resolved["kafka"]["period_seconds"] == 60
        from scripts.product_v050.sampling_support import profile

        kafka = profile(
            FIXTURE["collector"],
            'jvm_memory_used_bytes{service_name="kafka"}',
            resolved_defaults=resolved,
        )
        buckets = profile(
            FIXTURE["collector"],
            'traces_span_metrics_duration_milliseconds_bucket{service_name="payment"}',
            resolved_defaults=resolved,
        )
        assert kafka["declared_period_seconds"] == 60
        assert kafka["maximum_gap_seconds"] == 76
        assert buckets["expected_histogram_bounds"] == dc.mapping()["defaults"][
            "histogram_bounds_milliseconds"
        ] + ["+Inf"]
        assert (
            resolved["span_metrics"]["histogram_bounds_milliseconds"]
            == dc.mapping()["defaults"]["histogram_bounds_milliseconds"]
        )


def test_complete_projection_retains_unknown_presence_without_secret_values():
    p = complete_process_projection(
        "/bin/java\0-javaagent:/secret/other.jar=SECRET\0-Dotel.config.file=/SECRET\0OTEL_UNKNOWN=SECRET\0",
        container_id="a" * 64,
        observed_at=1,
    )
    assert (
        p["unknown_agent_option_present"]
        and "otel.config.file" in p["otel_property_names"]
    )
    assert "OTEL_UNKNOWN" in p["otel_environment_names"]
    assert "SECRET" not in json.dumps(p)


@pytest.mark.parametrize(
    "option",
    [
        "@/SECRET",
        "JDK_JAVA_OPTIONS=@/SECRET",
        "-XX:VMOptionsFile=/SECRET",
        "-XX:Flags=/SECRET",
    ],
)
def test_opaque_jvm_files_fail_closed(option):
    from scripts.product_v050.engineering_configuration import (
        default_override_conflicts,
    )

    p = complete_process_projection(
        "/bin/java\0" + option + "\0", container_id="a" * 64, observed_at=1
    )
    assert p["opaque_argument_file_present"] is True
    assert default_override_conflicts(p)
    assert "SECRET" not in json.dumps(p)


def test_new_semantic_code_is_frozen():
    from ecomsre.product.knowledge.evolution_v050 import evaluation_bindings

    bindings = evaluation_bindings()
    for name in (
        "scripts/product_v050/default_credentials.py",
        "scripts/product_v050/engineering_configuration.py",
        "config/product-v050/reviewed-producer-defaults-v1.json",
    ):
        assert name in bindings


@pytest.mark.parametrize(
    "damage", [None, "mount", "writable", "bytes", "legacy", "command"]
)
def test_runtime_projection_requires_actual_readonly_mount_and_redacts(damage):
    rows = []
    for role in ["kafka", "otel-collector"]:
        rows.append(
            dict(
                Id=("a" if role == "kafka" else "b") * 64,
                Image=dc.mapping()[
                    "kafka_image_id" if role == "kafka" else "collector_image_id"
                ],
                State=dict(StartedAt="2026-09-27T00:00:00Z", Running=True),
                RestartCount=0,
                Config=dict(
                    Labels={
                        "com.docker.compose.service": role,
                        "com.docker.compose.project": "fixture",
                        "io.ecomsre.sandbox.id": "fixture",
                        "org.opencontainers.image.version": "0.157.0",
                    },
                    Cmd=["--config=/etc/ecomsre/collector.json"],
                    Entrypoint=None,
                    Env=[],
                ),
                Mounts=[
                    dict(
                        Destination="/etc/ecomsre/collector.json",
                        Source="/PRIVATE/config",
                        Type="bind",
                        RW=False,
                    )
                ],
            )
        )
    raw = json.dumps(FIXTURE["collector"]).encode()
    if damage == "mount":
        rows[1]["Mounts"][0]["Source"] = "/OTHER"
    if damage == "writable":
        rows[1]["Mounts"][0]["RW"] = True
    if damage == "bytes":
        raw = b"{}"
    if damage == "legacy":
        rows[1]["Config"].pop("Env")
    if damage == "command":
        rows[1]["Config"]["Cmd"] = ["--secret=SECRET"]

    def project():
        return dc.project_runtime_observation(
            rows,
            deployment_id="fixture",
            collector=FIXTURE["collector"],
            observed_at=1,
            config_source="/PRIVATE/config",
            config_bytes=raw,
        )

    if damage in {"mount", "writable", "bytes", "legacy"}:
        with pytest.raises(ValueError):
            project()
    else:
        runtime, mounted = project()
        assert runtime["services"]["otel-collector"]["mounted_config_sha256"] == ie.sha(
            mounted
        )
        assert "PRIVATE" not in json.dumps(runtime)
        assert "SECRET" not in json.dumps(runtime)
        if damage == "command":
            assert runtime["services"]["otel-collector"]["command"] == [
                "UNKNOWN_COMMAND"
            ]

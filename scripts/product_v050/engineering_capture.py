"""Explicitly authorized, single-deployment engineering capture; never a batch.

Reuses the retained layout and Owned.validate. Commands are fixed typed steps,
all attempts are journaled before execution, and original product storage is
never opened. No Provider, fault, recovery, registry or promotion entrypoint.
"""

import argparse
from copy import deepcopy
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import socket
import subprocess
import time

import httpx

from scripts.product.minimal_payment_acceptance_v040.owned import semantic
from scripts.product_v050.live_environment import Owned, PORTS, digest, save, UPSTREAM
from scripts.product_v050 import ingestion_evidence as ie, sampling_support as ss

REPO = Path(__file__).resolve().parents[2]
ROOT = REPO / ".local/engineering-calibration/live-01"
PRIOR = REPO / ".local/product-v050/live-final-closure-09"
CLEANING = False
CLEANUP_READ_RESERVE = 13
CLEANUP_TIME_RESERVE = 15 * 60

ENV_KEYS = {
    "OTEL_SERVICE_NAME",
    "OTEL_METRIC_EXPORT_INTERVAL",
    "OTEL_METRICS_EXPORTER",
    "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE",
    "OTEL_JMX_CONFIG",
    "OTEL_JMX_TARGET_SYSTEM",
    "OTEL_JAVAAGENT_CONFIGURATION_FILE",
    "OTEL_JAVAAGENT_EXTENSIONS",
    "OTEL_INSTRUMENTATION_METHODS_INCLUDE",
}


def load(path):
    return json.loads(Path(path).read_text())


def reserve(category, detail):
    auth = load(ROOT / "authorization.json")
    remaining = (
        datetime.fromisoformat(auth["deadline"]) - datetime.now(UTC)
    ).total_seconds()
    if remaining <= 0 or (not CLEANING and remaining < CLEANUP_TIME_RESERVE):
        raise ValueError("CALIBRATION_TIME_CAP")
    journal = ROOT / "operations.jsonl"
    entries = (
        [json.loads(s) for s in journal.read_text().splitlines()]
        if journal.exists()
        else []
    )
    reads = sum(e["category"] in {"docker_read", "docker_reads"} for e in entries)
    if category in {"docker_read", "docker_reads"}:
        limit = auth["caps"].get("docker_reads", 60)
        if reads >= limit - (0 if CLEANING else CLEANUP_READ_RESERVE):
            raise ValueError("DOCKER_READ_CAP_OR_CLEANUP_RESERVE")
    counts = {k: sum(e["category"] == k for e in entries) for k in auth["caps"]}
    if category in counts and counts[category] >= auth["caps"][category]:
        raise ValueError("CALIBRATION_CAP:" + category)
    # The precheck uses docker_read; retain the same spelling throughout.
    if (
        category == "docker_read"
        and sum(e["category"] == category for e in entries) >= 60
    ):
        raise ValueError("DOCKER_READ_CAP")
    entry = dict(
        sequence=len(entries) + 1,
        at=datetime.now(UTC).isoformat(),
        monotonic=time.monotonic(),
        category=category,
        detail=detail,
        status="ATTEMPTED",
    )
    with journal.open("a") as stream:
        stream.write(json.dumps(entry, sort_keys=True) + "\n")
    journal.chmod(0o600)
    return entry["sequence"]


def ensure_capacity(reads, seconds):
    auth = load(ROOT / "authorization.json")
    entries = (
        [json.loads(s) for s in (ROOT / "operations.jsonl").read_text().splitlines()]
        if (ROOT / "operations.jsonl").exists()
        else []
    )
    used = sum(e["category"] in {"docker_read", "docker_reads"} for e in entries)
    if used + reads + CLEANUP_READ_RESERVE > auth["caps"].get("docker_reads", 60):
        raise ValueError("INSUFFICIENT_READS_WITH_CLEANUP_RESERVE")
    if (
        datetime.fromisoformat(auth["deadline"]) - datetime.now(UTC)
    ).total_seconds() < seconds + CLEANUP_TIME_RESERVE:
        raise ValueError("INSUFFICIENT_TIME_WITH_CLEANUP_RESERVE")


def docker(args, *, mutation=False, timeout=30):
    pre = load(ROOT / "precheck.json")
    argv = ["docker", "--host", pre["endpoint"], *args]
    seq = reserve("docker_mutation" if mutation else "docker_reads", {"argv": argv})
    result = subprocess.run(argv, capture_output=True, timeout=timeout)
    save(
        ROOT / "receipts" / f"{seq:04}.json",
        dict(
            returncode=result.returncode,
            stdout_sha256=hashlib.sha256(result.stdout).hexdigest(),
            stderr_sha256=hashlib.sha256(result.stderr).hexdigest(),
        ),
    )
    if result.returncode:
        raise ValueError("DOCKER_COMMAND_FAILED:" + str(seq))
    return result.stdout.decode().strip()


def fresh():
    pre = load(ROOT / "precheck.json")
    if docker(["info", "--format", "{{.ID}}"]) != pre["daemon"]:
        raise ValueError("DAEMON_DRIFT")


def snapshot(tag):
    fresh()
    pre = load(ROOT / "precheck.json")
    rows = {}
    for kind, listing in [
        ("container", ["ps", "-aq", "--no-trunc"]),
        ("network", ["network", "ls", "-q", "--no-trunc"]),
        ("volume", ["volume", "ls", "-q"]),
    ]:
        ids = docker(listing).split()
        rows[kind] = json.loads(docker([kind, "inspect", *ids])) if ids else []
    ours = (
        load(ROOT / "manifest.json")["labels"]
        if (ROOT / "manifest.json").exists()
        else None
    )
    foreign = {k: {} for k in rows}
    for kind, group in rows.items():
        for row in group:
            labels = row.get("Config", {}).get("Labels", row.get("Labels")) or {}
            if ours and all(labels.get(k) == v for k, v in ours.items()):
                continue
            identity = row.get("Id", row.get("Name"))
            foreign[kind][identity] = hashlib.sha256(
                json.dumps(semantic(kind, row), sort_keys=True).encode()
            ).hexdigest()
    previous_extra = load(PRIOR / "admitted-baseline.json")["network_extra"]
    for row in rows["network"]:
        if (
            row["Id"] in previous_extra
            and {k: row.get(k) for k in previous_extra[row["Id"]]}
            != previous_extra[row["Id"]]
        ):
            raise ValueError("NON_OWNED_NETWORK_POLICY_DRIFT")
    if foreign != pre["inventory"]:
        raise ValueError("NON_OWNED_DRIFT")
    save(ROOT / f"snapshot-{tag}.json", project(rows))
    return rows


def project(rows):
    result = {}
    for kind, group in rows.items():
        result[kind] = []
        for row in group:
            item = {
                k: row.get(k)
                for k in [
                    "Id",
                    "Name",
                    "Created",
                    "CreatedAt",
                    "Image",
                    "State",
                    "RestartCount",
                    "HostConfig",
                    "Mounts",
                    "NetworkSettings",
                    "Labels",
                    "Driver",
                    "Options",
                    "Mountpoint",
                    "Containers",
                ]
                if k in row
            }
            if kind == "container":
                config = row["Config"]
                item["Config"] = {k: config.get(k) for k in ["Labels", "User"]}
                item["allowed_environment"] = {
                    k: v
                    for x in config.get("Env", [])
                    for k, _, v in [x.partition("=")]
                    if k in ENV_KEYS
                }
                item["config_sha256"] = digest(config)
            result[kind].append(item)
    return result


def owner():
    manifest = load(ROOT / "manifest.json")
    obj = object.__new__(Owned)
    obj.root, obj.nonce, obj.labels = ROOT, manifest["campaign"], manifest["labels"]
    obj.plan, obj.images = (
        load(ROOT / "compose.json"),
        load(PRIOR / "cached-images.json"),
    )
    obj.context = load(ROOT / "precheck.json")["context"]
    births = load(ROOT / "births.json")
    obj.births = {
        kind: {r.get("Id", r.get("Name")): r for r in group}
        for kind, group in births.items()
    }
    return obj


def validate(rows):
    obj = owner()
    got = {k: [] for k in obj.births}
    for kind, group in rows.items():
        for row in group:
            identity = row.get("Id", row.get("Name"))
            labels = row.get("Config", {}).get("Labels", row.get("Labels")) or {}
            if identity not in obj.births[kind]:
                if all(labels.get(k) == v for k, v in obj.labels.items()):
                    raise ValueError("UNRECORDED_OWNED_RESOURCE")
                continue
            birth = obj.births[kind][identity]
            for field in (
                ["Id", "Created"]
                if kind != "volume"
                else ["Name", "CreatedAt", "Driver", "Options", "Mountpoint"]
            ):
                if row.get(field) != birth.get(field):
                    raise ValueError("BIRTH_DRIFT")
            if any(labels.get(k) != v for k, v in obj.labels.items()):
                raise ValueError("OWNERSHIP_UNKNOWN")
            if kind == "container":
                obj.validate(labels["com.docker.compose.service"], row)
            got[kind].append(identity)
    if any(set(got[k]) != set(obj.births[k]) for k in got):
        raise ValueError("OWNED_RESOURCE_SET_DRIFT")


def prepare():
    if (ROOT / "manifest.json").exists():
        raise ValueError("ALREADY_PREPARED")
    actual = subprocess.check_output(
        [
            "git",
            "-C",
            str(REPO / "third_party/opentelemetry-demo"),
            "rev-parse",
            "HEAD",
        ],
        text=True,
    ).strip()
    if actual != UPSTREAM:
        raise ValueError("UPSTREAM_DRIFT")
    prior = load(PRIOR / "compose.json")
    plan = deepcopy(prior)
    nonce = (
        "ecomsre-calibration-"
        + hashlib.sha256((ROOT / "authorization.json").read_bytes()).hexdigest()[:10]
    )
    labels = {
        "io.ecomsre.minimal.goal": hashlib.sha256(
            (REPO / "docs/goals/EcomSRE_Fresh_Start_Brief.md").read_bytes()
        ).hexdigest(),
        "io.ecomsre.minimal.attempt": nonce,
        "io.ecomsre.sandbox.id": nonce,
    }
    old = plan["name"]
    plan["name"] = nonce
    plan["networks"]["default"]["name"] = nonce + "-default"
    plan["volumes"] = {
        k.replace(old, nonce): {**v, "name": v["name"].replace(old, nonce)}
        for k, v in plan["volumes"].items()
    }
    for service in plan["services"].values():
        service["labels"] = labels
        for mount in service["volumes"]:
            if mount["type"] == "volume":
                mount["source"] = mount["source"].replace(old, nonce)
            elif mount["source"].startswith(str(PRIOR)):
                mount["source"] = mount["source"].replace(str(PRIOR), str(ROOT), 1)
    save(ROOT / "collector.json", load(PRIOR / "collector.json"))
    save(ROOT / "control/demo.flagd.json", load(PRIOR / "documents.json")["BASELINE"])
    save(ROOT / "compose.json", plan)
    save(
        ROOT / "manifest.json",
        dict(
            campaign=nonce,
            labels=labels,
            prior_plan_sha256=digest(prior),
            plan_sha256=digest(plan),
            upstream=UPSTREAM,
        ),
    )
    images = load(PRIOR / "cached-images.json")
    current = json.loads(
        docker(["image", "inspect", *[v["runtime_reference"] for v in images.values()]])
    )
    if len(current) != len(images):
        raise ValueError("IMAGE_SET_INCOMPLETE")
    for row, expected in zip(current, images.values()):
        if (
            row["Id"] not in {expected["Id"], expected["index_id"]}
            or row["Architecture"] != "arm64"
        ):
            raise ValueError("IMAGE_IDENTITY_DRIFT")
    save(
        ROOT / "image-identities.json",
        [
            {
                "Id": r["Id"],
                "RepoDigests": r.get("RepoDigests"),
                "labels": r["Config"].get("Labels"),
            }
            for r in current
        ],
    )
    resolved = json.loads(
        docker(
            ["compose", "-f", str(ROOT / "compose.json"), "config", "--format", "json"]
        )
    )
    save(ROOT / "resolved-plan-sha256.json", {"sha256": digest(resolved)})


def start():
    ensure_capacity(12, 300)
    reserve(
        "deployment_starts", {"manifest_sha256": digest(load(ROOT / "manifest.json"))}
    )
    snapshot("prestart")
    for port, _ in PORTS.values():
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", port))
    plan = load(ROOT / "compose.json")
    labels = load(ROOT / "manifest.json")["labels"]
    label_args = [x for k, v in labels.items() for x in ["--label", k + "=" + v]]
    # Record names before mutation so partial create remains recoverable.
    save(
        ROOT / "declared-resources.json",
        {
            "network": [plan["networks"]["default"]["name"]],
            "volume": list(plan["volumes"]),
            "container": [s["container_name"] for s in plan["services"].values()],
        },
    )
    docker(
        ["network", "create", *label_args, plan["networks"]["default"]["name"]],
        mutation=True,
    )
    for name in plan["volumes"]:
        docker(["volume", "create", *label_args, name], mutation=True)
    docker(
        [
            "compose",
            "-f",
            str(ROOT / "compose.json"),
            "create",
            "--no-build",
            "--pull",
            "never",
        ],
        mutation=True,
        timeout=180,
    )
    fresh()
    declared = load(ROOT / "declared-resources.json")
    rows = {
        kind: json.loads(docker([kind, "inspect", *names]))
        for kind, names in declared.items()
    }
    for kind, group in rows.items():
        for row in group:
            actual = row.get("Config", {}).get("Labels", row.get("Labels")) or {}
            if any(actual.get(k) != v for k, v in labels.items()):
                raise ValueError("BIRTH_OWNERSHIP_UNKNOWN")
            if (
                row.get("Id", row.get("Name"))
                in load(ROOT / "precheck.json")["inventory"][kind]
            ):
                raise ValueError("BIRTH_PREEXISTS")
    save(ROOT / "births.json", project(rows))
    validate(rows)
    docker(
        [
            "compose",
            "-f",
            str(ROOT / "compose.json"),
            "start",
            "--wait",
            "--wait-timeout",
            "240",
        ],
        mutation=True,
        timeout=270,
    )
    fresh()
    rows["container"] = json.loads(
        docker(["container", "inspect", *[r["Id"] for r in rows["container"]]])
    )
    validate(rows)
    save(
        ROOT / "started.json",
        dict(at=datetime.now(UTC).isoformat(), resources=project(rows)),
    )


def http_get(url, params, output):
    if url not in {
        "http://127.0.0.1:19090/api/v1/query",
        "http://127.0.0.1:19090/api/v1/query_range",
        "http://127.0.0.1:16686/jaeger/ui/api/traces",
        "http://127.0.0.1:19200/otel-logs-*/_search",
    }:
        raise ValueError("ENDPOINT_NOT_ALLOWLISTED")
    seq = reserve("http_reads", dict(url=url, params=params))
    started = time.monotonic()
    received = bytearray()
    status = None
    error = None
    truncated = False
    try:
        with httpx.stream(
            "GET", url, params=params, timeout=10, trust_env=False
        ) as response:
            status = response.status_code
            for block in response.iter_bytes():
                received.extend(block)
                if len(received) > 2 * 1024 * 1024:
                    del received[2 * 1024 * 1024 :]
                    truncated = True
                    break
    except Exception as exc:
        error = type(exc).__name__
    raw = output.with_suffix(".body")
    raw.write_bytes(received)
    raw.chmod(0o600)
    record = dict(
        sequence=seq,
        url=url,
        params=params,
        status=status,
        error=error,
        truncated=truncated,
        elapsed_seconds=time.monotonic() - started,
        body_sha256=hashlib.sha256(received).hexdigest(),
        at=datetime.now(UTC).isoformat(),
    )
    save(output, record)
    if status != 200 or error or truncated:
        return None
    try:
        return json.loads(received)
    except ValueError:
        return None


def collect(number):
    ensure_capacity(7, 120)
    target = ROOT / f"round-{number}"
    target.mkdir(mode=0o700)
    reserve("rounds", {"round": number})
    rows = snapshot("round-" + str(number))
    validate(rows)
    started_at = datetime.fromisoformat(load(ROOT / "started.json")["at"]).timestamp()
    end = time.time()
    start = end - 300
    if end - started_at < 600:
        raise ValueError("STARTUP_ACCUMULATION_NOT_READY")
    collection = load(PRIOR / "validation-authorization.json")["plan"]["collection"]
    queries = {
        k: collection["actual_queries"][k] for k in collection["preparation_query_keys"]
    }
    collector = load(ROOT / "collector.json")
    application = (
        load(ROOT / "application-support.json")
        if (ROOT / "application-support.json").exists()
        else None
    )
    ss.verify_default_evidence(
        application,
        lambda h: (ROOT / "objects/sha256" / h[:2] / (h + ".json")).read_bytes(),
    )
    results = []
    begin = time.monotonic()
    for index, (key, query) in enumerate(queries.items()):
        if time.monotonic() - begin >= 120:
            raise ValueError("ROUND_TIME_CAP")
        actual = http_get(
            "http://127.0.0.1:19090/api/v1/query_range",
            dict(query=query, start=start, end=end, step=10),
            target / f"{index:02}-query.json",
        )
        states = {}
        details = {}
        bodies = {}
        for j, (selector, left, params) in enumerate(
            ie.expected_reads(query, start, end, version=ss.VERSION)
        ):
            body = http_get(
                "http://127.0.0.1:19090/api/v1/query",
                params,
                target / f"{index:02}-raw-{j}.json",
            )
            if body is None:
                states[selector] = "HTTP_EVIDENCE_UNAVAILABLE"
                continue
            detail = ss.assess(
                body,
                selector,
                left,
                end,
                ss.profile(collector, selector, application),
                query_start=start,
                inner_seconds=start - left,
            )
            states[selector] = detail["state"]
            details[selector] = detail
            if detail["state"] in {"FRESH_COVERED", "EMPTY"}:
                bodies[selector] = body
        problems = ss.correspondence(bodies)
        results.append(
            dict(
                key=key,
                query=query,
                actual_query_available=actual is not None,
                states=states,
                diagnostics=details,
                correspondence=problems,
                coverage="INVALID_SAMPLE_EVIDENCE"
                if problems or actual is None
                else ie.query_coverage(query, states),
            )
        )
    sources = {
        name: hashlib.sha256((REPO / name).read_bytes()).hexdigest()
        for name in [
            "scripts/product_v050/engineering_capture.py",
            "scripts/product_v050/ingestion_evidence.py",
            "scripts/product_v050/sampling_support.py",
        ]
    }
    save(
        target / "result.json",
        dict(
            mode="SEEN_DEVELOPMENT_CALIBRATION",
            start=start,
            end=end,
            source_sha256=sources,
            input_sha256={
                str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [
                    ROOT / "collector.json",
                    ROOT / "manifest.json",
                    ROOT / "application-support.json",
                ]
                if p.exists()
            },
            queries=results,
            formal_pass=False,
            promotion_eligible=False,
        ),
    )
    print(
        json.dumps(
            {
                "round": number,
                "queries": len(results),
                "supported": sum(
                    q["coverage"].startswith("SUPPORTED") for q in results
                ),
            }
        ),
        flush=True,
    )


def cleanup():
    global CLEANING
    CLEANING = True
    attempt = len(list(ROOT.glob("cleanup-authority-*.json"))) + 1
    tag = str(time.time_ns())
    # Also handles partially created deployments. Names alone never prove ownership:
    # require this authorization's unique labels, declared name, absence at preflight,
    # and exact retained birth identity whenever a birth was recorded.
    rows = snapshot("precleanup-" + tag)
    manifest = load(ROOT / "manifest.json")
    declared = load(ROOT / "declared-resources.json")
    recorded = load(ROOT / "births.json") if (ROOT / "births.json").exists() else {}
    selected = {k: [] for k in rows}
    for kind, group in rows.items():
        births = {r.get("Id", r.get("Name")): r for r in recorded.get(kind, [])}
        for row in group:
            labels = row.get("Config", {}).get("Labels", row.get("Labels")) or {}
            if not all(labels.get(k) == v for k, v in manifest["labels"].items()):
                continue
            identity = row.get("Id", row.get("Name"))
            name = row["Name"].lstrip("/")
            if (
                name not in declared[kind]
                or identity in load(ROOT / "precheck.json")["inventory"][kind]
            ):
                raise ValueError("CLEANUP_OWNERSHIP_UNKNOWN")
            if recorded and identity not in births:
                raise ValueError("UNRECORDED_OWNED_RESOURCE")
            if identity in births:
                fields = (
                    ["Id", "Created"]
                    if kind != "volume"
                    else ["Name", "CreatedAt", "Driver", "Options", "Mountpoint"]
                )
                if any(row.get(k) != births[identity].get(k) for k in fields):
                    raise ValueError("CLEANUP_BIRTH_DRIFT")
            if kind == "network" and set(row.get("Containers", {})) - {
                r["Id"]
                for r in rows["container"]
                if all(
                    (r["Config"].get("Labels") or {}).get(k) == v
                    for k, v in manifest["labels"].items()
                )
            }:
                raise ValueError("UNOWNED_NETWORK_ATTACHMENT")
            selected[kind].append(identity)
    save(
        ROOT / f"cleanup-authority-{attempt}.json",
        dict(resources=selected, at=datetime.now(UTC).isoformat()),
    )
    if selected["container"]:
        docker(
            ["stop", "--time", "10", *selected["container"]], mutation=True, timeout=180
        )
        # Non-force rm rejects running containers; a partial stop cannot cause force removal.
        docker(["container", "rm", *selected["container"]], mutation=True, timeout=60)
    for kind in ["network", "volume"]:
        if selected[kind]:
            docker([kind, "rm", *selected[kind]], mutation=True, timeout=60)
    after = snapshot("after-cleanup-" + tag)
    if any(
        r.get("Id", r.get("Name")) in selected[k]
        for k, group in after.items()
        for r in group
    ):
        raise ValueError("OWNED_REMAINING")
    if any(
        all(
            (r.get("Config", {}).get("Labels", r.get("Labels")) or {}).get(k) == v
            for k, v in manifest["labels"].items()
        )
        for group in after.values()
        for r in group
    ):
        raise ValueError("LATE_OR_UNRECORDED_OWNED_REMAINING")
    save(
        ROOT / "cleanup.json",
        dict(
            clean=True,
            non_owned_unchanged=True,
            owned_remaining=0,
            at=datetime.now(UTC).isoformat(),
        ),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["prepare", "start", "collect", "cleanup"])
    parser.add_argument("--round", type=int, choices=[1, 2, 3])
    args = parser.parse_args()
    if args.operation == "collect":
        collect(args.round)
    else:
        globals()[args.operation]()

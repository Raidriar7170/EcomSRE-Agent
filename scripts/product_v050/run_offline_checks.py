"""Run v0.5 fixture checks and retain a small public-safe machine result.

No Provider requests, Docker operation or knowledge-promotion claim. Pytest's
raw local output is not included in the public projection.
"""

import argparse
from datetime import UTC, datetime
import hashlib
import json
import platform
from importlib.metadata import version
from pathlib import Path
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[2]


def bound_sources(root: Path) -> list[Path]:
    return sorted(
        set(root.glob("src/ecomsre/product/investigation/*.py"))
        | set(root.glob("src/ecomsre/product/knowledge/*v050.py"))
        | set(root.glob("tests/product_v050/*.py"))
        | set(root.glob("scripts/product_v050/*.py"))
        | {root / "scripts/ci/verify_product_v050_continuation.py",
           root / "scripts/ci/verify_product_v050_provider_unblock.py",
           root / "scripts/ci/verify_product_v050_live_resume.py",
           root / "scripts/ci/verify_product_v050_docker_stability.py",
           root / "src/ecomsre/product/connectors/opensearch.py",
           root / "config/product-v050/openai-gpt54-mini-prices-20260917.json"}
        | {
            root / "src/ecomsre/product/knowledge/expressions.py",
            root / "src/ecomsre/product/remediation/planner.py",
            root / "src/ecomsre/product/api.py",
            root / "src/ecomsre/product/jobs/worker.py",
            root / "src/ecomsre/product/incidents/extensions.py",
            root / "src/ecomsre/product/settings.py",
            root / "src/ecomsre/product/knowledge/repository.py",
            root / "src/ecomsre/product/jobs/contracts.py",
            root / "src/ecomsre/product/jobs/contracts_v050.py",
            root / "src/ecomsre/product/jobs/repository.py",
            root / "scripts/ci/verify_product_v050_history.py",
            root / "src/ecomsre/product/incidents/diagnosis_bridge.py",
            root / "scripts/product_v050/run_offline_checks.py",
            root / "scripts/product_v050/preflight.py",
            root / "scripts/ci/verify_product_v050.py",
        }
    )


def run(output: Path) -> int:
    with tempfile.TemporaryDirectory(prefix="ecomsre-v050-check-") as temp:
        junit = Path(temp) / "junit.xml"
        command = [
            sys.executable,
            "-m",
            "pytest",
            "tests/product_v050",
            "-q",
            f"--junitxml={junit}",
        ]
        completed = subprocess.run(command, cwd=ROOT, check=False)
        if not junit.exists():
            raise RuntimeError("pytest did not produce a result")
        cases = []
        for case in ET.parse(junit).getroot().iter("testcase"):
            status = (
                "FAILED"
                if case.find("failure") is not None or case.find("error") is not None
                else ("SKIPPED" if case.find("skipped") is not None else "PASSED")
            )
            cases.append(
                {
                    "id": case.attrib.get("classname", "") + "::" + case.attrib["name"],
                    "status": status,
                }
            )
    paths = bound_sources(ROOT)
    report = {
        "schema_version": "ecomsre.product.offline-checks.v050",
        "evidence_mode": "FIXTURE_ONLY",
        "runtime": {
            "python": platform.python_version(),
            "pytest": version("pytest"),
            "pydantic": version("pydantic"),
        },
        "observed_at": datetime.now(UTC).isoformat(),
        "command": "python -m pytest tests/product_v050 -q --junitxml=<temporary-file>",
        "exit_code": completed.returncode,
        "cases": cases,
        "source_sha256": {
            str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in paths
        },
        "provider_request_count": 0,
        "live_episode_count": 0,
        "external_product_writes": 0,
        "scope": "v050 fixture checks only; no learning or live acceptance",
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return completed.returncode


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    raise SystemExit(run(parser.parse_args().output))


if __name__ == "__main__":
    main()

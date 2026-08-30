from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from ecomsre.product.pilot.runtime_continuity_v0231 import (
    AuthorityContinuousSandboxLifecycleV0231,
    ContinuityPreflightReportV0231,
    ProductBaselineContinuationContextV0231,
    ProductV023PrivateStateBindingV0231,
    load_preserved_runtime_inputs_v0231,
)
from ecomsre_live_sandbox.contracts import canonical_json_bytes, load_bundle
from scripts.ci.verify_product_v0231_history import (
    verify_product_v0231_history,
)


DESCRIPTOR_TERMINAL = "ECOMSRE_PRODUCT_V0231_CONTINUITY_DESCRIPTOR_PASS"
PREFLIGHT_TERMINAL = "ECOMSRE_PRODUCT_V0231_CONTINUITY_PREFLIGHT_PASS"


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Product v0.2.3.1 JSON object is invalid: {path.name}")
    return value


def _contains_absolute_locator(value: object, *, field_name: str | None = None) -> bool:
    if isinstance(value, str):
        allowed_container_destination = (
            field_name == "container_destination" and value == "/etc/flagd"
        )
        return (value.startswith("/") and not allowed_container_destination) or (
            value.startswith("file:")
        )
    if isinstance(value, Mapping):
        return any(
            _contains_absolute_locator(item, field_name=str(key))
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple)):
        return any(_contains_absolute_locator(item) for item in value)
    return False


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json_bytes(payload))


def _markdown(report: ContinuityPreflightReportV0231) -> str:
    return "\n".join(
        (
            "# Product v0.2.3.1 Runtime continuity preflight",
            "",
            f"- Descriptor terminal: `{report.descriptor_terminal}`",
            f"- Preflight terminal: `{report.terminal}`",
            f"- Context SHA-256: `{report.context_sha256}`",
            (
                "- Flagd descriptor SHA-256: "
                f"`{report.flagd_bind_descriptor_sha256}`"
            ),
            (
                "- Runtime authority descriptor SHA-256: "
                f"`{report.runtime_authority_descriptor_sha256}`"
            ),
            f"- Resolved Compose SHA-256: `{report.resolved_compose_sha256}`",
            f"- Pilot Runtime authority SHA-256: "
            f"`{report.pilot_runtime_authority_sha256}`",
            f"- Connector binding SHA-256: `{report.connector_binding_sha256}`",
            "- Exact predecessor flagd path and Baseline bytes: `true`",
            "- All Runtime authority components equal: `true`",
            "- Docker starts / live sessions: `0 / 0`",
            "- Incidents / diagnoses / fault attempts: `0 / 0 / 0`",
            "- Action authority: `NONE`",
            "",
            "The raw Compose document and absolute local locators remain private under ",
            "`.local/product-v0231/continuity-preflight/`.",
            "",
        )
    )


def _verify_or_write(
    path: Path,
    payload: Mapping[str, Any],
    *,
    write_reports: bool,
) -> None:
    if write_reports:
        _write_json(path, payload)
        return
    if _load_object(path) != dict(payload):
        raise ValueError(f"Product v0.2.3.1 tracked report differs: {path.name}")


def run_continuity_preflight_v0231(
    *,
    project_root: Path,
    predecessor_root: Path,
    write_reports: bool,
) -> ContinuityPreflightReportV0231:
    root = Path(project_root).resolve(strict=True)
    predecessor = Path(predecessor_root).resolve(strict=True)
    verify_product_v0231_history(
        root,
        predecessor_root=predecessor,
        write_reports=False,
    )
    manifest = _load_object(root / "config/product-v0231/historical-results.v1.json")
    binding = ProductV023PrivateStateBindingV0231.model_validate(
        manifest.get("private_state")
    )
    context = ProductBaselineContinuationContextV0231.model_validate(
        _load_object(
            root / "docs/analysis/product-v0231-baseline-continuation-context.json"
        )
    )
    bundle = load_bundle(
        predecessor / "config/live-telemetry-controlled-remediation-v1"
    )
    authority, resolved_compose = load_preserved_runtime_inputs_v0231(
        predecessor_root=predecessor,
        binding=binding,
    )
    lifecycle = AuthorityContinuousSandboxLifecycleV0231(
        predecessor_root=predecessor,
        private_root=root / ".local/product-v0231/continuity-preflight",
        binding=binding,
        context=context,
        bundle=bundle,
        preserved_authority=authority,
        preserved_resolved_compose=resolved_compose,
    )
    report = lifecycle.admit_prestart()
    flagd = lifecycle.flagd_descriptor
    runtime = lifecycle.runtime_descriptor
    if flagd is None or runtime is None:
        raise RuntimeError("Product v0.2.3.1 continuity descriptors are absent")
    public_payloads = (
        flagd.model_dump(mode="json"),
        runtime.model_dump(mode="json"),
        report.model_dump(mode="json"),
    )
    if any(_contains_absolute_locator(payload) for payload in public_payloads):
        raise ValueError("Product v0.2.3.1 public preflight leaks a local locator")

    analysis = root / "docs/analysis"
    _verify_or_write(
        analysis / "product-v0231-flagd-bind-descriptor.json",
        public_payloads[0],
        write_reports=write_reports,
    )
    _verify_or_write(
        analysis / "product-v0231-runtime-authority-descriptor.json",
        public_payloads[1],
        write_reports=write_reports,
    )
    _verify_or_write(
        analysis / "product-v0231-continuity-preflight.json",
        public_payloads[2],
        write_reports=write_reports,
    )
    markdown_path = analysis / "product-v0231-continuity-preflight.md"
    markdown = _markdown(report)
    if write_reports:
        markdown_path.write_text(markdown, encoding="utf-8")
    elif markdown_path.read_text(encoding="utf-8") != markdown:
        raise ValueError("Product v0.2.3.1 continuity Markdown differs")
    return report


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-root",
        type=Path,
        default=Path(__file__).resolve().parents[2],
    )
    parser.add_argument("--predecessor-root", type=Path, required=True)
    parser.add_argument("--write-reports", action="store_true")
    args = parser.parse_args(argv)
    report = run_continuity_preflight_v0231(
        project_root=args.project_root,
        predecessor_root=args.predecessor_root,
        write_reports=args.write_reports,
    )
    print(report.descriptor_terminal)
    print(report.terminal)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

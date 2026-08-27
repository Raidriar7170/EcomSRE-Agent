from __future__ import annotations

from pathlib import Path
import tomllib


ROOT = Path(__file__).resolve().parents[2]


def test_product_runtime_dependencies_stay_within_goal_allowlist() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = {item.split(">", 1)[0].split("=", 1)[0] for item in project["project"]["dependencies"]}

    assert dependencies == {"fastapi", "httpx", "pydantic", "tiktoken", "uvicorn"}


def test_product_docker_and_compose_are_two_process_read_only_shell() -> None:
    dockerfile = (ROOT / "Dockerfile.product").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.product.yml").read_text(encoding="utf-8")
    dockerignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")

    assert "USER ecomsre" in dockerfile
    assert 'CMD ["python", "-m", "ecomsre.product.app"]' in dockerfile
    assert "api:" in compose
    assert "worker:" in compose
    assert "python -m ecomsre.product.jobs.worker" in compose
    assert "127.0.0.1:${ECOMSRE_PRODUCT_API_PORT:-8080}:8080" in compose
    assert "${ECOMSRE_ADMIN_TOKEN:?" in compose
    assert compose.count("ecomsre-product-data:/var/lib/ecomsre") == 2
    assert "ecomsre-product-data:" in compose
    assert "docker.sock" not in compose
    assert "privileged:" not in compose
    worker_section = compose.split("  worker:", maxsplit=1)[1]
    assert "ECOMSRE_ADMIN_TOKEN" not in worker_section
    assert dockerignore.splitlines()[0] == "*"
    assert "!src/**" in dockerignore


def test_product_examples_and_ci_typecheck_surface_exist() -> None:
    env_example = (ROOT / "examples/product/.env.example").read_text(encoding="utf-8")
    environment = (
        ROOT / "examples/product/environment.otel-demo.json"
    ).read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/agent-mainline.yml").read_text(
        encoding="utf-8"
    )

    assert "ECOMSRE_ADMIN_TOKEN=" in env_example
    assert '"PROMETHEUS"' in environment
    assert '"OPENSEARCH"' in environment
    assert '"JAEGER"' in environment
    assert '"HTTP_HEALTH"' in environment
    assert "src/ecomsre/product" in workflow

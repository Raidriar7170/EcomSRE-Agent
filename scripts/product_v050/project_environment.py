"""Load only the explicitly authorized project's literal dotenv values."""

import os
from pathlib import Path
import re

KEYS = (
    "ECOMSRE_LLM_BASE_URL",
    "ECOMSRE_LLM_API_KEY",
    "ECOMSRE_LLM_MODEL",
    "ECOMSRE_PRODUCT_PROVIDER_PRICE_FILE",
)


def load_project_environment(path: Path) -> dict:
    values = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.fullmatch(
            r"(?:export\s+)?([A-Z_][A-Z0-9_]*)\s*=\s*(.*?)\s*", stripped
        )
        if match is None:
            raise ValueError("PROJECT_ENV_LITERAL_ASSIGNMENTS_REQUIRED")
        key, value = match.groups()
        if key not in KEYS:
            continue
        if len(value) > 1 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if any(c in value for c in ("$", "`", "\n", "\r")):
            raise ValueError("PROJECT_ENV_EXPANSION_FORBIDDEN")
        if key in values:
            raise ValueError("PROJECT_ENV_DUPLICATE_KEY")
        if os.environ.get(key) and os.environ[key] != value:
            raise ValueError("PROJECT_ENV_PROCESS_CONFLICT")
        values[key] = value
    for key, value in values.items():
        os.environ[key] = value
    return {key: bool(os.environ.get(key)) for key in KEYS}

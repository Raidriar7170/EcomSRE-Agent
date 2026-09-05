"""Fixed v0.4 process entrypoints with private file creation permissions."""

import argparse
import os
import runpy
import sys

ROLES = ("api", "worker", "executor", "control-gateway", "observation-proxy")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=ROLES)
    role = parser.parse_args().role
    # Set before application dispatch can open SQLite, WAL or CAS.
    os.umask(0o077)
    module = (
        "ecomsre.product.app" if role == "api"
        else "ecomsre.product.jobs.worker" if role == "worker"
        else "ecomsre.product.remediation.runtime"
    )
    sys.argv = [module] + ([] if role in {"api", "worker"} else [role])
    runpy.run_module(module, run_name="__main__")


if __name__ == "__main__":
    main()

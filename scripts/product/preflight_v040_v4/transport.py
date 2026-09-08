"""Bound client time and output before buffering untrusted daemon responses."""

from __future__ import annotations
import os
from pathlib import Path
import selectors
import subprocess
import time
from typing import Mapping
from .common import require


def bounded(
    command: list[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    timeout: int,
    maximum: int = 64_000_000,
) -> subprocess.CompletedProcess[bytes]:
    process = subprocess.Popen(
        command, cwd=cwd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    selector = selectors.DefaultSelector()
    assert process.stdout is not None and process.stderr is not None
    selector.register(process.stdout, selectors.EVENT_READ, "stdout")
    selector.register(process.stderr, selectors.EVENT_READ, "stderr")
    data = {"stdout": bytearray(), "stderr": bytearray()}
    started = time.monotonic()
    try:
        while selector.get_map():
            require(time.monotonic() - started < timeout, "CLIENT_TIMEOUT")
            for key, _ in selector.select(
                timeout=min(0.5, max(0.01, timeout - (time.monotonic() - started)))
            ):
                chunk = os.read(key.fd, 65536)
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                data[key.data].extend(chunk)
                require(
                    len(data[key.data])
                    <= (maximum if key.data == "stdout" else 1_000_000),
                    "CLIENT_OUTPUT_BOUNDS",
                )
        returncode = process.wait(
            timeout=max(0.01, timeout - (time.monotonic() - started))
        )
        return subprocess.CompletedProcess(
            command, returncode, bytes(data["stdout"]), bytes(data["stderr"])
        )
    except (Exception, KeyboardInterrupt):
        # Only our own client child is terminated. Never send Docker kill or touch
        # a daemon resource. A mutation outcome is reconciled from its journal.
        process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()

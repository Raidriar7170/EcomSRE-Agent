from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import stat
from typing import Iterator, cast

from ecomsre.product.pilot.contracts_v02 import (
    QueueFlagTransitionV02,
    QueueProfileV02,
)


_PRIVATE_FLAG_KEY_V02 = "kafkaQueueProblems"


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


class QueueFlagControllerV02:
    """Evaluator-only exact-file controller for the preregistered queue flag."""

    def __init__(
        self,
        *,
        runtime_path: Path,
        profile: QueueProfileV02,
        expected_baseline_sha256: str,
    ) -> None:
        self.runtime_path = Path(runtime_path)
        self.profile = profile.frozen()
        self.expected_baseline_sha256 = expected_baseline_sha256
        self._baseline_bytes = self._read_bytes()
        if _sha256(self._baseline_bytes) != expected_baseline_sha256:
            raise ValueError("queue controller baseline digest differs")
        self._validate_document(
            json.loads(self._baseline_bytes),
            require_baseline_default=True,
        )

    def _read_bytes(self) -> bytes:
        flags = os.O_RDONLY
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(self.runtime_path, flags)
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise ValueError("queue controller runtime is not a regular file")
            with os.fdopen(os.dup(descriptor), "rb") as handle:
                return handle.read()
        finally:
            os.close(descriptor)

    def _validate_document(
        self,
        payload: object,
        *,
        require_baseline_default: bool = False,
    ) -> dict[str, object]:
        if not isinstance(payload, dict) or not isinstance(payload.get("flags"), dict):
            raise ValueError("queue controller runtime schema differs")
        flags = payload["flags"]
        target = flags.get(_PRIVATE_FLAG_KEY_V02)
        if not isinstance(target, dict) or target.get("state") != "ENABLED":
            raise ValueError("preregistered queue flag is missing or disabled")
        variants = target.get("variants")
        if (
            not isinstance(variants, dict)
            or variants.get("off") != self.profile.expected_default_value
        ):
            raise ValueError("preregistered queue flag baseline differs")
        default_variant = target.get("defaultVariant")
        if (
            require_baseline_default
            and (
                not isinstance(default_variant, str)
                or variants.get(default_variant)
                != self.profile.expected_default_value
            )
        ):
            raise ValueError("queue flag active default does not map to baseline")
        return payload

    def _atomic_write(self, payload: bytes) -> None:
        parent = self.runtime_path.parent
        if parent.is_symlink():
            raise ValueError("queue controller parent may not be a symlink")
        temporary = parent / f".{self.runtime_path.name}.product-v02.tmp"
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        try:
            with os.fdopen(descriptor, "wb", closefd=False) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.close(descriptor)
            descriptor = -1
            os.replace(temporary, self.runtime_path)
            os.chmod(self.runtime_path, 0o600, follow_symlinks=False)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            if temporary.exists():
                temporary.unlink()

    def apply(self, value: int) -> QueueFlagTransitionV02:
        if value not in self.profile.candidate_values:
            raise ValueError("queue value is outside the frozen candidate set")
        before = self._read_bytes()
        if _sha256(before) != self.expected_baseline_sha256:
            raise RuntimeError("queue flag baseline must be restored before apply")
        payload = self._validate_document(
            json.loads(before),
            require_baseline_default=True,
        )
        flags = cast(dict[str, object], payload["flags"])
        target = dict(cast(dict[str, object], flags[_PRIVATE_FLAG_KEY_V02]))
        variants = dict(cast(dict[str, object], target["variants"]))
        variant = f"ecomsre-v02-{value}"
        variants[variant] = value
        target["variants"] = variants
        target["defaultVariant"] = variant
        changed_flags = dict(flags)
        changed_flags[_PRIVATE_FLAG_KEY_V02] = target
        changed = dict(payload)
        changed["flags"] = changed_flags
        encoded = json.dumps(changed, indent=2, sort_keys=True).encode("utf-8") + b"\n"
        self._atomic_write(encoded)
        observed = self._validate_document(json.loads(self._read_bytes()))
        observed_flags = cast(dict[str, object], observed["flags"])
        observed_target = cast(
            dict[str, object], observed_flags[_PRIVATE_FLAG_KEY_V02]
        )
        if observed_target["defaultVariant"] != variant:
            raise RuntimeError("queue flag positive readback failed")
        return QueueFlagTransitionV02(
            before_sha256=_sha256(before),
            after_sha256=_sha256(encoded),
            applied_value=value,
        )

    def restore(self) -> None:
        self._atomic_write(self._baseline_bytes)
        if _sha256(self._read_bytes()) != self.expected_baseline_sha256:
            raise RuntimeError("queue flag exact restoration failed")

    @contextmanager
    def activated(self, value: int) -> Iterator[QueueFlagTransitionV02]:
        try:
            transition = self.apply(value)
            yield transition
        finally:
            if _sha256(self._read_bytes()) != self.expected_baseline_sha256:
                self.restore()


__all__ = ["QueueFlagControllerV02"]

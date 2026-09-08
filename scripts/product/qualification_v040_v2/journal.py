"""V2 seed gate; v1 journal and evidence remain unmodified."""

from __future__ import annotations
from copy import deepcopy
import json
from typing import Any

from scripts.product.qualification_v040.guard import QualificationJournal, require, seal
from scripts.product.qualification_v040.volumes import KAFKA_PATHS, validate_seed_pair
from scripts.product.qualification_v040_v2.policy import provenance_gate


class V2Journal(QualificationJournal):
    def validate_seeds(self, stage: str, saved: dict[str, Any]) -> None:
        names = [s["name"] for s in self.plan["stages"]]
        index = names.index(stage)
        if index < names.index("AFTER_COPYUP_MEASUREMENT"):
            require(saved["status"] == "NOT_BOUND", "UNEXPECTED_SEED_BINDING")
            return
        if index > names.index("BEFORE_CLEANUP"):
            require(
                saved["status"] == "NOT_REQUIRED_DURING_CLEANUP", "SEED_LIFECYCLE_DRIFT"
            )
            return
        require(saved["status"] == "MEASURED", "SEED_CAPTURE_MISSING")
        policy = self.plan["copyup_access_policy"]
        binding = {k: saved[k] for k in ("initial", "uid", "gid")}
        image = {
            "reference": policy["pinned_reference"],
            "platform_digest": policy["image_source"]["platform_digest"],
            "config_digest": policy["image_source"]["config_digest"],
        }
        provenance_gate(policy, binding["initial"], image)
        if self.seed_binding is None:
            require(stage == "AFTER_COPYUP_MEASUREMENT", "UNBOUND_SEED_BIRTH")
            self.seed_binding = deepcopy(binding)
            seal(self.root / "seed-binding.json", binding)
        else:
            require(
                binding == self.seed_binding
                and json.loads((self.root / "seed-binding.json").read_bytes())
                == binding,
                "SEED_BINDING_DRIFT",
            )
        validate_seed_pair(saved["before"], saved["after"])
        initial = {m["path"]: m["entries"] for m in binding["initial"]}
        for measurements in (saved["before"], saved["after"]):
            for measured in measurements:
                path, entries = measured["path"], measured["entries"]
                require(
                    not any(
                        ".ecomsre-v2-" in name
                        or ".ecomsre-v040-qualification-sentinel" in name
                        for name in entries
                    ),
                    "SENTINEL_NOT_REMOVED",
                )
                for name, expected in initial[path].items():
                    require(
                        entries.get(name) == expected,
                        "COPYUP_CONTENT_DRIFT",
                        path + "/" + name,
                    )
                if index < names.index("AFTER_SANDBOX_START") or path == KAFKA_PATHS[0]:
                    require(entries == initial[path], "UNKNOWN_COPYUP_ENTRY", path)
                elif path == KAFKA_PATHS[1] and self.generated_config is not None:
                    require(
                        entries == self.generated_config, "COPYUP_CONTENT_DRIFT", path
                    )
                for name in set(entries) - set(initial[path]):
                    entry = entries[name]
                    require(
                        entry["uid"] == binding["uid"]
                        and entry["gid"] == binding["gid"]
                        and not entry["mode"] & 0o002,
                        "KAFKA_MUTABLE_IDENTITY_DRIFT",
                        path + "/" + name,
                    )
        if stage == "AFTER_KAFKA_IDENTITY":
            require(self.generated_config is None, "SEED_BINDING_DRIFT")
            self.generated_config = deepcopy(
                next(
                    m["entries"] for m in saved["after"] if m["path"] == KAFKA_PATHS[1]
                )
            )
            seal(self.root / "generated-config-binding.json", self.generated_config)
        elif self.generated_config is not None:
            require(
                json.loads((self.root / "generated-config-binding.json").read_bytes())
                == self.generated_config,
                "SEED_BINDING_DRIFT",
            )

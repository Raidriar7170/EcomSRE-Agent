"""Create-once local catalog, selection, and assembly store for v2.3.4.1."""

from __future__ import annotations

import os
from pathlib import Path
import re

from ecomsre.dta_v2.v22.read_contracts import DtaModelV22
from ecomsre.dta_v2.v23.registration_alias_provider_v2341 import (
    RegistrationAliasProviderResultV2341,
)
from ecomsre.dta_v2.v23.registration_assembler_v2341 import (
    FormalRegistrationAssemblyV2341,
)
from ecomsre.dta_v2.v23.registration_catalog_v2341 import (
    RegistrationOptionCatalogV2341,
)


class LocalRegistrationAliasStoreV2341:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.catalogs_dir = self.root / "registration-option-catalogs-v2341"
        self.selections_dir = self.root / "registration-alias-selections-v2341"
        self.assemblies_dir = self.root / "registration-assemblies-v2341"

    @staticmethod
    def _write_bound(path: Path, value: DtaModelV22) -> None:
        rendered = value.model_dump_json(indent=2) + "\n"
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:
            if not path.is_file() or path.read_text(encoding="utf-8") != rendered:
                raise ValueError(f"local v2.3.4.1 artifact already differs: {path.name}")
            return
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(rendered)

    def save_catalog(self, catalog: RegistrationOptionCatalogV2341) -> Path:
        path = self.catalogs_dir / f"{catalog.authorization_id}.json"
        self._write_bound(path, catalog)
        return path

    def load_catalog(self, authorization_id: str) -> RegistrationOptionCatalogV2341:
        if re.fullmatch(r"authorization-v234-[0-9a-f]{16}", authorization_id) is None:
            raise ValueError("registration catalog authorization ID is invalid")
        path = self.catalogs_dir / f"{authorization_id}.json"
        if not path.is_file():
            raise ValueError("registration option catalog is absent")
        return RegistrationOptionCatalogV2341.model_validate_json(path.read_bytes())

    def save_provider_result(
        self, result: RegistrationAliasProviderResultV2341
    ) -> Path:
        selection_id = result.trace.canonical_selection_sha256[:16]
        path = self.selections_dir / f"selection-v2341-{selection_id}.json"
        self._write_bound(path, result)
        return path

    def load_provider_result(
        self, selection_id: str
    ) -> RegistrationAliasProviderResultV2341:
        if re.fullmatch(r"selection-v2341-[0-9a-f]{16}", selection_id) is None:
            raise ValueError("registration alias selection ID is invalid")
        path = self.selections_dir / f"{selection_id}.json"
        if not path.is_file():
            raise ValueError("registration alias selection is absent")
        return RegistrationAliasProviderResultV2341.model_validate_json(path.read_bytes())

    def find_provider_result(
        self, result_sha256: str
    ) -> RegistrationAliasProviderResultV2341:
        if re.fullmatch(r"[0-9a-f]{64}", result_sha256) is None:
            raise ValueError("registration alias result digest is invalid")
        matches = tuple(
            result
            for path in sorted(self.selections_dir.glob("selection-v2341-*.json"))
            if (
                result := RegistrationAliasProviderResultV2341.model_validate_json(
                    path.read_bytes()
                )
            ).result_sha256
            == result_sha256
        )
        if len(matches) != 1:
            raise ValueError("registration alias result digest is not uniquely bound")
        return matches[0]

    def save_assembly(self, assembly: FormalRegistrationAssemblyV2341) -> Path:
        path = self.assemblies_dir / f"{assembly.formal_draft.draft_id}.json"
        self._write_bound(path, assembly)
        return path

    def load_assembly(self, draft_id: str) -> FormalRegistrationAssemblyV2341:
        if re.fullmatch(r"draft-v234-[0-9a-f]{16}", draft_id) is None:
            raise ValueError("registration assembly draft ID is invalid")
        path = self.assemblies_dir / f"{draft_id}.json"
        if not path.is_file():
            raise ValueError("formal registration assembly is absent")
        return FormalRegistrationAssemblyV2341.model_validate_json(path.read_bytes())


__all__ = ("LocalRegistrationAliasStoreV2341",)

"""Deterministic scenario-instance seed material for paired evaluation."""

from __future__ import annotations

import hashlib


def seed_material(evaluation_version: str, template_id: str, seed_id: str) -> str:
    material = b"\0".join(
        item.encode("utf-8") for item in (evaluation_version, template_id, seed_id)
    )
    return hashlib.sha256(material).hexdigest()

def variant_order_hash(
    evaluation_version: str,
    template_id: str,
    seed_id: str,
) -> str:
    material = b"\0".join(
        item.encode("utf-8")
        for item in (evaluation_version, template_id, seed_id, "variant-order")
    )
    return hashlib.sha256(material).hexdigest()

"""Public routes and standalone visual structure stay tied to source."""

from pathlib import Path
import re
import xml.etree.ElementTree as ET
from scripts.ci.verify_product_v041_closeout import Document
from ecomsre.product.remediation.api import router

ROOT = Path(__file__).resolve().parents[2]


def test_remediation_documentation_matches_actual_routes():
    text = (
        (ROOT / "docs/product/API.md").read_text().split("## Bounded remediation", 1)[1]
    )
    documented = set(re.findall(r"`(/v1/[^`]+)`", text))
    implemented = {route.path for route in router.routes}
    assert documented == implemented
    assert not any("execute" in path for path in implemented)


def test_offline_html_has_unique_ids_resolved_anchors_and_no_external_assets():
    source = (ROOT / "docs/interview/ecomsre-agent-v041-handbook.html").read_text()
    doc = Document()
    doc.feed(source)
    assert len(doc.ids) == len(set(doc.ids))
    assert all(link[1:] in doc.ids for link in doc.links if link.startswith("#"))
    assert not re.search(
        r'<(?:script|img|link)[^>]+(?:src|href)=["\']https?://', source
    )
    assert "@media print" in source and "@media(max-width:600px)" in source
    assert (ROOT / "docs/interview/ecomsre-agent-v03-handbook.html").exists()
    ET.parse(ROOT / "docs/assets/ecomsre-v041-architecture.svg")

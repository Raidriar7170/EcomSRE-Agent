"""Blocked export preserves unknown state and refuses active database writers."""

import json
from types import SimpleNamespace

import pytest

from ecomsre.product.remediation.live_evidence import LiveCleanupV040
from scripts.product.export_payment_v040 import cleanup_projection, ensure_quiescent


@pytest.mark.parametrize('prior,current,expected', [(True, False, True), (None, False, None), (False, True, True), (False, False, False)])
def test_cleanup_preserves_historical_non_owned_truth(prior, current, expected):
    previous = LiveCleanupV040(verdict='CLEAN', baseline_restored=True, owned_containers=0, owned_networks=0, owned_volumes=0, non_owned_resources_changed=prior)
    projected = cleanup_projection(previous, {'container': [], 'network': [], 'volume': []}, {'container': 0, 'network': 0, 'volume': 0}, current)
    assert projected.non_owned_resources_changed is expected
    assert projected.verdict == ('CLEAN' if expected is False else 'BLOCKED')


def test_residual_counts_prevent_stale_cleanup_pass():
    previous = LiveCleanupV040(verdict='CLEAN', baseline_restored=True, owned_containers=0, owned_networks=0, owned_volumes=0, non_owned_resources_changed=False)
    projected = cleanup_projection(previous, {'container': ['stopped-writer'], 'network': ['isolated'], 'volume': []}, {'container': 2, 'network': 1, 'volume': 3}, False)
    assert projected.verdict == 'BLOCKED'
    assert (projected.owned_containers, projected.owned_networks, projected.owned_volumes) == (3, 2, 3)


@pytest.mark.parametrize('service', ['api', 'worker', 'remediation-executor', 'remediation-control-gateway'])
def test_active_writer_only_publishes_nonfinal_unknown_capsule(tmp_path, service):
    (tmp_path / 'host').mkdir(mode=0o700)
    (tmp_path / 'host/frozen-manifest.json').write_text(json.dumps({'manifest_sha256': 'a'*64}))
    runtime = SimpleNamespace(private=tmp_path, docker=lambda *args: json.dumps([{'Config': {'Labels': {'com.docker.compose.service': service}}, 'State': {'Running': True}}]))
    with pytest.raises(ValueError, match='writer active'):
        ensure_quiescent(runtime, {'container': ['owned-id']})
    capsule = json.loads((tmp_path / 'host/export-blocked-active-writer.json').read_text())
    assert capsule['status'] == 'NONFINAL_BLOCKER_OBSERVATION'
    assert capsule['counter_state'] == 'UNKNOWN'
    assert not (tmp_path / 'host/private-evidence-index.json').exists()

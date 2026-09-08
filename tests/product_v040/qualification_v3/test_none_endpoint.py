from copy import deepcopy

import pytest

from scripts.product.qualification_v040.guard import QualificationBlocked
from scripts.product.qualification_v040_v3.guard import QualificationJournal


def case():
    journal = object.__new__(QualificationJournal)
    journal.none_endpoint = None
    base = {
        "Id": "none-id",
        "Name": "none",
        "Driver": "null",
        "Labels": {},
        "Containers": {},
    }
    journal.plan = {"nonowned": {"networks": {"none-id": deepcopy(base)}}}
    probe = {
        "Id": "owned-probe",
        "Name": "/probe",
        "HostConfig": {"NetworkMode": "none"},
        "State": {"Running": True},
        "NetworkSettings": {
            "Networks": {"none": {"NetworkID": "none-id", "EndpointID": "ep"}}
        },
    }
    rows = {"networks": {"none-id": deepcopy(base)}}
    rows["networks"]["none-id"]["Containers"] = {
        "owned-probe": {
            "Name": "probe",
            "EndpointID": "ep",
            "MacAddress": "",
            "IPv4Address": "",
            "IPv6Address": "",
        }
    }
    return journal, probe, rows


def test_owned_endpoint_semantic_view_preserves_raw_and_identity():
    journal, probe, rows = case()
    raw = deepcopy(rows)
    assert (
        journal.nonowned_view(
            rows, {"probe/container/kafka-volume-probe": probe}, "AFTER_PROBE_START"
        )
        == journal.plan["nonowned"]
    )
    assert rows == raw


@pytest.mark.parametrize(
    "change",
    ["replacement", "extra_endpoint", "ip", "label", "wrong_cid", "stopped_attached"],
)
def test_unknown_network_membership_or_identity_fails_closed(change):
    journal, probe, rows = case()
    network = rows["networks"]["none-id"]
    if change == "replacement":
        network["Id"] = "replacement"
    elif change == "extra_endpoint":
        network["Containers"]["unknown"] = {}
    elif change == "ip":
        network["Containers"]["owned-probe"]["IPv4Address"] = "10.0.0.2/24"
    elif change == "label":
        network["Labels"]["unknown"] = "changed"
    elif change == "wrong_cid":
        probe["Id"] = "different"
    else:
        probe["State"]["Running"] = False
    with pytest.raises(QualificationBlocked):
        journal.nonowned_view(
            rows, {"probe/container/kafka-volume-probe": probe}, "AFTER_PROBE_START"
        )


def test_consistent_endpoint_replacement_in_both_views_is_denied():
    journal, probe, rows = case()
    seen = {"probe/container/kafka-volume-probe": probe}
    journal.nonowned_view(rows, seen, "AFTER_PROBE_START")
    probe["NetworkSettings"]["Networks"]["none"]["EndpointID"] = "replacement"
    rows["networks"]["none-id"]["Containers"]["owned-probe"]["EndpointID"] = (
        "replacement"
    )
    with pytest.raises(QualificationBlocked, match="PROBE_ENDPOINT_IDENTITY_DRIFT"):
        journal.nonowned_view(rows, seen, "BEFORE_SENTINEL")


def test_endpoint_cannot_first_bind_at_an_ordinary_stage():
    journal, probe, rows = case()
    with pytest.raises(QualificationBlocked, match="PROBE_ENDPOINT_BIND_STAGE_DRIFT"):
        journal.nonowned_view(
            rows, {"probe/container/kafka-volume-probe": probe}, "BEFORE_SENTINEL"
        )

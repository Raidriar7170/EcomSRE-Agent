import io
import tarfile
import pytest
from scripts.product.preflight_v040_v4.common import Failure
from scripts.product.preflight_v040_v4.copyup import parse_copyup
from scripts.product.preflight_v040_v4.processes import validate_probe_census
from scripts.product.preflight_v040_v4.sentinel import SENTINEL_V4, validate_sentinel


def archive(names, kind=tarfile.DIRTYPE):
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode="w") as tar:
        for name in names:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.uid = 1000
            member.gid = 0
            member.mode = 0o775
            if kind == tarfile.SYMTYPE:
                member.linkname = "/tmp"
            tar.addfile(member)
    return stream.getvalue()


@pytest.mark.parametrize(
    "names,kind",
    [
        (["secrets", "secrets"], tarfile.DIRTYPE),
        (["secrets/../escape"], tarfile.DIRTYPE),
        (["/secrets"], tarfile.DIRTYPE),
        (["secrets"], tarfile.SYMTYPE),
        (["secrets"], tarfile.FIFOTYPE),
    ],
)
def test_tar_cannot_escape_or_alias(names, kind):
    with pytest.raises(Failure):
        parse_copyup(archive(names, kind), "/etc/kafka/secrets")


def test_tar_preserves_path_specific_group():
    result = parse_copyup(archive(["secrets"]), "/etc/kafka/secrets")
    assert result["entries"]["."]["gid"] == 0
    assert result["entries"]["."]["mode"] == 0o775


def test_partial_process_and_sentinel_are_not_success():
    with pytest.raises(Failure):
        validate_probe_census("COMPLETE\0")
    with pytest.raises(Failure):
        validate_sentinel("COMPLETE\0", "0" * 32)
    assert ".ecomsre-v4-$1" in SENTINEL_V4
    assert "set -C" in SENTINEL_V4 and 'rm -- "$target"' in SENTINEL_V4

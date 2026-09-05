import io
import json
import zipfile

import pytest

from app.toolchain import (
    TOOLCHAIN_REF_ENV,
    ToolchainProvenanceError,
    bind_bundle_toolchain,
    bind_compact_metadata,
    resolve_toolchain_ref,
)


VALID_REF = "ghcr.io/workpiece-gr/resin-slicer-toolchain@sha256:" + "a" * 64


def _bundle() -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("part.ctb", b"ctb")
        zf.writestr("manifest.json", json.dumps({"engine": {"prusaslicer": {"version": "2.9.6"}}}))
    return buffer.getvalue()


def test_unset_toolchain_ref_is_allowed_for_nonproduction(monkeypatch):
    monkeypatch.delenv(TOOLCHAIN_REF_ENV, raising=False)
    assert resolve_toolchain_ref(required=False) is None
    with pytest.raises(ToolchainProvenanceError):
        resolve_toolchain_ref(required=True)


def test_mutable_toolchain_tag_is_rejected(monkeypatch):
    monkeypatch.setenv(TOOLCHAIN_REF_ENV, "ghcr.io/workpiece-gr/resin-slicer-toolchain:latest")
    with pytest.raises(ToolchainProvenanceError):
        resolve_toolchain_ref()


def test_immutable_ghcr_digest_is_accepted(monkeypatch):
    monkeypatch.setenv(TOOLCHAIN_REF_ENV, VALID_REF)
    assert resolve_toolchain_ref(required=True) == VALID_REF


def test_bundle_binding_records_toolchain_without_changing_payload_or_engine_contract():
    rebound = bind_bundle_toolchain(_bundle(), VALID_REF)
    with zipfile.ZipFile(io.BytesIO(rebound)) as zf:
        assert zf.read("part.ctb") == b"ctb"
        manifest = json.loads(zf.read("manifest.json"))
    assert manifest["engine"] == {"prusaslicer": {"version": "2.9.6"}}
    assert manifest["execution_environment"] == {
        "toolchain_image_ref": VALID_REF,
        "immutable": True,
    }


def test_compact_metadata_binding_records_unresolved_candidate():
    metadata = bind_compact_metadata('{"engine":{"prusaslicer":"2.9.6"}}', None)
    payload = json.loads(metadata)
    assert payload["execution_environment"] == {
        "toolchain_image_ref": None,
        "immutable": False,
    }

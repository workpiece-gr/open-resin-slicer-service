from __future__ import annotations

import io
import json
import os
import re
import zipfile
from collections.abc import Mapping


TOOLCHAIN_REF_ENV = "WORKPIECE_RESIN_TOOLCHAIN_REF"
_IMMUTABLE_GHCR_REF = re.compile(r"^ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$")
_TOOLCHAIN_RECORD_KEYS = {"toolchain_image_ref", "immutable"}


class ToolchainProvenanceError(RuntimeError):
    pass


def _validated_ref(value: object, *, required: bool) -> str | None:
    if value is None:
        if required:
            raise ToolchainProvenanceError(
                "An immutable GHCR toolchain image ref pinned by sha256 digest is required for production."
            )
        return None
    if not isinstance(value, str):
        raise ToolchainProvenanceError(
            "Toolchain image ref must be an immutable GHCR image pinned by sha256 digest."
        )
    ref = value.strip()
    if not ref:
        if required:
            raise ToolchainProvenanceError(
                "An immutable GHCR toolchain image ref pinned by sha256 digest is required for production."
            )
        return None
    if not _IMMUTABLE_GHCR_REF.fullmatch(ref):
        raise ToolchainProvenanceError(
            "Toolchain image ref must be a GHCR image pinned by sha256 digest, not a mutable tag."
        )
    return ref


def resolve_toolchain_ref(*, required: bool = False) -> str | None:
    """Return the immutable runtime toolchain ref, rejecting mutable/ambiguous identifiers."""
    raw = os.environ.get(TOOLCHAIN_REF_ENV)
    if raw is None or not raw.strip():
        if required:
            raise ToolchainProvenanceError(
                f"{TOOLCHAIN_REF_ENV} must identify an immutable toolchain image for production."
            )
        return None
    try:
        return _validated_ref(raw, required=required)
    except ToolchainProvenanceError as exc:
        raise ToolchainProvenanceError(
            f"{TOOLCHAIN_REF_ENV} must be a GHCR image pinned by sha256 digest, not a mutable tag."
        ) from exc


def toolchain_record(ref: str | None) -> dict[str, object]:
    """Create a normalized execution-environment receipt from a validated immutable ref."""
    validated = _validated_ref(ref, required=False)
    return {
        "toolchain_image_ref": validated,
        "immutable": validated is not None,
    }


def validate_toolchain_record(
    record: object,
    *,
    required: bool = False,
) -> dict[str, object]:
    """Validate a carried toolchain receipt without consulting mutable process state."""
    if record is None:
        if required:
            raise ToolchainProvenanceError(
                "Production provenance requires an immutable toolchain execution-environment record."
            )
        return toolchain_record(None)
    if not isinstance(record, Mapping):
        raise ToolchainProvenanceError(
            "Execution-environment provenance must be a toolchain record object."
        )
    if set(record) != _TOOLCHAIN_RECORD_KEYS:
        raise ToolchainProvenanceError(
            "Toolchain provenance record must contain exactly toolchain_image_ref and immutable."
        )
    immutable = record["immutable"]
    if not isinstance(immutable, bool):
        raise ToolchainProvenanceError(
            "Toolchain provenance immutable flag must be boolean."
        )
    ref = _validated_ref(record["toolchain_image_ref"], required=required)
    if ref is None:
        if immutable:
            raise ToolchainProvenanceError(
                "Toolchain provenance cannot claim immutable=true without an image digest."
            )
        return toolchain_record(None)
    if not immutable:
        raise ToolchainProvenanceError(
            "A digest-pinned toolchain provenance record must declare immutable=true."
        )
    return {
        "toolchain_image_ref": ref,
        "immutable": True,
    }


def _zip_write(zf: zipfile.ZipFile, name: str, data: bytes) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    zf.writestr(info, data)


def bind_bundle_toolchain(bundle: bytes, ref: str | None) -> bytes:
    """Bind the execution environment into manifest.json without changing member payload identities."""
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as source:
        names = source.namelist()
        if "manifest.json" not in names:
            raise ToolchainProvenanceError("Resin bundle has no manifest.json to bind toolchain provenance.")
        members = {name: source.read(name) for name in names}

    manifest = json.loads(members["manifest.json"])
    if not isinstance(manifest, dict):
        raise ToolchainProvenanceError("Resin manifest root is invalid.")
    manifest["execution_environment"] = toolchain_record(ref)
    members["manifest.json"] = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as target:
        for name in names:
            _zip_write(target, name, members[name])
    return output.getvalue()


def bind_compact_metadata(metadata: str, ref: str | None) -> str:
    payload = json.loads(metadata)
    if not isinstance(payload, dict):
        raise ToolchainProvenanceError("Compact resin metadata must be a JSON object.")
    payload["execution_environment"] = toolchain_record(ref)
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
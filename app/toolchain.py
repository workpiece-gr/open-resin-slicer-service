from __future__ import annotations

import io
import json
import os
import re
import zipfile


TOOLCHAIN_REF_ENV = "WORKPIECE_RESIN_TOOLCHAIN_REF"
_IMMUTABLE_GHCR_REF = re.compile(r"^ghcr\.io/[a-z0-9._/-]+@sha256:[0-9a-f]{64}$")


class ToolchainProvenanceError(RuntimeError):
    pass


def resolve_toolchain_ref(*, required: bool = False) -> str | None:
    """Return the immutable runtime toolchain ref, rejecting mutable/ambiguous identifiers."""
    value = os.environ.get(TOOLCHAIN_REF_ENV, "").strip()
    if not value:
        if required:
            raise ToolchainProvenanceError(
                f"{TOOLCHAIN_REF_ENV} must identify an immutable toolchain image for production."
            )
        return None
    if not _IMMUTABLE_GHCR_REF.fullmatch(value):
        raise ToolchainProvenanceError(
            f"{TOOLCHAIN_REF_ENV} must be a GHCR image pinned by sha256 digest, not a mutable tag."
        )
    return value


def toolchain_record(ref: str | None) -> dict[str, object]:
    return {
        "image_ref": ref,
        "immutable": ref is not None,
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
    engine = manifest.setdefault("engine", {})
    if not isinstance(engine, dict):
        raise ToolchainProvenanceError("Resin manifest engine field is invalid.")
    engine["toolchain"] = toolchain_record(ref)
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
    payload["toolchain"] = toolchain_record(ref)
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)

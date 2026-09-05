from __future__ import annotations

import os
import re
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response

from .engine import (
    PRUSA_SLICER_COMMIT,
    PRUSA_SLICER_VERSION,
    UVTOOLS_VERSION,
    EngineError,
    Orientation,
    build_review_bundle,
    compact_metadata,
    slice_native,
)
from .orientation import OrientationPlanError
from .orientation_pipeline import build_proxy_orientation_plan, proxy_orientation_plan_manifest
from .orientation_proxy import OrientationProxyError
from .orientation_screen import OrientationScreenError
from .profiles import ProfileError, ProfileRegistry
from .stl import StlValidationError, validate_stl_bytes
from .toolchain import (
    TOOLCHAIN_REF_ENV,
    ToolchainProvenanceError,
    bind_bundle_toolchain,
    bind_compact_metadata,
    resolve_toolchain_ref,
    toolchain_record,
)

PROFILE_ROOT = Path(os.environ.get("PROFILE_ROOT", "/app/profiles"))
SOURCE_CODE_URL = os.environ.get("SOURCE_CODE_URL", "https://github.com/workpiece-gr/open-resin-slicer-service")
PROJECT_TOKEN = os.environ.get("WORKPIECE_RESIN_PROJECT_API_TOKEN", "")
PRUSA_BIN = os.environ.get("PRUSA_SLICER_BIN", "/opt/prusaslicer/prusa-slicer")
UVTOOLS_CMD = os.environ.get("UVTOOLS_CMD", "/opt/uvtools/UVtoolsCmd")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(30 * 1024 * 1024)))
SLICE_TIMEOUT = int(os.environ.get("SLICE_TIMEOUT_SECONDS", "240"))
UVTOOLS_TIMEOUT = int(os.environ.get("UVTOOLS_TIMEOUT_SECONDS", "120"))
REJECT_CRITICAL = os.environ.get("REJECT_ON_CRITICAL_UVTOOLS_ISSUES", "1") not in {"0", "false", "False"}

registry = ProfileRegistry(PROFILE_ROOT)
app = FastAPI(title="Workpiece Open Resin Slicer Service", version="0.5.0")


def _binary_available(path: str) -> bool:
    from shutil import which
    return bool(which(path) or Path(path).is_file())


def _bearer(authorization: str | None) -> str:
    match = re.fullmatch(r"Bearer\s+(.+)", authorization or "", flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _authorize(authorization: str | None) -> None:
    if not PROJECT_TOKEN:
        raise HTTPException(status_code=503, detail="Resin project API is not enabled.")
    if _bearer(authorization) != PROJECT_TOKEN:
        raise HTTPException(status_code=401, detail="Unauthorized.")


async def _read_stl_upload(file: UploadFile) -> tuple[str, bytes]:
    filename = file.filename or ""
    if not filename.lower().endswith(".stl"):
        raise HTTPException(status_code=400, detail="Resin input must be an STL file.")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="STL exceeds the configured upload limit.")
    return filename, data


def _toolchain_health() -> dict:
    try:
        ref = resolve_toolchain_ref(required=False)
        return {**toolchain_record(ref), "valid": True}
    except ToolchainProvenanceError as exc:
        return {
            "toolchain_image_ref": os.environ.get(TOOLCHAIN_REF_ENV) or None,
            "immutable": False,
            "valid": False,
            "error": str(exc),
        }


@app.get("/health")
def health() -> dict:
    registry.reload()
    return {
        "ok": True,
        "service": "open-resin-slicer-service",
        "engines": {
            "prusaslicer": {"version": PRUSA_SLICER_VERSION, "commit": PRUSA_SLICER_COMMIT, "available": _binary_available(PRUSA_BIN)},
            "uvtools": {"version": UVTOOLS_VERSION, "available": _binary_available(UVTOOLS_CMD)},
            "toolchain": _toolchain_health(),
        },
        "candidate_profiles_ready": registry.candidate_ready,
        "production_profiles_ready": registry.production_ready,
        "project_api_enabled": bool(PROJECT_TOKEN),
        "artifact_contract": "source STL -> review 3MF -> intermediate SL1 -> printer-native CTB/GOO",
        "source": SOURCE_CODE_URL,
    }


@app.get("/source")
def source() -> RedirectResponse:
    return RedirectResponse(SOURCE_CODE_URL, status_code=307)


@app.get("/v1/profiles")
def profiles() -> dict:
    registry.reload()
    return registry.public_summary()


@app.post("/v1/orientation/proxy")
async def orientation_proxy(
    file: UploadFile = File(...),
    finalist_limit: int = Form(5),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Geometry-only orientation screening; never grants manufacturing authority."""
    _authorize(authorization)
    _, data = await _read_stl_upload(file)
    try:
        plan = build_proxy_orientation_plan(data, finalist_limit=finalist_limit)
    except (OrientationPlanError, OrientationProxyError, OrientationScreenError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return JSONResponse(
        content=proxy_orientation_plan_manifest(plan),
        headers={"Cache-Control": "private, no-store"},
    )


async def _slice_request(
    *,
    file: UploadFile,
    printer_profile: str,
    resin_profile: str,
    quality: str,
    rotate_x: float,
    rotate_y: float,
    rotate_z: float,
    authorization: str | None,
    production: bool,
) -> Response:
    _authorize(authorization)
    try:
        toolchain_ref = resolve_toolchain_ref(required=production)
    except ToolchainProvenanceError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    filename, data = await _read_stl_upload(file)
    try:
        stl = validate_stl_bytes(data)
        registry.reload()
        resolver = registry.resolve_production if production else registry.resolve_candidate
        printer, resin, quality_profile = resolver(printer_profile, resin_profile, quality)
        artifact = slice_native(
            data,
            original_name=filename,
            printer=printer,
            resin=resin,
            quality=quality_profile,
            orientation=Orientation(rotate_x, rotate_y, rotate_z),
            prusa_bin=PRUSA_BIN,
            uvtools_cmd=UVTOOLS_CMD,
            slice_timeout=SLICE_TIMEOUT,
            uvtools_timeout=UVTOOLS_TIMEOUT,
            reject_critical=REJECT_CRITICAL if production else False,
        )
    except (StlValidationError, ProfileError, EngineError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    authority = "production-authoritative" if production else "acceptance-candidate-only"
    bundle, bundle_filename = build_review_bundle(
        data, original_name=filename, artifact=artifact, authority=authority
    )
    bundle = bind_bundle_toolchain(bundle, toolchain_ref)
    metadata = bind_compact_metadata(compact_metadata(artifact), toolchain_ref)
    critical = sum(artifact.issue_summary.get(k, 0) for k in ("islands", "resin_traps", "suction_cups", "touching_bounds", "empty_layers"))
    headers = {
        "Content-Disposition": f'attachment; filename="{bundle_filename}"',
        "X-Workpiece-Authority": authority,
        "X-Workpiece-Source-SHA256": artifact.source_sha256,
        "X-Workpiece-Project-SHA256": artifact.project_sha256,
        "X-Workpiece-Intermediate-SHA256": artifact.intermediate_sha256,
        "X-Workpiece-Native-SHA256": artifact.native_sha256,
        "X-Workpiece-Printer-Profile": artifact.printer_profile,
        "X-Workpiece-Resin-Profile": artifact.resin_profile,
        "X-Workpiece-Quality-Profile": artifact.quality_profile,
        "X-Workpiece-STL-Triangles": str(stl["triangles"]),
        "X-Workpiece-UVtools-Critical-Issues": str(critical),
        "X-Workpiece-Resin-Metadata": metadata,
        "Cache-Control": "private, no-store",
    }
    if toolchain_ref is not None:
        headers["X-Workpiece-Toolchain-Ref"] = toolchain_ref
    return Response(content=bundle, media_type="application/zip", headers=headers)


@app.post("/v1/candidate")
async def candidate(
    file: UploadFile = File(...),
    printer_profile: str = Form(...),
    resin_profile: str = Form(...),
    quality: str = Form(...),
    rotate_x: float = Form(0.0),
    rotate_y: float = Form(0.0),
    rotate_z: float = Form(0.0),
    authorization: str | None = Header(default=None),
) -> Response:
    return await _slice_request(
        file=file, printer_profile=printer_profile, resin_profile=resin_profile, quality=quality,
        rotate_x=rotate_x, rotate_y=rotate_y, rotate_z=rotate_z,
        authorization=authorization, production=False,
    )


@app.post("/v1/project")
async def project(
    file: UploadFile = File(...),
    printer_profile: str = Form(...),
    resin_profile: str = Form(...),
    quality: str = Form(...),
    rotate_x: float = Form(0.0),
    rotate_y: float = Form(0.0),
    rotate_z: float = Form(0.0),
    authorization: str | None = Header(default=None),
) -> Response:
    return await _slice_request(
        file=file, printer_profile=printer_profile, resin_profile=resin_profile, quality=quality,
        rotate_x=rotate_x, rotate_y=rotate_y, rotate_z=rotate_z,
        authorization=authorization, production=True,
    )

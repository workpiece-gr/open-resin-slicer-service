from __future__ import annotations

import asyncio
import math
import os
import re
from pathlib import Path

from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
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
from .estimate import (
    EstimateError,
    build_estimate,
    engine_failure,
    measure_native,
    prepare_estimate,
)
from .orientation import OrientationPlanError
from .orientation_pipeline import build_proxy_orientation_plan, proxy_orientation_plan_manifest
from .orientation_proxy import OrientationProxyError
from .orientation_screen import DEFAULT_PROXY_FINALISTS, MAX_PROXY_FINALISTS, OrientationScreenError
from .profiles import ProfileError, ProfileRegistry
from .project import ProjectError, build_order_project, prepare_order_project
from .stl import StlValidationError, validate_stl_bytes

PROFILE_ROOT = Path(os.environ.get("PROFILE_ROOT", "/app/profiles"))
SOURCE_CODE_URL = os.environ.get("SOURCE_CODE_URL", "https://github.com/workpiece-gr/open-resin-slicer-service")
PROJECT_TOKEN = os.environ.get("WORKPIECE_RESIN_PROJECT_API_TOKEN", "")
PRUSA_BIN = os.environ.get("PRUSA_SLICER_BIN", "/opt/prusaslicer/prusa-slicer")
UVTOOLS_CMD = os.environ.get("UVTOOLS_CMD", "/opt/uvtools/UVtoolsCmd")
MAX_UPLOAD_BYTES = int(os.environ.get("MAX_UPLOAD_BYTES", str(30 * 1024 * 1024)))
SLICE_TIMEOUT = int(os.environ.get("SLICE_TIMEOUT_SECONDS", "240"))
UVTOOLS_TIMEOUT = int(os.environ.get("UVTOOLS_TIMEOUT_SECONDS", "120"))
# /v1/estimate and /v1/project share the single slice slot but may queue behind a running slice.
ESTIMATE_QUEUE_TIMEOUT = float(os.environ.get("ESTIMATE_QUEUE_TIMEOUT_SECONDS", "60"))
PROJECT_QUEUE_TIMEOUT = float(os.environ.get("PROJECT_QUEUE_TIMEOUT_SECONDS", "600"))
SERVICE_COMMIT = os.environ.get("WORKPIECE_RESIN_SERVICE_COMMIT", "")


def _bounded_concurrency(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be an integer between 1 and 4.") from exc
    if not 1 <= value <= 4:
        raise RuntimeError(f"{name} must be an integer between 1 and 4.")
    return value


def _capacity_wait_seconds() -> float:
    raw = os.environ.get("RESOURCE_CAPACITY_WAIT_SECONDS", "0.25").strip()
    try:
        value = float(raw)
    except ValueError as exc:
        raise RuntimeError("RESOURCE_CAPACITY_WAIT_SECONDS must be between 0.01 and 10 seconds.") from exc
    if not math.isfinite(value) or not 0.01 <= value <= 10.0:
        raise RuntimeError("RESOURCE_CAPACITY_WAIT_SECONDS must be between 0.01 and 10 seconds.")
    return value


def _project_quantity_limit() -> int:
    raw = os.environ.get("MAX_PROJECT_QUANTITY", "100").strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("MAX_PROJECT_QUANTITY must be an integer between 1 and 1000.") from exc
    if not 1 <= value <= 1000:
        raise RuntimeError("MAX_PROJECT_QUANTITY must be an integer between 1 and 1000.")
    return value


def _registry_snapshot() -> ProfileRegistry:
    """Load an isolated profile snapshot for each request path.

    ProfileRegistry exposes mutable implementation details (including reload and metadata
    dictionaries), so sharing one process-global instance would reintroduce cross-request
    mutation/race risk. Profile loading is cheap relative to slicing and stays outside the
    external-engine hot path.
    """
    return ProfileRegistry(PROFILE_ROOT)


MAX_CONCURRENT_SLICES = _bounded_concurrency("MAX_CONCURRENT_SLICES", 1)
MAX_CONCURRENT_PROXY_JOBS = _bounded_concurrency("MAX_CONCURRENT_PROXY_JOBS", 1)
RESOURCE_CAPACITY_WAIT_SECONDS = _capacity_wait_seconds()
MAX_PROJECT_QUANTITY = _project_quantity_limit()
SLICE_CAPACITY = asyncio.Semaphore(MAX_CONCURRENT_SLICES)
PROXY_CAPACITY = asyncio.Semaphore(MAX_CONCURRENT_PROXY_JOBS)

app = FastAPI(title="Workpiece Open Resin Slicer Service", version="1.0.0")


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


async def _acquire_capacity(semaphore: asyncio.Semaphore, workload: str) -> None:
    try:
        await asyncio.wait_for(semaphore.acquire(), timeout=RESOURCE_CAPACITY_WAIT_SECONDS)
    except TimeoutError as exc:
        raise HTTPException(
            status_code=429,
            detail=f"{workload} capacity is busy; retry shortly.",
            headers={"Retry-After": "2"},
        ) from exc


def _coded_http(code: str, message: str, status: int, headers: dict[str, str] | None = None) -> HTTPException:
    return HTTPException(status_code=status, detail={"code": code, "message": message}, headers=headers)


async def _read_stl_upload(file: UploadFile) -> tuple[str, bytes]:
    filename = file.filename or ""
    if not filename.lower().endswith(".stl"):
        raise HTTPException(status_code=400, detail="Resin input must be an STL file.")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if not data or len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="STL exceeds the configured upload limit.")
    return filename, data


@app.get("/health")
def health() -> dict:
    registry = _registry_snapshot()
    return {
        "ok": True,
        "service": "open-resin-slicer-service",
        "engines": {
            "prusaslicer": {"version": PRUSA_SLICER_VERSION, "commit": PRUSA_SLICER_COMMIT, "available": _binary_available(PRUSA_BIN)},
            "uvtools": {"version": UVTOOLS_VERSION, "available": _binary_available(UVTOOLS_CMD)},
        },
        "service_commit": SERVICE_COMMIT or None,
        "resource_limits": {
            "max_upload_bytes": MAX_UPLOAD_BYTES,
            "max_concurrent_slices": MAX_CONCURRENT_SLICES,
            "max_concurrent_proxy_jobs": MAX_CONCURRENT_PROXY_JOBS,
            "max_project_quantity": MAX_PROJECT_QUANTITY,
            "capacity_wait_seconds": RESOURCE_CAPACITY_WAIT_SECONDS,
            "slice_timeout_seconds": SLICE_TIMEOUT,
            "uvtools_timeout_seconds": UVTOOLS_TIMEOUT,
        },
        "profiles_ready": registry.candidate_ready,
        "project_api_enabled": bool(PROJECT_TOKEN),
        "source": SOURCE_CODE_URL,
    }


@app.get("/source")
def source() -> RedirectResponse:
    return RedirectResponse(SOURCE_CODE_URL, status_code=307)


@app.get("/v1/profiles")
def profiles() -> dict:
    return _registry_snapshot().public_summary()


@app.post("/v1/orientation/proxy")
async def orientation_proxy(
    file: UploadFile = File(...),
    finalist_limit: int = Form(DEFAULT_PROXY_FINALISTS),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Geometry-only orientation screening (no slicing)."""
    _authorize(authorization)
    if isinstance(finalist_limit, bool) or not 1 <= finalist_limit <= MAX_PROXY_FINALISTS:
        raise HTTPException(
            status_code=422,
            detail=f"finalist_limit must be between 1 and the {MAX_PROXY_FINALISTS}-candidate safety cap.",
        )

    _, data = await _read_stl_upload(file)
    await _acquire_capacity(PROXY_CAPACITY, "Orientation proxy")
    try:
        try:
            plan = await run_in_threadpool(
                build_proxy_orientation_plan,
                data,
                finalist_limit=finalist_limit,
            )
        except (OrientationPlanError, OrientationProxyError, OrientationScreenError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        PROXY_CAPACITY.release()
    return JSONResponse(
        content=proxy_orientation_plan_manifest(plan),
        headers={"Cache-Control": "private, no-store"},
    )


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
    """Direct slice of one part in a proposed orientation; returns the review bundle ZIP."""
    _authorize(authorization)
    filename, data = await _read_stl_upload(file)
    await _acquire_capacity(SLICE_CAPACITY, "Resin slicing")
    try:
        try:
            stl = await run_in_threadpool(validate_stl_bytes, data)
            registry = _registry_snapshot()
            printer, resin, quality_profile = registry.resolve_candidate(
                printer_profile,
                resin_profile,
                quality,
            )
            artifact = await run_in_threadpool(
                slice_native,
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
                reject_critical=False,
            )
            authority = "review-candidate"
            bundle, bundle_filename = await run_in_threadpool(
                build_review_bundle,
                data,
                original_name=filename,
                artifact=artifact,
                authority=authority,
            )
        except (StlValidationError, ProfileError, EngineError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
    finally:
        SLICE_CAPACITY.release()

    metadata = compact_metadata(artifact)
    critical = sum(artifact.issue_summary.get(k, 0) for k in ("islands", "resin_traps", "suction_cups", "touching_bounds", "empty_layers"))
    headers = {
        "Content-Disposition": f'attachment; filename="{bundle_filename}"',
        "X-Workpiece-Authority": authority,
        "X-Workpiece-Source-SHA256": artifact.source_sha256,
        "X-Workpiece-Project-SHA256": artifact.project_sha256,
        "X-Workpiece-Effective-Config-SHA256": artifact.effective_config_sha256,
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
    return Response(content=bundle, media_type="application/zip", headers=headers)


@app.post("/v1/estimate")
async def estimate(
    file: UploadFile = File(...),
    printer_profile: str = Form(...),
    resin_profile: str = Form(...),
    quality: str = Form(...),
    rotate_x: float = Form(0.0),
    rotate_y: float = Form(0.0),
    rotate_z: float = Form(0.0),
    authorization: str | None = Header(default=None),
) -> JSONResponse:
    """Lean per-part measurements for instant quotes: no price, no retained artifacts."""
    _authorize(authorization)
    try:
        filename, data = await _read_stl_upload(file)
    except HTTPException as exc:
        raise EstimateError("INVALID_STL", str(exc.detail), status=exc.status_code).http() from exc
    try:
        prepared = await run_in_threadpool(
            prepare_estimate,
            data,
            registry=_registry_snapshot(),
            printer_id=printer_profile,
            resin_id=resin_profile,
            quality_id=quality,
            orientation=Orientation(rotate_x, rotate_y, rotate_z),
        )
    except EstimateError as exc:
        raise exc.http() from exc

    try:
        await asyncio.wait_for(SLICE_CAPACITY.acquire(), timeout=ESTIMATE_QUEUE_TIMEOUT)
    except TimeoutError as exc:
        raise EstimateError("BUSY", "Resin slicing capacity is busy; retry shortly.", status=429).http(
            {"Retry-After": "30"}
        ) from exc
    try:
        try:
            artifact = await run_in_threadpool(
                slice_native,
                data,
                original_name=filename,
                printer=prepared.printer,
                resin=prepared.resin,
                quality=prepared.quality,
                orientation=prepared.orientation,
                prusa_bin=PRUSA_BIN,
                uvtools_cmd=UVTOOLS_CMD,
                slice_timeout=SLICE_TIMEOUT,
                uvtools_timeout=UVTOOLS_TIMEOUT,
                reject_critical=False,
            )
            metrics = await run_in_threadpool(
                measure_native, artifact, uvtools_cmd=UVTOOLS_CMD, uvtools_timeout=UVTOOLS_TIMEOUT
            )
        except RuntimeError as exc:  # EngineError / SlicedFinalistAdapterError
            raise engine_failure(exc).http() from exc
    finally:
        SLICE_CAPACITY.release()

    try:
        payload = build_estimate(prepared, artifact, metrics, service_commit=SERVICE_COMMIT)
    except EstimateError as exc:
        raise exc.http() from exc
    return JSONResponse(content=payload, headers={"Cache-Control": "private, no-store"})


@app.post("/v1/project")
async def project(
    file: UploadFile = File(...),
    printer_profile: str = Form(...),
    resin_profile: str = Form(...),
    quality: str = Form(...),
    requested_quantity: int = Form(1),
    finalist_limit: int = Form(DEFAULT_PROXY_FINALISTS),
    authorization: str | None = Header(default=None),
) -> Response:
    """Order project: orient, plan plates for the quantity, slice every plate; returns a ZIP.

    Background job for the website after an order. The ZIP holds one PrusaSlicer 3MF and
    one CTB per plate, the effective config and ``project.json``. The workshop opens and
    reviews every plate project before printing.
    """
    _authorize(authorization)
    if isinstance(requested_quantity, bool) or not 1 <= requested_quantity <= MAX_PROJECT_QUANTITY:
        raise _coded_http("INVALID_QUANTITY", f"requested_quantity must be between 1 and {MAX_PROJECT_QUANTITY}.", 422)
    if isinstance(finalist_limit, bool) or not 1 <= finalist_limit <= MAX_PROXY_FINALISTS:
        raise _coded_http("INVALID_FINALIST_LIMIT", f"finalist_limit must be between 1 and {MAX_PROXY_FINALISTS}.", 422)
    try:
        filename, data = await _read_stl_upload(file)
    except HTTPException as exc:
        raise _coded_http("INVALID_STL", str(exc.detail), exc.status_code) from exc

    try:
        prepared = await run_in_threadpool(
            prepare_order_project,
            data,
            original_name=filename,
            requested_quantity=requested_quantity,
            registry=_registry_snapshot(),
            printer_id=printer_profile,
            resin_id=resin_profile,
            quality_id=quality,
        )
    except ProjectError as exc:
        raise _coded_http(exc.code, str(exc), exc.status) from exc

    try:
        await asyncio.wait_for(SLICE_CAPACITY.acquire(), timeout=PROJECT_QUEUE_TIMEOUT)
    except TimeoutError as exc:
        raise _coded_http("BUSY", "Resin slicing capacity is busy; retry later.", 429, {"Retry-After": "120"}) from exc
    try:
        result = await run_in_threadpool(
            build_order_project,
            prepared,
            prusa_bin=PRUSA_BIN,
            uvtools_cmd=UVTOOLS_CMD,
            slice_timeout=SLICE_TIMEOUT,
            uvtools_timeout=UVTOOLS_TIMEOUT,
            finalist_limit=finalist_limit,
            service_commit=SERVICE_COMMIT or None,
        )
    except ProjectError as exc:
        raise _coded_http(exc.code, str(exc), exc.status) from exc
    finally:
        SLICE_CAPACITY.release()

    manifest = result.manifest
    headers = {
        "Content-Disposition": f'attachment; filename="{result.zip_filename}"',
        "X-Workpiece-Source-SHA256": manifest["source"]["sha256"],
        "X-Workpiece-Plate-Count": str(manifest["totals"]["plates"]),
        "X-Workpiece-Instance-Count": str(manifest["totals"]["instances"]),
        "X-Workpiece-Project-Schema": manifest["schema"],
        "Cache-Control": "private, no-store",
    }
    return Response(content=result.zip_bytes, media_type="application/zip", headers=headers)

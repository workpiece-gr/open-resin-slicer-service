from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path

from .orientation_pipeline import proxy_orientation_plan_manifest
from .orientation_sliced import sliced_orientation_manifest
from .production_orchestration import (
    PRODUCTION_ORDER_AUTHORITY,
    SelectedProductionOrderResult,
)


PRODUCTION_BUNDLE_SCHEMA = "workpiece-resin-selected-production-bundle-v1"


class ProductionBundleError(ValueError):
    pass


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exact_bytes(name: str, data: bytes, expected_sha256: str) -> None:
    if not isinstance(data, bytes) or not data:
        raise ProductionBundleError(f"{name} must contain exact non-empty bytes.")
    if _sha(data) != str(expected_sha256).strip().lower():
        raise ProductionBundleError(f"{name} bytes do not match their exact SHA-256 receipt.")


def _safe_stem(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", Path(name).stem).strip("-._") or "workpiece"


def _zip_write(
    archive: zipfile.ZipFile,
    name: str,
    data: bytes,
    *,
    stored: bool = False,
) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED
    info.external_attr = 0o644 << 16
    archive.writestr(info, data)


def build_selected_production_bundle(
    *,
    source_stl: bytes,
    original_name: str,
    result: SelectedProductionOrderResult,
) -> tuple[bytes, str]:
    """Retain the complete selected multi-plate production evidence chain in one ZIP."""
    _exact_bytes("source STL", source_stl, result.source_sha256)
    manifest = dict(result.order_manifest)
    if manifest.get("authority") != PRODUCTION_ORDER_AUTHORITY:
        raise ProductionBundleError(
            "Selected production bundle requires a production-authoritative order manifest."
        )
    environment = manifest.get("execution_environment")
    if not isinstance(environment, dict):
        raise ProductionBundleError("Production order manifest is missing execution_environment.")
    if environment.get("toolchain_image_ref") != result.toolchain_image_ref or environment.get("immutable") is not True:
        raise ProductionBundleError(
            "Production order manifest is not bound to the exact immutable runtime toolchain image."
        )

    source_record = manifest.get("source")
    if not isinstance(source_record, dict):
        raise ProductionBundleError("Production order manifest is missing its source record.")
    source_filename = str(source_record.get("name", "")).strip()
    if not source_filename or "/" in source_filename or "\\" in source_filename:
        raise ProductionBundleError("Production order source filename must be a simple retained filename.")
    if source_record.get("sha256") != result.source_sha256:
        raise ProductionBundleError("Production order source hash differs from the exact source STL bytes.")
    requested_name = str(original_name).strip().replace("\\", "/").rsplit("/", 1)[-1]
    if requested_name != source_filename:
        raise ProductionBundleError(
            "Production bundle source filename differs from the selected order source record."
        )

    sliced = result.sliced_execution
    if sliced.selected_result is None:
        raise ProductionBundleError("Production order has no retained selected sliced winner.")
    selected = sliced.selected_result.artifact
    selected_payloads = (
        ("selected review 3MF", selected.project_bytes, selected.project_sha256),
        (
            "selected effective config",
            selected.effective_config_bytes,
            selected.effective_config_sha256,
        ),
        ("selected intermediate SL1", selected.intermediate_bytes, selected.intermediate_sha256),
        ("selected printer-native file", selected.bytes, selected.native_sha256),
    )
    for name, data, digest in selected_payloads:
        _exact_bytes(name, data, digest)

    plan = result.selected_orientation_plan
    expected_selected_hashes = (
        plan.review_project_sha256,
        plan.effective_config_sha256,
        plan.intermediate_sha256,
        plan.native_sha256,
    )
    actual_selected_hashes = (
        selected.project_sha256,
        selected.effective_config_sha256,
        selected.intermediate_sha256,
        selected.native_sha256,
    )
    if actual_selected_hashes != expected_selected_hashes:
        raise ProductionBundleError(
            "Retained selected winner bytes do not match the selected orientation plan receipts."
        )

    manifest_plates = manifest.get("plates")
    if not isinstance(manifest_plates, list):
        raise ProductionBundleError("Production order manifest plates must be a list.")
    by_index = {item.get("plate_index"): item for item in manifest_plates if isinstance(item, dict)}
    result_indices = [item.plate_index for item in result.plates]
    if len(by_index) != len(manifest_plates) or set(by_index) != set(result_indices):
        raise ProductionBundleError(
            "Production order manifest does not cover the exact physical plate result set."
        )

    stem = _safe_stem(source_filename)
    selected_files = {
        "review_3mf": f"selected/{stem}-selected-review.3mf",
        "effective_config": f"selected/{stem}-selected-effective.ini",
        "intermediate_sl1": f"selected/{stem}-selected-intermediate.sl1",
        "printer_native": f"selected/{stem}-selected{Path(selected.filename).suffix.lower() or '.native'}",
    }
    orientation_files = {
        "proxy": "orientation-proxy.json",
        "sliced": "orientation-sliced.json",
    }
    issue_files: dict[str, str] = {}

    retained: list[tuple[str, bytes, bool]] = [
        (source_filename, source_stl, False),
        (selected_files["review_3mf"], selected.project_bytes, True),
        (selected_files["effective_config"], selected.effective_config_bytes, False),
        (selected_files["intermediate_sl1"], selected.intermediate_bytes, True),
        (selected_files["printer_native"], selected.bytes, True),
    ]

    for plate in result.plates:
        record = by_index[plate.plate_index]
        files = record.get("files")
        if not isinstance(files, dict):
            raise ProductionBundleError(
                f"Production order plate {plate.plate_index} is missing retained file receipts."
            )
        project_record = files.get("review_3mf")
        intermediate_record = files.get("intermediate_sl1")
        native_record = files.get("printer_native")
        if not all(isinstance(item, dict) for item in (project_record, intermediate_record, native_record)):
            raise ProductionBundleError(
                f"Production order plate {plate.plate_index} has incomplete retained file receipts."
            )

        materialized = plate.materialization.project
        artifact = plate.native_execution.artifact
        _exact_bytes("materialized plate 3MF", materialized.bytes, materialized.sha256)
        _exact_bytes("plate intermediate SL1", artifact.intermediate_bytes, artifact.intermediate_sha256)
        _exact_bytes("plate printer-native file", artifact.native_bytes, artifact.native_sha256)

        expected_names = (
            plate.retained_project_filename,
            plate.retained_intermediate_filename,
            plate.retained_native_filename,
        )
        actual_names = (
            project_record.get("name"),
            intermediate_record.get("name"),
            native_record.get("name"),
        )
        if actual_names != expected_names:
            raise ProductionBundleError(
                f"Production order plate {plate.plate_index} retained filenames differ from orchestration receipts."
            )
        expected_hashes = (
            materialized.sha256,
            artifact.intermediate_sha256,
            artifact.native_sha256,
        )
        actual_hashes = (
            project_record.get("sha256"),
            intermediate_record.get("sha256"),
            native_record.get("sha256"),
        )
        if actual_hashes != expected_hashes:
            raise ProductionBundleError(
                f"Production order plate {plate.plate_index} file hashes differ from exact retained bytes."
            )
        if record.get("issues") != artifact.issue_summary:
            raise ProductionBundleError(
                f"Production order plate {plate.plate_index} issue receipt differs from final native execution."
            )

        issue_name = f"plate-{plate.plate_index:03d}-uvtools-issues.txt"
        issue_files[str(plate.plate_index)] = issue_name
        retained.extend(
            [
                (plate.retained_project_filename, materialized.bytes, True),
                (plate.retained_intermediate_filename, artifact.intermediate_bytes, True),
                (plate.retained_native_filename, artifact.native_bytes, True),
                (issue_name, artifact.issue_text.encode("utf-8", errors="replace"), False),
            ]
        )

    proxy_payload = json.dumps(
        proxy_orientation_plan_manifest(result.proxy_plan),
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    sliced_payload = json.dumps(
        sliced_orientation_manifest(result.sliced_execution.validation),
        indent=2,
        sort_keys=True,
    ).encode("utf-8")
    retained.extend(
        [
            (orientation_files["proxy"], proxy_payload, False),
            (orientation_files["sliced"], sliced_payload, False),
        ]
    )

    names = [name for name, _, _ in retained]
    if len(names) != len(set(names)) or "manifest.json" in names:
        raise ProductionBundleError("Production bundle retained filenames are not unique.")

    manifest["bundle"] = {
        "schema": PRODUCTION_BUNDLE_SCHEMA,
        "selected_winner_files": selected_files,
        "orientation_evidence_files": orientation_files,
        "plate_uvtools_issue_files": issue_files,
        "retained_member_count": len(retained) + 1,
        "production_enablement_performed": False,
    }
    manifest_payload = json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8")

    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", allowZip64=True) as archive:
        for name, data, stored in retained:
            _zip_write(archive, name, data, stored=stored)
        _zip_write(archive, "manifest.json", manifest_payload)
    return output.getvalue(), f"{stem}-workpiece-resin-production.zip"

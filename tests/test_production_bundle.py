import hashlib
import io
import json
import zipfile
from types import SimpleNamespace

import pytest

import app.production_bundle as production_bundle


TOOLCHAIN = "ghcr.io/workpiece-gr/resin-slicer-toolchain@sha256:" + "a" * 64
SOURCE = b"exact source stl"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _result():
    selected_project = b"selected review 3mf"
    selected_config = b"selected effective config"
    selected_intermediate = b"selected intermediate sl1"
    selected_native = b"selected native ctb"
    selected = SimpleNamespace(
        project_bytes=selected_project,
        project_sha256=_sha(selected_project),
        effective_config_bytes=selected_config,
        effective_config_sha256=_sha(selected_config),
        intermediate_bytes=selected_intermediate,
        intermediate_sha256=_sha(selected_intermediate),
        bytes=selected_native,
        native_sha256=_sha(selected_native),
        filename="selected.ctb",
    )

    plate_results = []
    manifest_plates = []
    for index in (1, 2):
        project = f"plate {index} materialized 3mf".encode()
        intermediate = f"plate {index} intermediate sl1".encode()
        native = f"plate {index} native ctb".encode()
        project_name = f"part-plate-{index:03d}-materialized.3mf"
        intermediate_name = f"part-plate-{index:03d}-intermediate.sl1"
        native_name = f"part-plate-{index:03d}.ctb"
        issues = {"islands": 0, "overhangs": 0}
        artifact = SimpleNamespace(
            intermediate_bytes=intermediate,
            intermediate_sha256=_sha(intermediate),
            native_bytes=native,
            native_sha256=_sha(native),
            issue_summary=issues,
            issue_text=f"plate {index} issues: 0\n",
        )
        plate_results.append(
            SimpleNamespace(
                plate_index=index,
                materialization=SimpleNamespace(
                    project=SimpleNamespace(bytes=project, sha256=_sha(project))
                ),
                native_execution=SimpleNamespace(artifact=artifact),
                retained_project_filename=project_name,
                retained_intermediate_filename=intermediate_name,
                retained_native_filename=native_name,
            )
        )
        manifest_plates.append(
            {
                "plate_index": index,
                "files": {
                    "review_3mf": {"name": project_name, "sha256": _sha(project)},
                    "intermediate_sl1": {
                        "name": intermediate_name,
                        "sha256": _sha(intermediate),
                    },
                    "printer_native": {"name": native_name, "sha256": _sha(native)},
                },
                "issues": issues,
            }
        )

    order_manifest = {
        "schema": "workpiece-resin-order-manifest-v4",
        "authority": "production-authoritative",
        "source": {"name": "part.stl", "sha256": _sha(SOURCE)},
        "execution_environment": {
            "toolchain_image_ref": TOOLCHAIN,
            "immutable": True,
        },
        "plates": manifest_plates,
    }
    return SimpleNamespace(
        source_sha256=_sha(SOURCE),
        toolchain_image_ref=TOOLCHAIN,
        order_manifest=order_manifest,
        proxy_plan=object(),
        sliced_execution=SimpleNamespace(
            selected_result=SimpleNamespace(artifact=selected),
            validation=object(),
        ),
        selected_orientation_plan=SimpleNamespace(
            review_project_sha256=selected.project_sha256,
            effective_config_sha256=selected.effective_config_sha256,
            intermediate_sha256=selected.intermediate_sha256,
            native_sha256=selected.native_sha256,
        ),
        plates=tuple(plate_results),
    )


def test_production_bundle_retains_complete_multi_plate_chain(monkeypatch):
    monkeypatch.setattr(
        production_bundle,
        "proxy_orientation_plan_manifest",
        lambda plan: {"schema": "proxy", "source_sha256": _sha(SOURCE)},
    )
    monkeypatch.setattr(
        production_bundle,
        "sliced_orientation_manifest",
        lambda validation: {"schema": "sliced", "status": "selected"},
    )
    result = _result()
    bundle, filename = production_bundle.build_selected_production_bundle(
        source_stl=SOURCE,
        original_name="customer/part.stl",
        result=result,
    )

    assert filename == "part-workpiece-resin-production.zip"
    with zipfile.ZipFile(io.BytesIO(bundle), "r") as archive:
        names = archive.namelist()
        assert len(names) == len(set(names))
        assert archive.read("part.stl") == SOURCE
        assert archive.read("part-plate-001-materialized.3mf") == b"plate 1 materialized 3mf"
        assert archive.read("part-plate-002.ctb") == b"plate 2 native ctb"
        assert archive.read("plate-001-uvtools-issues.txt") == b"plate 1 issues: 0\n"
        assert json.loads(archive.read("orientation-proxy.json"))["schema"] == "proxy"
        assert json.loads(archive.read("orientation-sliced.json"))["schema"] == "sliced"
        manifest = json.loads(archive.read("manifest.json"))

    assert manifest["schema"] == "workpiece-resin-order-manifest-v4"
    assert manifest["execution_environment"] == {
        "toolchain_image_ref": TOOLCHAIN,
        "immutable": True,
    }
    assert manifest["bundle"]["schema"] == "workpiece-resin-selected-production-bundle-v1"
    assert manifest["bundle"]["selected_winner_files"]["review_3mf"] == "selected/part-selected-review.3mf"
    assert manifest["bundle"]["plate_uvtools_issue_files"] == {
        "1": "plate-001-uvtools-issues.txt",
        "2": "plate-002-uvtools-issues.txt",
    }
    assert manifest["bundle"]["production_enablement_performed"] is False


def test_production_bundle_rejects_changed_source_bytes(monkeypatch):
    monkeypatch.setattr(production_bundle, "proxy_orientation_plan_manifest", lambda plan: {})
    monkeypatch.setattr(production_bundle, "sliced_orientation_manifest", lambda validation: {})
    with pytest.raises(production_bundle.ProductionBundleError, match="source STL bytes"):
        production_bundle.build_selected_production_bundle(
            source_stl=b"changed source",
            original_name="part.stl",
            result=_result(),
        )


def test_production_bundle_rejects_changed_plate_native_bytes(monkeypatch):
    monkeypatch.setattr(production_bundle, "proxy_orientation_plan_manifest", lambda plan: {})
    monkeypatch.setattr(production_bundle, "sliced_orientation_manifest", lambda validation: {})
    result = _result()
    plate = result.plates[0]
    plate.native_execution.artifact.native_bytes = b"tampered native"
    with pytest.raises(production_bundle.ProductionBundleError, match="plate printer-native file bytes"):
        production_bundle.build_selected_production_bundle(
            source_stl=SOURCE,
            original_name="part.stl",
            result=result,
        )

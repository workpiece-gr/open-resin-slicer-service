import hashlib
import io
import json
import zipfile

import pytest

from app.coordinate_mapping import ManufacturingDisplayTransform
from app.materialization_selected import (
    SelectedMaterializationError,
    materialize_selected_plate_project,
)
from app.orientation_plate import (
    MANUFACTURING_ENVELOPE_COORDINATE_SPACE,
    SelectedOrientationPlatePlan,
)
from app.placement import Envelope2D
from app.plate import PrinterPlatePlan, plan_rectangular_instances
from app.profiles import ProfileRegistry


MODEL_MEMBER = "3D/3dmodel.model"


def _project() -> bytes:
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
        ' <resources>\n'
        '  <object id="1" type="model"><mesh><vertices/><triangles/></mesh></object>\n'
        ' </resources>\n'
        ' <build>\n'
        '  <item objectid="1" transform="1 0 0 0 1 0 0 0 1 10 20 0" printable="1"/>\n'
        ' </build>\n'
        '</model>\n'
    ).encode()
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr(MODEL_MEMBER, model)
        archive.writestr("Metadata/Slic3r_PE_sla_support_points.txt", b"supports")
    return output.getvalue()


def _registry(tmp_path) -> ProfileRegistry:
    printers = tmp_path / "printers"
    printers.mkdir()
    (printers / "printer-a.ini").write_text("printer_technology = SLA\n", encoding="utf-8")
    (printers / "printer-a.json").write_text(
        json.dumps(
            {
                "id": "printer-a",
                "candidate_ready": True,
                "production_ready": False,
                "config": "printers/printer-a.ini",
                "display_width_mm": 80,
                "display_height_mm": 60,
                "manufacturing_envelope_width_mm": 80,
                "manufacturing_envelope_depth_mm": 60,
                "manufacturing_envelope_coordinate_mapping": "validated",
                "manufacturing_to_display_transform": {
                    "origin_display_x_mm": 0,
                    "origin_display_y_mm": 60,
                    "x_axis_display_x": 1,
                    "x_axis_display_y": 0,
                    "y_axis_display_x": 0,
                    "y_axis_display_y": -1,
                },
            }
        ),
        encoding="utf-8",
    )
    return ProfileRegistry(tmp_path)


def _selected(source: bytes, transform: ManufacturingDisplayTransform) -> SelectedOrientationPlatePlan:
    plan = plan_rectangular_instances(
        footprint_width_mm=45,
        footprint_depth_mm=25,
        quantity=2,
        plate_width_mm=80,
        plate_depth_mm=60,
        spacing_mm=5,
        edge_margin_mm=5,
    )
    assert plan.rotation_z_deg == 90
    return SelectedOrientationPlatePlan(
        orientation_deg=(15.0, 0.0, 0.0),
        source_sha256="d" * 64,
        review_project_sha256=hashlib.sha256(source).hexdigest(),
        effective_config_sha256="9" * 64,
        intermediate_sha256="b" * 64,
        native_sha256="c" * 64,
        pretranslation_envelope=Envelope2D(10, 55, 15, 40),
        printer_plate_plan=PrinterPlatePlan(
            printer_profile_id="printer-a",
            manufacturing_envelope_coordinate_mapping="validated",
            plan=plan,
        ),
        pretranslation_coordinate_space=MANUFACTURING_ENVELOPE_COORDINATE_SPACE,
        native_display_envelope=Envelope2D(10, 55, 20, 45),
        manufacturing_to_display_transform=transform,
    )


def test_selected_plate_materializer_maps_centres_and_reflected_rotation(tmp_path):
    source = _project()
    registry = _registry(tmp_path)
    selected = _selected(
        source,
        registry.printer_manufacturing_display_transform("printer-a"),
    )

    result = materialize_selected_plate_project(
        selected,
        registry=registry,
        plate_index=1,
        selected_review_project_bytes=source,
    )

    assert [
        (item.instance_index, item.target_display_x_mm, item.target_display_y_mm, item.rotation_z_deg)
        for item in result.display_placements
    ] == [
        (1, 17.5, 32.5, -90),
        (2, 47.5, 32.5, -90),
    ]
    assert result.project.instance_indices == (1, 2)
    assert result.project.sha256 == hashlib.sha256(result.project.bytes).hexdigest()
    with zipfile.ZipFile(io.BytesIO(result.project.bytes), "r") as archive:
        assert archive.read("Metadata/Slic3r_PE_sla_support_points.txt") == b"supports"


def test_selected_plate_materializer_rejects_transform_drift(tmp_path):
    source = _project()
    registry = _registry(tmp_path)
    current = registry.printer_manufacturing_display_transform("printer-a")
    stale = ManufacturingDisplayTransform(
        origin_display_x_mm=1,
        origin_display_y_mm=60,
        x_axis_display_x=1,
        x_axis_display_y=0,
        y_axis_display_x=0,
        y_axis_display_y=-1,
    )
    selected = _selected(source, stale)
    assert stale != current
    with pytest.raises(SelectedMaterializationError, match="changed after"):
        materialize_selected_plate_project(
            selected,
            registry=registry,
            plate_index=1,
            selected_review_project_bytes=source,
        )

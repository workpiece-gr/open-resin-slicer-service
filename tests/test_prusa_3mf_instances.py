import hashlib
import io
import re
import zipfile

import pytest

from app.placement import Envelope2D
from app.prusa_3mf_instances import (
    DisplayInstancePlacement,
    ThreeMFMaterializationError,
    compose_display_instance_transform,
    materialize_prusa_project_instances,
)


MODEL = "3D/3dmodel.model"
SOURCE_TRANSFORM = "1 0 0 0 1 0 0 0 1 100 200 5"


def _project(*, build_items: int = 1) -> bytes:
    items = "\n".join(
        f'  <item objectid="1" transform="{SOURCE_TRANSFORM}" printable="1"/>'
        for _ in range(build_items)
    )
    model = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<model unit="millimeter" xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02">\n'
        ' <resources>\n'
        '  <object id="1" type="model"><mesh><vertices/><triangles/></mesh></object>\n'
        ' </resources>\n'
        ' <build>\n'
        f'{items}\n'
        ' </build>\n'
        '</model>\n'
    ).encode()
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w") as archive:
        model_info = zipfile.ZipInfo(MODEL, date_time=(2026, 1, 2, 3, 4, 6))
        model_info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(model_info, model)
        support_info = zipfile.ZipInfo("Metadata/Slic3r_PE_sla_support_points.txt", date_time=(2026, 1, 2, 3, 4, 6))
        support_info.compress_type = zipfile.ZIP_STORED
        archive.writestr(support_info, b"support-metadata-must-not-change")
        archive.writestr("Metadata/Slic3r_PE.config", b"resolved-config-metadata")
    return out.getvalue()


def _model_xml(project: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(project), "r") as archive:
        return archive.read(MODEL)


def _support_metadata(project: bytes) -> bytes:
    with zipfile.ZipFile(io.BytesIO(project), "r") as archive:
        return archive.read("Metadata/Slic3r_PE_sla_support_points.txt")


def _transforms(project: bytes) -> list[tuple[float, ...]]:
    matches = re.findall(rb'<item\b[^<>]*\btransform="([^"]+)"[^<>]*/>', _model_xml(project))
    return [tuple(float(value) for value in raw.split()) for raw in matches]


def test_composes_world_translation_from_exact_envelope_center_not_model_origin():
    source = tuple(float(value) for value in SOURCE_TRANSFORM.split())
    result = compose_display_instance_transform(
        source,
        source_display_envelope=Envelope2D(100, 120, 200, 210),
        placement=DisplayInstancePlacement(1, 30, 40, 0),
    )
    # Original local->world translation is (100,200,5); exact supported envelope center is
    # (110,205), so moving that center to (30,40) yields world translation (20,35,5).
    assert result == (1, 0, 0, 0, 1, 0, 0, 0, 1, 20, 35, 5)


def test_composes_90_degree_rotation_around_supported_envelope_center():
    source = tuple(float(value) for value in SOURCE_TRANSFORM.split())
    result = compose_display_instance_transform(
        source,
        source_display_envelope=Envelope2D(100, 120, 200, 210),
        placement=DisplayInstancePlacement(1, 30, 40, 90),
    )
    assert result == (0, 1, 0, -1, 0, 0, 0, 0, 1, 35, 30, 5)


def test_materializer_clones_single_prusa_build_item_and_preserves_sla_metadata():
    source = _project()
    placements = (
        DisplayInstancePlacement(4, 30, 40, 0),
        DisplayInstancePlacement(5, 60, 40, 0),
    )
    result = materialize_prusa_project_instances(
        source,
        source_project_sha256=hashlib.sha256(source).hexdigest(),
        source_display_envelope=Envelope2D(100, 120, 200, 210),
        placements=placements,
    )
    assert result.instance_count == 2
    assert result.instance_indices == (4, 5)
    assert result.sha256 == hashlib.sha256(result.bytes).hexdigest()
    assert _support_metadata(result.bytes) == b"support-metadata-must-not-change"
    transforms = _transforms(result.bytes)
    assert transforms == [
        (1, 0, 0, 0, 1, 0, 0, 0, 1, 20, 35, 5),
        (1, 0, 0, 0, 1, 0, 0, 0, 1, 50, 35, 5),
    ]
    assert tuple(transforms) == result.display_transforms


def test_materialization_is_deterministic_for_same_exact_project_and_placements():
    source = _project()
    kwargs = dict(
        source_project_sha256=hashlib.sha256(source).hexdigest(),
        source_display_envelope=Envelope2D(100, 120, 200, 210),
        placements=(
            DisplayInstancePlacement(1, 30, 40, 90),
            DisplayInstancePlacement(2, 60, 40, 90),
        ),
    )
    first = materialize_prusa_project_instances(source, **kwargs)
    second = materialize_prusa_project_instances(source, **kwargs)
    assert first.bytes == second.bytes
    assert first.sha256 == second.sha256


def test_materializer_fails_closed_on_non_single_source_item_or_wrong_source_hash():
    source = _project(build_items=2)
    with pytest.raises(ThreeMFMaterializationError, match="exactly one source build item"):
        materialize_prusa_project_instances(
            source,
            source_project_sha256=hashlib.sha256(source).hexdigest(),
            source_display_envelope=Envelope2D(100, 120, 200, 210),
            placements=(DisplayInstancePlacement(1, 30, 40),),
        )

    source = _project()
    with pytest.raises(ThreeMFMaterializationError, match="do not match"):
        materialize_prusa_project_instances(
            source,
            source_project_sha256="0" * 64,
            source_display_envelope=Envelope2D(100, 120, 200, 210),
            placements=(DisplayInstancePlacement(1, 30, 40),),
        )


def test_materializer_requires_unique_ascending_instance_indices():
    source = _project()
    with pytest.raises(ThreeMFMaterializationError, match="unique ascending"):
        materialize_prusa_project_instances(
            source,
            source_project_sha256=hashlib.sha256(source).hexdigest(),
            source_display_envelope=Envelope2D(100, 120, 200, 210),
            placements=(
                DisplayInstancePlacement(2, 30, 40),
                DisplayInstancePlacement(1, 60, 40),
            ),
        )

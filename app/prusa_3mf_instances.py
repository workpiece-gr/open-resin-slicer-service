from __future__ import annotations

import copy
import hashlib
import math
import re
import shutil
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .placement import Envelope2D


MODEL_MEMBER = "3D/3dmodel.model"
MAX_3MF_MEMBERS = 512
MAX_3MF_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
MAX_MODEL_XML_BYTES = 384 * 1024 * 1024
MAX_BUILD_TAIL_BYTES = 1024 * 1024
MAX_MATERIALIZED_INSTANCES = 1000
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ITEM_RE = re.compile(rb"<item\b[^<>]*/>")
_TRANSFORM_RE = re.compile(rb'\btransform="([^"]*)"')
_OBJECT_ID_RE = re.compile(rb'\bobjectid="([0-9]+)"')


class ThreeMFMaterializationError(ValueError):
    pass


@dataclass(frozen=True)
class DisplayInstancePlacement:
    instance_index: int
    target_display_x_mm: float
    target_display_y_mm: float
    rotation_z_deg: int = 0

    def __post_init__(self) -> None:
        if isinstance(self.instance_index, bool) or not isinstance(self.instance_index, int) or self.instance_index < 1:
            raise ThreeMFMaterializationError("instance_index must be a positive integer.")
        values = (self.target_display_x_mm, self.target_display_y_mm)
        if any(isinstance(value, bool) for value in values):
            raise ThreeMFMaterializationError("Display target coordinates must be finite numbers.")
        try:
            x, y = (float(value) for value in values)
        except (TypeError, ValueError) as exc:
            raise ThreeMFMaterializationError("Display target coordinates must be finite numbers.") from exc
        if not math.isfinite(x) or not math.isfinite(y):
            raise ThreeMFMaterializationError("Display target coordinates must be finite numbers.")
        if self.rotation_z_deg not in {-90, 0, 90}:
            raise ThreeMFMaterializationError("rotation_z_deg must be -90, 0, or 90 degrees.")
        object.__setattr__(self, "target_display_x_mm", x)
        object.__setattr__(self, "target_display_y_mm", y)


@dataclass(frozen=True)
class Materialized3MFProject:
    bytes: bytes
    sha256: str
    instance_count: int
    instance_indices: tuple[int, ...]
    display_transforms: tuple[tuple[float, ...], ...]


def _parse_transform(raw: bytes) -> tuple[float, ...]:
    try:
        values = tuple(float(item) for item in raw.decode("ascii").split())
    except (UnicodeDecodeError, ValueError) as exc:
        raise ThreeMFMaterializationError("Prusa 3MF build-item transform is not numeric.") from exc
    if len(values) != 12 or not all(math.isfinite(value) for value in values):
        raise ThreeMFMaterializationError("Prusa 3MF build-item transform must contain exactly 12 finite numbers.")
    return values


def _to_matrix(values: Sequence[float]) -> list[list[float]]:
    matrix = [[0.0] * 4 for _ in range(4)]
    matrix[3][3] = 1.0
    index = 0
    for column in range(4):
        for row in range(3):
            matrix[row][column] = float(values[index])
            index += 1
    return matrix


def _from_matrix(matrix: Sequence[Sequence[float]]) -> tuple[float, ...]:
    values: list[float] = []
    for column in range(4):
        for row in range(3):
            value = float(matrix[row][column])
            if abs(value) < 5e-13:
                value = 0.0
            values.append(value)
    return tuple(values)


def _multiply(a: Sequence[Sequence[float]], b: Sequence[Sequence[float]]) -> list[list[float]]:
    return [
        [sum(float(a[row][k]) * float(b[k][column]) for k in range(4)) for column in range(4)]
        for row in range(4)
    ]


def _translation(x: float, y: float, z: float = 0.0) -> list[list[float]]:
    return [
        [1.0, 0.0, 0.0, x],
        [0.0, 1.0, 0.0, y],
        [0.0, 0.0, 1.0, z],
        [0.0, 0.0, 0.0, 1.0],
    ]


def _rotation_z(degrees: int) -> list[list[float]]:
    if degrees == 0:
        c, s = 1.0, 0.0
    elif degrees == 90:
        c, s = 0.0, 1.0
    elif degrees == -90:
        c, s = 0.0, -1.0
    else:
        raise ThreeMFMaterializationError("Unsupported display-space plate rotation.")
    return [
        [c, -s, 0.0, 0.0],
        [s, c, 0.0, 0.0],
        [0.0, 0.0, 1.0, 0.0],
        [0.0, 0.0, 0.0, 1.0],
    ]


def compose_display_instance_transform(
    source_transform: Sequence[float],
    *,
    source_display_envelope: Envelope2D,
    placement: DisplayInstancePlacement,
) -> tuple[float, ...]:
    """Rotate exact world geometry around its envelope centre, then move that centre."""
    if len(source_transform) != 12:
        raise ThreeMFMaterializationError("source_transform must contain exactly 12 values.")
    source = _to_matrix(source_transform)
    world_operation = _multiply(
        _translation(placement.target_display_x_mm, placement.target_display_y_mm),
        _multiply(
            _rotation_z(placement.rotation_z_deg),
            _translation(-source_display_envelope.center_x_mm, -source_display_envelope.center_y_mm),
        ),
    )
    return _from_matrix(_multiply(world_operation, source))


def _format_transform(values: Sequence[float]) -> bytes:
    return " ".join("0" if abs(value) < 5e-13 else format(float(value), ".9g") for value in values).encode("ascii")


def _copy_exact_bytes(source, target, count: int) -> None:
    remaining = count
    while remaining:
        chunk = source.read(min(1024 * 1024, remaining))
        if not chunk:
            raise ThreeMFMaterializationError("Prusa 3MF model XML ended unexpectedly during rewrite.")
        target.write(chunk)
        remaining -= len(chunk)


def _rewrite_model_xml(
    source_path: Path,
    output_path: Path,
    *,
    source_display_envelope: Envelope2D,
    placements: Sequence[DisplayInstancePlacement],
) -> tuple[tuple[float, ...], ...]:
    size = source_path.stat().st_size
    if size <= 0 or size > MAX_MODEL_XML_BYTES:
        raise ThreeMFMaterializationError("Prusa 3MF model XML size is outside the supported materialization bound.")
    tail_offset = max(0, size - MAX_BUILD_TAIL_BYTES)
    with source_path.open("rb") as source:
        source.seek(tail_offset)
        tail = source.read()
    if tail.count(b"<build") != 1 or tail.count(b"</build>") != 1:
        raise ThreeMFMaterializationError("Expected exactly one pinned-Prusa build section near the end of 3MF model XML.")
    relative_start = tail.index(b"<build")
    relative_end = tail.index(b"</build>", relative_start) + len(b"</build>")
    build = tail[relative_start:relative_end]
    item_matches = list(_ITEM_RE.finditer(build))
    if len(item_matches) != 1:
        raise ThreeMFMaterializationError("Selected review 3MF must contain exactly one source build item before plate cloning.")
    item_match = item_matches[0]
    item = item_match.group(0)
    transform_matches = list(_TRANSFORM_RE.finditer(item))
    object_matches = list(_OBJECT_ID_RE.finditer(item))
    if len(transform_matches) != 1 or len(object_matches) != 1 or int(object_matches[0].group(1)) < 1:
        raise ThreeMFMaterializationError("Selected review 3MF build item is missing canonical objectid/transform attributes.")
    source_transform = _parse_transform(transform_matches[0].group(1))

    prefix = build[:item_match.start()]
    suffix = build[item_match.end():]
    line_start = prefix.rfind(b"\n") + 1
    indent = prefix[line_start:]
    if indent.strip():
        raise ThreeMFMaterializationError("Pinned-Prusa build-item indentation is not canonical.")
    transform_value_start, transform_value_end = transform_matches[0].span(1)
    item_before_transform = item[:transform_value_start]
    item_after_transform = item[transform_value_end:]

    transforms = tuple(
        compose_display_instance_transform(
            source_transform,
            source_display_envelope=source_display_envelope,
            placement=placement,
        )
        for placement in placements
    )
    rendered_items = [
        item_before_transform + _format_transform(transform) + item_after_transform
        for transform in transforms
    ]
    replacement = prefix + (b"\n" + indent).join(rendered_items) + suffix

    absolute_start = tail_offset + relative_start
    absolute_end = tail_offset + relative_end
    with source_path.open("rb") as source, output_path.open("wb") as output:
        _copy_exact_bytes(source, output, absolute_start)
        output.write(replacement)
        source.seek(absolute_end)
        shutil.copyfileobj(source, output, length=1024 * 1024)
    return transforms


def _validate_placements(placements: Sequence[DisplayInstancePlacement]) -> tuple[DisplayInstancePlacement, ...]:
    values = tuple(placements)
    if not values or len(values) > MAX_MATERIALIZED_INSTANCES:
        raise ThreeMFMaterializationError(
            f"Plate materialization requires between 1 and {MAX_MATERIALIZED_INSTANCES} instances."
        )
    indices = [item.instance_index for item in values]
    if indices != sorted(indices) or len(set(indices)) != len(indices):
        raise ThreeMFMaterializationError("Display placements must have unique ascending instance indices.")
    return values


def materialize_prusa_project_instances(
    project_bytes: bytes,
    *,
    source_project_sha256: str,
    source_display_envelope: Envelope2D,
    placements: Sequence[DisplayInstancePlacement],
) -> Materialized3MFProject:
    """Clone the exact selected Prusa build item at deterministic display-space transforms.

    Only the `3D/3dmodel.model` build section is changed. Object geometry, Prusa model
    metadata, SLA support points, drain holes, and resolved recipe data are copied
    unchanged. Pinned Prusa 2.9.6 loads each build item as a `ModelInstance`; SLA support
    points remain object-level, so all clones share the selected support definition.
    """
    if not isinstance(project_bytes, bytes) or not project_bytes:
        raise ThreeMFMaterializationError("project_bytes must contain the exact selected review 3MF.")
    if not _SHA256_RE.fullmatch(str(source_project_sha256)):
        raise ThreeMFMaterializationError("source_project_sha256 must be a lowercase SHA-256 digest.")
    if hashlib.sha256(project_bytes).hexdigest() != source_project_sha256:
        raise ThreeMFMaterializationError("Selected review 3MF bytes do not match source_project_sha256.")
    if not isinstance(source_display_envelope, Envelope2D):
        raise ThreeMFMaterializationError("source_display_envelope must be exact CTB-bound Envelope2D evidence.")
    placement_values = _validate_placements(placements)

    with tempfile.TemporaryDirectory(prefix="workpiece-resin-3mf-") as temp:
        root = Path(temp)
        source_zip_path = root / "source.3mf"
        output_zip_path = root / "materialized.3mf"
        source_model_path = root / "source.model"
        output_model_path = root / "materialized.model"
        source_zip_path.write_bytes(project_bytes)
        try:
            with zipfile.ZipFile(source_zip_path, "r") as source_zip:
                infos = source_zip.infolist()
                names = [info.filename for info in infos]
                if len(infos) > MAX_3MF_MEMBERS or len(names) != len(set(names)):
                    raise ThreeMFMaterializationError("Selected review 3MF has too many or duplicate ZIP members.")
                if sum(info.file_size for info in infos) > MAX_3MF_UNCOMPRESSED_BYTES:
                    raise ThreeMFMaterializationError("Selected review 3MF exceeds the bounded uncompressed project size.")
                model_infos = [info for info in infos if info.filename == MODEL_MEMBER]
                if len(model_infos) != 1:
                    raise ThreeMFMaterializationError(f"Selected review 3MF must contain exactly one {MODEL_MEMBER} member.")
                model_info = model_infos[0]
                if model_info.flag_bits & 0x1:
                    raise ThreeMFMaterializationError("Encrypted 3MF members are not supported.")
                with source_zip.open(model_info, "r") as source_model, source_model_path.open("wb") as target:
                    shutil.copyfileobj(source_model, target, length=1024 * 1024)
                transforms = _rewrite_model_xml(
                    source_model_path,
                    output_model_path,
                    source_display_envelope=source_display_envelope,
                    placements=placement_values,
                )

                with zipfile.ZipFile(output_zip_path, "w", allowZip64=True) as output_zip:
                    for info in infos:
                        if info.flag_bits & 0x1:
                            raise ThreeMFMaterializationError("Encrypted 3MF members are not supported.")
                        target_info = copy.copy(info)
                        with output_zip.open(target_info, "w") as target:
                            if info.filename == MODEL_MEMBER:
                                with output_model_path.open("rb") as source:
                                    shutil.copyfileobj(source, target, length=1024 * 1024)
                            else:
                                with source_zip.open(info, "r") as source:
                                    shutil.copyfileobj(source, target, length=1024 * 1024)
        except zipfile.BadZipFile as exc:
            raise ThreeMFMaterializationError("Selected review project is not a valid 3MF ZIP archive.") from exc

        output_bytes = output_zip_path.read_bytes()
    return Materialized3MFProject(
        bytes=output_bytes,
        sha256=hashlib.sha256(output_bytes).hexdigest(),
        instance_count=len(placement_values),
        instance_indices=tuple(item.instance_index for item in placement_values),
        display_transforms=transforms,
    )

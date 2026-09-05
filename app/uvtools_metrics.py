from __future__ import annotations

import math
import re
from dataclasses import dataclass


MAX_NATIVE_METRIC_LAYERS = 10_000
_NUMBER = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?"
_RECTANGLE_RE = re.compile(
    rf"Width\s*=\s*(?P<width>{_NUMBER}).*?Height\s*=\s*(?P<height>{_NUMBER})"
)
_LAYER_HEADER_RE = re.compile(r"^#\s*Layer:\s*(\d+)\s*$")


class UVtoolsMetricError(ValueError):
    pass


@dataclass(frozen=True)
class NativeArtifactMetrics:
    layer_count: int
    max_layer_area_mm2: float
    material_volume_mm3: float
    footprint_area_mm2: float
    z_height_mm: float


def _finite_nonnegative(name: str, raw: str) -> float:
    try:
        value = float(raw.strip())
    except (TypeError, ValueError) as exc:
        raise UVtoolsMetricError(f"{name} must be a finite non-negative number.") from exc
    if not math.isfinite(value) or value < 0:
        raise UVtoolsMetricError(f"{name} must be a finite non-negative number.")
    return value


def _properties_before_layers(output: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if _LAYER_HEADER_RE.match(line):
            break
        if not line or line.startswith("-") or line.startswith("Opening file") or line.startswith("Total properties"):
            continue
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.strip()
        if name in result:
            raise UVtoolsMetricError(f"Duplicate UVtools property: {name}.")
        result[name] = value.strip()
    return result


def parse_base_native_properties(output: str) -> tuple[int, float, float]:
    """Parse layer count, Z height and whole-print footprint area from UVtools output."""
    properties = _properties_before_layers(output)
    missing = [name for name in ("LayerCount", "PrintHeight", "BoundingRectangleMillimeters") if name not in properties]
    if missing:
        raise UVtoolsMetricError(
            "UVtools base properties are incomplete; missing: " + ", ".join(missing) + "."
        )

    raw_count = properties["LayerCount"].strip()
    if not raw_count.isdigit():
        raise UVtoolsMetricError("LayerCount must be a positive integer.")
    layer_count = int(raw_count)
    if layer_count < 1 or layer_count > MAX_NATIVE_METRIC_LAYERS:
        raise UVtoolsMetricError(
            f"LayerCount must be between 1 and {MAX_NATIVE_METRIC_LAYERS}."
        )

    z_height = _finite_nonnegative("PrintHeight", properties["PrintHeight"])
    if z_height <= 0:
        raise UVtoolsMetricError("PrintHeight must be positive for orientation validation.")

    match = _RECTANGLE_RE.search(properties["BoundingRectangleMillimeters"])
    if not match:
        raise UVtoolsMetricError("BoundingRectangleMillimeters does not contain Width and Height.")
    width = _finite_nonnegative("BoundingRectangleMillimeters.Width", match.group("width"))
    depth = _finite_nonnegative("BoundingRectangleMillimeters.Height", match.group("height"))
    if width <= 0 or depth <= 0:
        raise UVtoolsMetricError("Native footprint width and height must be positive.")
    return layer_count, z_height, width * depth


def parse_layer_native_properties(output: str, *, expected_layer_count: int) -> tuple[float, float]:
    """Parse max layer area and sum of cured layer volumes from UVtools layer output."""
    if isinstance(expected_layer_count, bool) or not isinstance(expected_layer_count, int):
        raise UVtoolsMetricError("expected_layer_count must be an integer.")
    if expected_layer_count < 1 or expected_layer_count > MAX_NATIVE_METRIC_LAYERS:
        raise UVtoolsMetricError(
            f"expected_layer_count must be between 1 and {MAX_NATIVE_METRIC_LAYERS}."
        )

    layers: dict[int, dict[str, str]] = {}
    current: int | None = None
    for raw_line in output.splitlines():
        line = raw_line.strip()
        header = _LAYER_HEADER_RE.match(line)
        if header:
            current = int(header.group(1))
            if current in layers:
                raise UVtoolsMetricError(f"Duplicate UVtools layer section: {current}.")
            layers[current] = {}
            continue
        if current is None or not line or line.startswith("-") or line.startswith("Total properties"):
            continue
        if ":" not in line:
            continue
        name, value = line.split(":", 1)
        name = name.strip()
        if name not in {"Area", "Volume"}:
            continue
        if name in layers[current]:
            raise UVtoolsMetricError(f"Duplicate {name} for UVtools layer {current}.")
        layers[current][name] = value.strip()

    expected = set(range(expected_layer_count))
    actual = set(layers)
    if actual != expected:
        raise UVtoolsMetricError(
            "UVtools layer properties must cover every layer exactly once; "
            f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}."
        )

    max_area = 0.0
    material_volume = 0.0
    for index in range(expected_layer_count):
        properties = layers[index]
        missing = [name for name in ("Area", "Volume") if name not in properties]
        if missing:
            raise UVtoolsMetricError(
                f"UVtools layer {index} is missing: {', '.join(missing)}."
            )
        area = _finite_nonnegative(f"layer {index} Area", properties["Area"])
        volume = _finite_nonnegative(f"layer {index} Volume", properties["Volume"])
        max_area = max(max_area, area)
        material_volume += volume

    if max_area <= 0 or material_volume <= 0:
        raise UVtoolsMetricError("Native artifact must contain positive cured area and volume.")
    return max_area, material_volume


def parse_native_artifact_metrics(base_output: str, layer_output: str) -> NativeArtifactMetrics:
    layer_count, z_height, footprint_area = parse_base_native_properties(base_output)
    max_area, material_volume = parse_layer_native_properties(
        layer_output, expected_layer_count=layer_count
    )
    return NativeArtifactMetrics(
        layer_count=layer_count,
        max_layer_area_mm2=round(max_area, 6),
        material_volume_mm3=round(material_volume, 6),
        footprint_area_mm2=round(footprint_area, 6),
        z_height_mm=round(z_height, 6),
    )


def base_property_command(uvtools_cmd: str, native_path: str) -> tuple[str, ...]:
    return (
        uvtools_cmd,
        "print-properties",
        native_path,
        "-n",
        "LayerCount",
        "PrintHeight",
        "BoundingRectangleMillimeters",
        "--no-progress",
    )


def layer_property_command(uvtools_cmd: str, native_path: str, *, layer_count: int) -> tuple[str, ...]:
    if isinstance(layer_count, bool) or not isinstance(layer_count, int) or not (1 <= layer_count <= MAX_NATIVE_METRIC_LAYERS):
        raise UVtoolsMetricError(
            f"layer_count must be between 1 and {MAX_NATIVE_METRIC_LAYERS}."
        )
    return (
        uvtools_cmd,
        "print-properties",
        native_path,
        "-r",
        f"0:{layer_count - 1}",
        "-n",
        "Area",
        "Volume",
        "--no-progress",
    )

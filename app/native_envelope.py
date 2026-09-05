from __future__ import annotations

import re
from dataclasses import dataclass

from .placement import Envelope2D
from .uvtools_metrics import NativeBoundingRectangle


NATIVE_ENVELOPE_SCHEMA = "workpiece-resin-native-envelope-v1"
NATIVE_DISPLAY_COORDINATE_SPACE = "uvtools-native-display-millimetres"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class NativeEnvelopeError(ValueError):
    pass


@dataclass(frozen=True)
class NativeEnvelopeEvidence:
    """Exact CTB-bound whole-print XY envelope in UVtools native display coordinates."""

    printer_profile_id: str
    printer_native_sha256: str
    envelope: Envelope2D

    def validate(self) -> "NativeEnvelopeEvidence":
        if not str(self.printer_profile_id).strip():
            raise NativeEnvelopeError("printer_profile_id is required.")
        if not _SHA256_RE.fullmatch(str(self.printer_native_sha256)):
            raise NativeEnvelopeError(
                "printer_native_sha256 must be a lowercase 64-character SHA-256 digest."
            )
        if not isinstance(self.envelope, Envelope2D):
            raise NativeEnvelopeError("envelope must be an Envelope2D.")
        return self

    @property
    def coordinate_space(self) -> str:
        return NATIVE_DISPLAY_COORDINATE_SPACE

    @property
    def automatic_materialization_authority(self) -> bool:
        return False


def native_envelope_from_rectangle(
    *,
    printer_profile_id: str,
    printer_native_sha256: str,
    rectangle: NativeBoundingRectangle,
) -> NativeEnvelopeEvidence:
    if not isinstance(rectangle, NativeBoundingRectangle):
        raise NativeEnvelopeError("rectangle must be NativeBoundingRectangle evidence from pinned UVtools.")
    evidence = NativeEnvelopeEvidence(
        printer_profile_id=str(printer_profile_id).strip(),
        printer_native_sha256=str(printer_native_sha256),
        envelope=Envelope2D(
            min_x_mm=rectangle.x_mm,
            max_x_mm=rectangle.max_x_mm,
            min_y_mm=rectangle.y_mm,
            max_y_mm=rectangle.max_y_mm,
        ),
    )
    return evidence.validate()


def native_envelope_manifest(evidence: NativeEnvelopeEvidence) -> dict:
    evidence.validate()
    envelope = evidence.envelope
    return {
        "schema": NATIVE_ENVELOPE_SCHEMA,
        "printer_profile_id": evidence.printer_profile_id,
        "printer_native_sha256": evidence.printer_native_sha256,
        "source": "exact-retained-printer-native-artifact",
        "coordinate_space": evidence.coordinate_space,
        "automatic_materialization_authority": False,
        "manufacturing_envelope_mapping_applied": False,
        "envelope_mm": {
            "min_x": envelope.min_x_mm,
            "max_x": envelope.max_x_mm,
            "min_y": envelope.min_y_mm,
            "max_y": envelope.max_y_mm,
            "width": envelope.width_mm,
            "depth": envelope.depth_mm,
        },
        "review_rule": (
            "This envelope is derived from the exact retained printer-native artifact and is expressed only in UVtools native display millimetres. "
            "It does not identify where the conservative manufacturing envelope lies inside the display and therefore cannot authorize automatic physical placement until that mapping is separately validated and applied."
        ),
    }

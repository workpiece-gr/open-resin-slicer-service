import pytest

from app.native_envelope import (
    NativeEnvelopeError,
    native_envelope_from_rectangle,
    native_envelope_manifest,
)
from app.uvtools_metrics import NativeBoundingRectangle


NATIVE_SHA = "a" * 64


def test_native_envelope_is_exact_ctb_bound_and_non_authoritative_for_materialization():
    evidence = native_envelope_from_rectangle(
        printer_profile_id="elegoo-mars-2",
        printer_native_sha256=NATIVE_SHA,
        rectangle=NativeBoundingRectangle(
            x_mm=1.25,
            y_mm=2.5,
            width_mm=20.5,
            height_mm=30.25,
        ),
    )
    assert evidence.printer_native_sha256 == NATIVE_SHA
    assert evidence.envelope.min_x_mm == 1.25
    assert evidence.envelope.max_x_mm == 21.75
    assert evidence.envelope.min_y_mm == 2.5
    assert evidence.envelope.max_y_mm == 32.75
    assert evidence.coordinate_space == "uvtools-native-display-millimetres"
    assert evidence.automatic_materialization_authority is False

    manifest = native_envelope_manifest(evidence)
    assert manifest["schema"] == "workpiece-resin-native-envelope-v1"
    assert manifest["printer_native_sha256"] == NATIVE_SHA
    assert manifest["coordinate_space"] == "uvtools-native-display-millimetres"
    assert manifest["automatic_materialization_authority"] is False
    assert manifest["manufacturing_envelope_mapping_applied"] is False
    assert manifest["envelope_mm"]["width"] == 20.5
    assert "cannot authorize automatic physical placement" in manifest["review_rule"]


def test_native_envelope_rejects_noncanonical_hash_and_non_uvtools_rectangle():
    with pytest.raises(NativeEnvelopeError, match="lowercase"):
        native_envelope_from_rectangle(
            printer_profile_id="elegoo-mars-2",
            printer_native_sha256="A" * 64,
            rectangle=NativeBoundingRectangle(0, 0, 1, 1),
        )
    with pytest.raises(NativeEnvelopeError, match="NativeBoundingRectangle"):
        native_envelope_from_rectangle(
            printer_profile_id="elegoo-mars-2",
            printer_native_sha256=NATIVE_SHA,
            rectangle=(0, 0, 1, 1),
        )

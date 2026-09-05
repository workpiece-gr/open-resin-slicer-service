import hashlib

import pytest

from app.materialization import (
    MaterializationError,
    MaterializedEnvelopeObservation,
    finalize_materialized_plate,
    materialized_plate_manifest,
    prepare_printer_plate_materialization,
)
from app.placement import Envelope2D
from app.plate import PrinterPlatePlan, plan_rectangular_instances


def _profile_plan(mapping: str = "validated") -> PrinterPlatePlan:
    return PrinterPlatePlan(
        printer_profile_id="elegoo-mars-2",
        manufacturing_envelope_coordinate_mapping=mapping,
        plan=plan_rectangular_instances(
            footprint_width_mm=30,
            footprint_depth_mm=30,
            quantity=2,
            plate_width_mm=80,
            plate_depth_mm=129,
            spacing_mm=5,
            edge_margin_mm=5,
            allow_rotate_90=True,
        ),
    )


def _pretranslation_envelope() -> Envelope2D:
    return Envelope2D(min_x_mm=100, max_x_mm=130, min_y_mm=-20, max_y_mm=10)


def test_unverified_coordinate_mapping_blocks_automatic_materialization():
    with pytest.raises(MaterializationError, match="coordinate mapping is not validated"):
        prepare_printer_plate_materialization(
            _profile_plan("unverified"),
            plate_index=1,
            pretranslation_envelope=_pretranslation_envelope(),
        )


def test_unverified_mapping_can_prepare_non_authoritative_candidate():
    spec = prepare_printer_plate_materialization(
        _profile_plan("unverified"),
        plate_index=1,
        pretranslation_envelope=_pretranslation_envelope(),
        require_validated_mapping=False,
    )
    assert spec.automatic_materialization_authority is False
    assert [item.translate_x_mm for item in spec.translations] == [-95.0, -60.0]
    assert [item.translate_y_mm for item in spec.translations] == [25.0, 25.0]


def test_finalize_binds_reextracted_envelopes_to_exact_project():
    spec = prepare_printer_plate_materialization(
        _profile_plan(),
        plate_index=1,
        pretranslation_envelope=_pretranslation_envelope(),
    )
    project = b"exact materialized plate 3mf"
    digest = hashlib.sha256(project).hexdigest()
    evidence = finalize_materialized_plate(
        spec,
        project_bytes=project,
        observations=(
            MaterializedEnvelopeObservation(
                instance_index=1,
                envelope=Envelope2D(5, 35, 5, 35),
                project_sha256=digest,
            ),
            MaterializedEnvelopeObservation(
                instance_index=2,
                envelope=Envelope2D(40, 70, 5, 35),
                project_sha256=digest,
            ),
        ),
    )
    assert evidence.project_sha256 == digest
    assert evidence.automatic_materialization_authority is True
    manifest = materialized_plate_manifest(evidence)
    assert manifest["schema"] == "workpiece-resin-materialized-plate-v1"
    assert manifest["project_sha256"] == digest
    assert manifest["automatic_materialization_authority"] is True
    assert len(manifest["translations"]) == 2
    assert len(manifest["materialized_envelopes"]) == 2


def test_finalize_rejects_observation_from_different_project():
    spec = prepare_printer_plate_materialization(
        _profile_plan(),
        plate_index=1,
        pretranslation_envelope=_pretranslation_envelope(),
    )
    project = b"exact materialized plate 3mf"
    wrong_digest = hashlib.sha256(b"different project").hexdigest()
    with pytest.raises(MaterializationError, match="not bound to the exact materialized project"):
        finalize_materialized_plate(
            spec,
            project_bytes=project,
            observations=(
                MaterializedEnvelopeObservation(
                    instance_index=1,
                    envelope=Envelope2D(5, 35, 5, 35),
                    project_sha256=wrong_digest,
                ),
                MaterializedEnvelopeObservation(
                    instance_index=2,
                    envelope=Envelope2D(40, 70, 5, 35),
                    project_sha256=wrong_digest,
                ),
            ),
        )


def test_finalize_rejects_support_or_pad_drift_outside_planned_slot():
    spec = prepare_printer_plate_materialization(
        _profile_plan(),
        plate_index=1,
        pretranslation_envelope=_pretranslation_envelope(),
    )
    project = b"exact materialized plate 3mf"
    digest = hashlib.sha256(project).hexdigest()
    with pytest.raises(MaterializationError, match="replan required"):
        finalize_materialized_plate(
            spec,
            project_bytes=project,
            observations=(
                MaterializedEnvelopeObservation(
                    instance_index=1,
                    envelope=Envelope2D(5, 35, 5, 35),
                    project_sha256=digest,
                ),
                MaterializedEnvelopeObservation(
                    instance_index=2,
                    envelope=Envelope2D(39.5, 70.5, 5, 35),
                    project_sha256=digest,
                ),
            ),
        )


def test_observation_source_must_be_exact_materialized_project():
    digest = "a" * 64
    with pytest.raises(MaterializationError, match="re-extracted"):
        MaterializedEnvelopeObservation(
            instance_index=1,
            envelope=Envelope2D(5, 35, 5, 35),
            project_sha256=digest,
            source="planned-envelope",
        )

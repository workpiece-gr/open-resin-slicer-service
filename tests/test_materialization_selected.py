from __future__ import annotations

import hashlib

from app.materialization import MaterializedEnvelopeObservation
from app.materialization_selected import (
    finalize_selected_materialized_plate,
    prepare_selected_plate_materialization,
    selected_materialized_plate_manifest,
)
from app.orientation_plate import SelectedOrientationPlatePlan
from app.placement import Envelope2D
from app.plate import PrinterPlatePlan, plan_rectangular_instances


SOURCE_SHA = "d" * 64
SELECTED_PROJECT_SHA = "a" * 64
SELECTED_CONFIG_SHA = "9" * 64
SELECTED_INTERMEDIATE_SHA = "b" * 64
SELECTED_NATIVE_SHA = "c" * 64


def _selected_plan() -> SelectedOrientationPlatePlan:
    printer_plan = PrinterPlatePlan(
        printer_profile_id="elegoo-mars-2",
        manufacturing_envelope_coordinate_mapping="unverified",
        plan=plan_rectangular_instances(
            footprint_width_mm=30,
            footprint_depth_mm=30,
            quantity=2,
            plate_width_mm=80,
            plate_depth_mm=129,
            spacing_mm=5,
            edge_margin_mm=3,
        ),
    )
    return SelectedOrientationPlatePlan(
        orientation_deg=(15.0, 0.0, 0.0),
        source_sha256=SOURCE_SHA,
        review_project_sha256=SELECTED_PROJECT_SHA,
        effective_config_sha256=SELECTED_CONFIG_SHA,
        intermediate_sha256=SELECTED_INTERMEDIATE_SHA,
        native_sha256=SELECTED_NATIVE_SHA,
        pretranslation_envelope=Envelope2D(100, 130, -20, 10),
        printer_plate_plan=printer_plan,
    )


def test_selected_materialization_binds_upstream_winner_to_exact_plate_output():
    selected = _selected_plan()
    spec = prepare_selected_plate_materialization(
        selected,
        plate_index=1,
        require_validated_mapping=False,
    )
    project = b"exact materialized plate project"
    project_sha = hashlib.sha256(project).hexdigest()
    plate = selected.printer_plate_plan.plan.plates[0]
    observations = tuple(
        MaterializedEnvelopeObservation(
            instance_index=placement.instance_index,
            envelope=Envelope2D(
                placement.x_mm - 15,
                placement.x_mm + 15,
                placement.y_mm - 15,
                placement.y_mm + 15,
            ),
            project_sha256=project_sha,
        )
        for placement in plate.placements
    )

    evidence = finalize_selected_materialized_plate(
        spec,
        project_bytes=project,
        observations=observations,
    )
    manifest = selected_materialized_plate_manifest(evidence)

    assert evidence.source_sha256 == SOURCE_SHA
    assert evidence.selected_orientation_deg == (15.0, 0.0, 0.0)
    assert evidence.selected_review_3mf_sha256 == SELECTED_PROJECT_SHA
    assert evidence.selected_effective_config_sha256 == SELECTED_CONFIG_SHA
    assert evidence.selected_intermediate_sl1_sha256 == SELECTED_INTERMEDIATE_SHA
    assert evidence.selected_printer_native_sha256 == SELECTED_NATIVE_SHA
    assert evidence.project_sha256 == project_sha
    assert manifest["source_sha256"] == SOURCE_SHA
    assert manifest["selected_sliced_artifacts"] == {
        "review_3mf_sha256": SELECTED_PROJECT_SHA,
        "effective_config_sha256": SELECTED_CONFIG_SHA,
        "intermediate_sl1_sha256": SELECTED_INTERMEDIATE_SHA,
        "printer_native_sha256": SELECTED_NATIVE_SHA,
    }
    assert manifest["materialized_plate"]["project_sha256"] == project_sha
    assert manifest["materialized_plate"]["automatic_materialization_authority"] is False


def test_selected_materialization_uses_selected_supported_envelope_for_translations():
    selected = _selected_plan()
    spec = prepare_selected_plate_materialization(
        selected,
        plate_index=1,
        require_validated_mapping=False,
    )
    first = spec.plate_spec.translations[0]
    first_placement = selected.printer_plate_plan.plan.plates[0].placements[0]
    assert first.translate_x_mm == first_placement.x_mm - 115
    assert first.translate_y_mm == first_placement.y_mm - (-5)

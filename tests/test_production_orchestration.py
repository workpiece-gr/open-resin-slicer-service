import hashlib
from types import SimpleNamespace

import pytest

import app.production_orchestration as orchestration
from app.toolchain import TOOLCHAIN_REF_ENV, ToolchainProvenanceError


VALID_TOOLCHAIN = "ghcr.io/workpiece-gr/resin-slicer-toolchain@sha256:" + "a" * 64
SOURCE = b"exact source stl bytes"
SOURCE_SHA = hashlib.sha256(SOURCE).hexdigest()


class _Registry:
    def __init__(self):
        self.calls = 0
        self.printer = SimpleNamespace(
            id="printer-a",
            metadata={"native_format": "ctb"},
        )
        self.resin = SimpleNamespace(id="resin-a")
        self.quality = SimpleNamespace(id="quality-a")

    def resolve_production(self, printer, resin, quality):
        self.calls += 1
        assert (printer, resin, quality) == ("printer-a", "resin-a", "quality-a")
        return self.printer, self.resin, self.quality


def _call(registry, **overrides):
    kwargs = {
        "source_stl": SOURCE,
        "original_name": "customer/part.stl",
        "requested_quantity": 2,
        "registry": registry,
        "printer_profile_id": "printer-a",
        "resin_profile_id": "resin-a",
        "quality_profile_id": "quality-a",
        "prusa_bin": "/opt/prusa",
        "uvtools_cmd": "/opt/uvtools",
        "slice_timeout": 240,
        "uvtools_timeout": 120,
    }
    kwargs.update(overrides)
    return orchestration.execute_selected_production_order(**kwargs)


def test_production_orchestration_requires_immutable_toolchain_before_profile_resolution(monkeypatch):
    monkeypatch.delenv(TOOLCHAIN_REF_ENV, raising=False)
    registry = _Registry()
    with pytest.raises(ToolchainProvenanceError, match="immutable toolchain image"):
        _call(registry)
    assert registry.calls == 0


def test_manual_review_selection_blocks_before_plate_planning(monkeypatch):
    monkeypatch.setenv(TOOLCHAIN_REF_ENV, VALID_TOOLCHAIN)
    registry = _Registry()
    proxy = SimpleNamespace(screening=SimpleNamespace(finalists=(object(), object())))
    sliced = SimpleNamespace(
        selected_result=None,
        selected_native_envelope=None,
        validation=object(),
        executed_finalist_count=2,
    )
    monkeypatch.setattr(orchestration, "build_proxy_orientation_plan", lambda *a, **k: proxy)
    monkeypatch.setattr(orchestration, "execute_real_sliced_finalists", lambda **k: sliced)
    monkeypatch.setattr(
        orchestration,
        "plan_selected_sliced_orientation",
        lambda **k: (_ for _ in ()).throw(AssertionError("plate planning must not run")),
    )

    with pytest.raises(orchestration.ProductionOrchestrationError, match="manual review"):
        _call(registry)
    assert registry.calls == 1


def test_selected_winner_must_match_exact_requested_source(monkeypatch):
    monkeypatch.setenv(TOOLCHAIN_REF_ENV, VALID_TOOLCHAIN)
    registry = _Registry()
    proxy = SimpleNamespace(screening=SimpleNamespace(finalists=(object(),)))
    selected_artifact = SimpleNamespace(
        source_sha256="f" * 64,
        project_bytes=b"selected project",
        effective_config_bytes=b"selected config",
    )
    sliced = SimpleNamespace(
        selected_result=SimpleNamespace(artifact=selected_artifact),
        selected_native_envelope=object(),
        validation=object(),
        executed_finalist_count=1,
    )
    monkeypatch.setattr(orchestration, "build_proxy_orientation_plan", lambda *a, **k: proxy)
    monkeypatch.setattr(orchestration, "execute_real_sliced_finalists", lambda **k: sliced)
    monkeypatch.setattr(
        orchestration,
        "plan_selected_sliced_orientation",
        lambda **k: (_ for _ in ()).throw(AssertionError("plate planning must not run")),
    )

    with pytest.raises(orchestration.ProductionOrchestrationError, match="exact requested source"):
        _call(registry)


def test_multi_plate_orchestration_sequences_complete_authority_chain(monkeypatch):
    monkeypatch.setenv(TOOLCHAIN_REF_ENV, VALID_TOOLCHAIN)
    registry = _Registry()
    proxy = SimpleNamespace(
        screening=SimpleNamespace(finalists=(object(), object(), object())),
    )
    selected_artifact = SimpleNamespace(
        source_sha256=SOURCE_SHA,
        project_bytes=b"exact selected review project",
        effective_config_bytes=b"exact selected effective config",
    )
    sliced = SimpleNamespace(
        selected_result=SimpleNamespace(artifact=selected_artifact),
        selected_native_envelope=object(),
        validation=object(),
        executed_finalist_count=3,
    )
    selected_plan = SimpleNamespace(
        printer_plate_plan=SimpleNamespace(
            plan=SimpleNamespace(
                plates=(SimpleNamespace(plate_index=1), SimpleNamespace(plate_index=2)),
            )
        )
    )

    monkeypatch.setattr(orchestration, "build_proxy_orientation_plan", lambda *a, **k: proxy)
    monkeypatch.setattr(orchestration, "execute_real_sliced_finalists", lambda **k: sliced)
    monkeypatch.setattr(orchestration, "plan_selected_sliced_orientation", lambda **k: selected_plan)

    calls = []

    def materialize(plan, *, registry, plate_index, selected_review_project_bytes):
        assert plan is selected_plan
        assert selected_review_project_bytes == selected_artifact.project_bytes
        calls.append(("materialize", plate_index))
        return SimpleNamespace(plate_index=plate_index)

    def prove_instances(plan, materialization, *, selected_review_project_bytes):
        assert selected_review_project_bytes == selected_artifact.project_bytes
        calls.append(("instances", materialization.plate_index))
        return SimpleNamespace(
            plate_index=materialization.plate_index,
            materialized_plate=f"selected-materialization-{materialization.plate_index}",
        )

    def execute_native(materialization, **kwargs):
        assert kwargs["selected_effective_config_bytes"] == selected_artifact.effective_config_bytes
        assert kwargs["reject_critical"] is True
        calls.append(("native", materialization.plate_index))
        idx = materialization.plate_index
        return SimpleNamespace(
            plate_index=idx,
            artifact=SimpleNamespace(
                intermediate_sha256=(str(idx) * 64)[:64],
                native_sha256=(str(idx + 2) * 64)[:64],
                issue_summary={"islands": 0},
            ),
        )

    def whole_plate(materialization, execution, *, printer):
        calls.append(("whole", materialization.plate_index))
        return SimpleNamespace(plate_index=materialization.plate_index)

    def authority(instance_evidence, execution, whole_plate, *, printer):
        calls.append(("authority", execution.plate_index))
        return SimpleNamespace(plate_index=execution.plate_index)

    captured = {}

    def build_order(**kwargs):
        captured.update(kwargs)
        assert kwargs["authority"] == "production-authoritative"
        return {
            "schema": "workpiece-resin-order-manifest-v4",
            "authority": kwargs["authority"],
            "plates": [{"plate_index": record.plate_index} for record in kwargs["plate_artifacts"]],
        }

    monkeypatch.setattr(orchestration, "materialize_selected_plate_project", materialize)
    monkeypatch.setattr(
        orchestration,
        "finalize_selected_materialized_plate_from_verified_build_items",
        prove_instances,
    )
    monkeypatch.setattr(orchestration, "execute_selected_materialized_plate_native", execute_native)
    monkeypatch.setattr(orchestration, "validate_whole_plate_native_envelope", whole_plate)
    monkeypatch.setattr(orchestration, "validate_selected_plate_authority", authority)
    monkeypatch.setattr(orchestration, "build_selected_orientation_order_manifest", build_order)

    result = _call(registry)

    assert calls == [
        ("materialize", 1),
        ("instances", 1),
        ("native", 1),
        ("whole", 1),
        ("authority", 1),
        ("materialize", 2),
        ("instances", 2),
        ("native", 2),
        ("whole", 2),
        ("authority", 2),
    ]
    assert result.source_sha256 == SOURCE_SHA
    assert result.toolchain_image_ref == VALID_TOOLCHAIN
    assert [item.plate_index for item in result.plates] == [1, 2]
    assert captured["source_filename"] == "part.stl"
    assert captured["requested_quantity"] == 2
    assert captured["printer_profile"] == "printer-a"
    assert captured["resin_profile"] == "resin-a"
    assert captured["quality_profile"] == "quality-a"
    records = captured["plate_artifacts"]
    assert [record.project_filename for record in records] == [
        "part-plate-001-materialized.3mf",
        "part-plate-002-materialized.3mf",
    ]
    assert [record.native_filename for record in records] == [
        "part-plate-001.ctb",
        "part-plate-002.ctb",
    ]
    assert result.order_manifest["execution_environment"] == {
        "toolchain_image_ref": VALID_TOOLCHAIN,
        "immutable": True,
    }
    assert result.order_manifest["orchestration"] == {
        "schema": "workpiece-resin-selected-production-orchestration-v1",
        "proxy_finalist_count": 3,
        "executed_finalist_count": 3,
        "physical_plate_count": 2,
        "production_enablement_performed": False,
    }

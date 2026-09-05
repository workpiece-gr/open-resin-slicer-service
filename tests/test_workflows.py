from pathlib import Path


def test_toolchain_workflow_runs_on_main_and_keeps_publication_explicit():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/toolchain.yml").read_text("utf-8")
    assert "      - main\n" in workflow
    assert "include-hidden-files: true" in workflow
    assert "github.event_name == 'workflow_dispatch' && inputs.publish == true" in workflow
    assert 'test "$RELEASE_ACK" = "PUBLISH_PINNED_TOOLCHAIN"' in workflow


def test_service_acceptance_requires_published_immutable_lock():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/ci.yml").read_text("utf-8")
    assert "needs.toolchain-pin.outputs.ready == 'true'" in workflow
    assert "docker pull \"$TOOLCHAIN_REF\"" in workflow
    assert "WORKPIECE_RESIN_TOOLCHAIN_REF=$TOOLCHAIN_REF" in workflow


def test_service_acceptance_exercises_retained_recipe_and_uvtools_exit_contract():
    root = Path(__file__).parents[1]
    workflow = (root / ".github/workflows/ci.yml").read_text("utf-8")
    assert "workpiece-resin-bundle-v2" in workflow
    assert "'source_stl', 'review_3mf', 'effective_config', 'intermediate_sl1', 'printer_native'" in workflow
    assert "Path('acceptance-out/effective.ini').write_bytes(payloads['effective_config'])" in workflow
    assert "--load /work/effective.ini" in workflow
    assert 'test "$base_status" -eq 1' in workflow
    assert 'test "$layer_status" -eq 1' in workflow
    assert 'test "$convert_status" -eq 1' in workflow

"""Issue #15: UVtools reports an island on the first model layer above the support tips on every orientation of a part with a flat underside."""
from app.issue_triage import island_layers, is_first_model_layer_island, support_lift_layers, triage_issue_summary

# Real report of the 40 x 30 x 20 mm box standing on its 40 x 20 face (measured on the host 2026-11-13): print 35.15 mm, model 30 mm, layer 0.05 mm.
REAL_TEXT = """Opening file production.ctb: Done in 0.58s
Detecting issues: Done in 1.09s
Issues: 11
Island, 103, 308607px², {X=810,Y=1375,Width=785,Height=396}
Overhang, 103, 186011px², {X=850,Y=1418,Width=704,Height=312}
TouchingBound, 0, 1760px², {X=1615,Y=1384,Width=5,Height=354}
TouchingBound, 1, 1760px², {X=1615,Y=1384,Width=5,Height=354}
"""
SUMMARY = {"islands": 1, "overhangs": 1, "resin_traps": 0, "suction_cups": 0, "touching_bounds": 2, "empty_layers": 0}


def test_lift_in_layers_from_the_measured_heights():
    assert support_lift_layers(35.15, 30.0, 0.05) == 103
    assert support_lift_layers(30.0, 30.0, 0.05) is None, "no supports under the part: nothing to excuse"
    assert support_lift_layers(35.15, 30.0, 0) is None and support_lift_layers("x", 30, 0.05) is None


def test_the_measured_island_on_layer_103_is_information_not_critical():
    triaged = triage_issue_summary(SUMMARY, REAL_TEXT, lift_layers=103)
    assert triaged["islands"] == 0 and triaged["support_tip_islands"] == 1
    assert triaged["touching_bounds"] == 2 and triaged["overhangs"] == 1, "everything else is untouched"
    assert SUMMARY["islands"] == 1, "the input is not modified"


def test_a_real_mid_body_island_stays_critical():
    text = "Issues: 2\nIsland, 103, 308607px², {X=1,Y=1,Width=1,Height=1}\nIsland, 240, 5000px², {X=1,Y=1,Width=1,Height=1}\n"
    triaged = triage_issue_summary({**SUMMARY, "islands": 2, "touching_bounds": 0}, text, lift_layers=103)
    assert triaged["islands"] == 1 and triaged["support_tip_islands"] == 1
    assert island_layers(text) == [103, 240]


def test_the_tolerance_is_one_layer_and_without_a_support_region_nothing_is_excused():
    assert [is_first_model_layer_island(layer, 103) for layer in (101, 102, 103, 104, 105)] == [False, True, True, True, False]
    assert is_first_model_layer_island(103, None) is False
    triaged = triage_issue_summary(SUMMARY, REAL_TEXT, lift_layers=None)
    assert triaged["islands"] == 1 and triaged["support_tip_islands"] == 0


def test_a_summary_that_disagrees_with_the_rows_is_not_guessed_at():
    triaged = triage_issue_summary({**SUMMARY, "islands": 3}, REAL_TEXT, lift_layers=103)
    assert triaged["islands"] == 3 and triaged["support_tip_islands"] == 0

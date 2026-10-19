import itertools
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agents.planner import build_constraints
from core.meal_engine import DIET_LEVEL, filter_foods, generate_meal_plan, parse_foods, parse_templates
from core.nutrition import DEMO_PROFILE, compute_targets
from core.report import meal_plan_to_df

ROOT = Path(__file__).resolve().parent.parent
FOODS, TEMPLATES = parse_foods(), parse_templates()
SLOTS = ["Breakfast", "Lunch", "Snack", "Dinner"]


def make(**over):
    prof = {**DEMO_PROFILE, **over}
    return generate_meal_plan(compute_targets(prof), build_constraints(prof), FOODS, TEMPLATES), prof


# ---------------------------------------------------------------- templates
def test_template_library():
    assert len(TEMPLATES) >= 12
    assert len({t["id"] for t in TEMPLATES}) == len(TEMPLATES)
    assert {t["diet"] for t in TEMPLATES} == {"vegetarian", "eggetarian", "non-vegetarian"}
    names = set(FOODS["food_name"])
    for t in TEMPLATES:
        assert list(t["slots"]) == SLOTS, t["id"]
        for slot, items in t["slots"].items():
            if slot != "Snack":  # snacks have no counted main dish
                assert any(i["role"] == "main" for i in items), (t["id"], slot)
            for i in items:
                assert i["food"] in names, (t["id"], i["food"])
        # a template must honour its own diet label
        for items in t["slots"].values():
            for i in items:
                row = FOODS[FOODS["food_name"] == i["food"]].iloc[0]
                if t["diet"] == "vegetarian":
                    assert row["is_veg"], (t["id"], i["food"])
                if t["diet"] == "eggetarian":
                    assert row["is_veg"] or "egg" in row["allergens"], (t["id"], i["food"])


# ---------------------------------------------------------------- demo case
def test_demo_plan_shape_and_constraints():
    plan, prof = make()
    t = compute_targets(prof)
    assert len(plan["days"]) == 7
    for d in plan["days"]:
        assert list(d["meals"]) == SLOTS
        assert all(d["meals"][s]["items"] for s in SLOTS)
        assert abs(d["totals"]["calories"] - t["calories"]) <= 0.10 * t["calories"], d["day"]
    names = {i["food_name"] for d in plan["days"] for s in SLOTS for i in d["meals"][s]["items"]}
    eligible = set(filter_foods(FOODS, build_constraints(prof))["food_name"])
    assert names <= eligible
    assert plan["weekly"]["within_budget"] and plan["weekly"]["cost_inr"] <= 1500
    assert max(plan["main_dish_counts"].values()) <= 2
    assert plan["validation_status"] == "not_validated"


def test_totals_are_consistent():
    plan, _ = make()
    for d in plan["days"]:
        assert d["totals"]["calories"] == pytest.approx(sum(d["meals"][s]["totals"]["calories"] for s in SLOTS), abs=0.5)
        for s in SLOTS:
            assert d["meals"][s]["totals"]["cost_inr"] == pytest.approx(sum(i["cost_inr"] for i in d["meals"][s]["items"]), abs=0.5)
    assert plan["weekly"]["cost_inr"] == pytest.approx(sum(d["totals"]["cost_inr"] for d in plan["days"]), abs=0.5)
    assert len(meal_plan_to_df(plan)) == sum(len(d["meals"][s]["items"]) for d in plan["days"] for s in SLOTS)


def test_deterministic_and_variation():
    a, prof = make()
    b, _ = make()
    assert [d["template_id"] for d in a["days"]] == [d["template_id"] for d in b["days"]]
    t, c = compute_targets(prof), build_constraints(prof)
    v1 = generate_meal_plan(t, c, FOODS, TEMPLATES, variation=1)
    assert [d["template_id"] for d in v1["days"]] != [d["template_id"] for d in a["days"]]


def test_peanut_substitution_is_explained():
    prof = {**DEMO_PROFILE, "cuisine": "North Indian"}
    plan = generate_meal_plan(compute_targets(prof), build_constraints(prof), FOODS, TEMPLATES, variation=4)
    for s in plan["substitutions"]:
        assert s["reason"] and s["replacement"]
    assert not any("Peanut" in i["food_name"] or "Poha with Peanuts" == i["food_name"]
                   for d in plan["days"] for sl in SLOTS for i in d["meals"][sl]["items"])


# ---------------------------------------------------------------- constraint matrix
DIETS = ["Vegetarian", "Eggetarian", "Non-vegetarian", "Vegan", "Jain"]
CUISINES = ["North Indian", "South Indian", "Mixed Indian"]
ALLERGY_SETS = [[], ["peanut"], ["dairy", "gluten"], ["soy", "tree_nuts"]]


@pytest.mark.parametrize("diet,cuisine,allergies", list(itertools.product(DIETS, CUISINES, ALLERGY_SETS)))
def test_constraint_matrix(diet, cuisine, allergies):
    if diet == "Eggetarian" and "egg" in allergies:
        pytest.skip("conflicting profile is rejected at the form")
    prof = {**DEMO_PROFILE, "diet": diet, "cuisine": cuisine, "allergies": allergies, "max_prep_time_min": 30,
            "budget_weekly_inr": 3000}
    t, c = compute_targets(prof), build_constraints(prof)
    plan = generate_meal_plan(t, c, FOODS, TEMPLATES)
    assert len(plan["days"]) == 7
    eligible = set(filter_foods(FOODS, c)["food_name"])
    for d in plan["days"]:
        for s in SLOTS:
            assert d["meals"][s]["items"], (diet, cuisine, allergies, d["day"], s)
            for i in d["meals"][s]["items"]:
                assert i["food_name"] in eligible, (diet, cuisine, allergies, i["food_name"])
        assert abs(d["totals"]["calories"] - t["calories"]) <= 0.15 * t["calories"]


# ---------------------------------------------------------------- UI
def test_meal_plan_page_flow_and_regenerate():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    at.switch_page("ui/pages/2_Profile.py").run()
    at.button[1].click().run()
    at.switch_page("ui/pages/4_Meal_Plan.py").run()
    assert not at.exception
    [b for b in at.button if "Generate 7-day" in b.label][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    plan = at.session_state["meal_plan"]
    assert len(plan["days"]) == 7 and at.session_state["targets"] is not None
    assert at.session_state["workflow_stages"]["Meal Planner"] in ("done", "warning")
    acts = [(e["agent_name"], e["action"]) for e in at.session_state["agent_log"]]
    assert ("Meal Planner Agent", "generate_meal_plan") in acts
    assert len(at.session_state["eval_runs"]) == 1
    [b for b in at.button if "Regenerate" in b.label][0].click().run()
    assert not at.exception
    assert at.session_state["meal_plan"]["variation"] == 1
    assert len(at.session_state["eval_runs"]) == 2
    at.switch_page("ui/pages/1_Overview.py").run()
    assert not at.exception
    at.switch_page("ui/pages/8_Agent_Log.py").run()
    assert not at.exception

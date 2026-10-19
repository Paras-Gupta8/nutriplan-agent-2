from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agents.nutrition_agent import build_targets
from agents.planner import build_constraints, build_execution_plan
from core.meal_engine import filter_foods, parse_foods
from core.nutrition import DEMO_PROFILE

ROOT = Path(__file__).resolve().parent.parent
FOODS = parse_foods()


def pool(**overrides):
    prof = {**DEMO_PROFILE, **overrides}
    return filter_foods(FOODS, build_constraints(prof))


def test_vegetarian_peanut_free_demo_pool():
    p = pool()
    assert p["is_veg"].all()
    assert not p["allergens"].apply(lambda a: "peanut" in a).any()
    assert set(p["cuisine"]) <= {"North Indian", "Pan-Indian"}
    assert (p["prep_time_min"] <= 30).all()


def test_vegan_excludes_dairy_and_egg():
    p = pool(diet="Vegan", allergies=[], cuisine="Mixed Indian")
    assert not p["allergens"].apply(lambda a: bool({"dairy", "egg"} & set(a))).any()
    assert "Paneer Bhurji" not in set(p["food_name"])


def test_jain_excludes_jain_unsafe():
    p = pool(diet="Jain", allergies=[], cuisine="Mixed Indian")
    assert p["is_veg"].all()
    assert not p["tags"].apply(lambda t: "jain_unsafe" in t).any()


def test_eggetarian_has_eggs_but_no_meat_or_fish():
    p = pool(diet="Eggetarian", allergies=[], cuisine="Mixed Indian")
    names = set(p["food_name"])
    assert "Boiled Eggs" in names
    assert not ({"Chicken Curry", "Fish Curry (Rohu)", "Prawn Masala"} & names)


def test_non_veg_includes_chicken_and_allergen_filter_still_applies():
    p = pool(diet="Non-vegetarian", allergies=["fish", "shellfish"], cuisine="Mixed Indian", max_prep_time_min=60)
    assert "Chicken Curry" in set(p["food_name"])
    assert not p["allergens"].apply(lambda a: bool({"fish", "shellfish"} & set(a))).any()


def test_south_indian_filter():
    p = pool(cuisine="South Indian", allergies=[])
    assert "North Indian" not in set(p["cuisine"])


def test_execution_plan_structure_for_demo():
    plan = build_execution_plan(DEMO_PROFILE, FOODS)
    assert plan["task"] == "Generate a 7-day vegetarian North Indian meal plan"
    assert plan["steps"][0] == "Calculate nutrition targets" and len(plan["steps"]) == 6
    c = plan["constraints"]
    assert c["allergies"] == ["peanut"] and c["budget_weekly_inr"] == 1500 and c["max_prep_time_min"] == 30
    f = plan["feasibility"]
    assert not f["warnings"], f["warnings"]
    assert f["filter_funnel"][-1]["remaining"] == f["eligible_foods"]


def test_planner_warns_on_tiny_pool():
    prof = {**DEMO_PROFILE, "diet": "Vegan", "allergies": ["soy", "gluten", "tree_nuts", "peanut"], "max_prep_time_min": 1}
    plan = build_execution_plan(prof, FOODS)
    assert plan["feasibility"]["warnings"]


def test_nutrition_agent_warnings():
    assert build_targets(DEMO_PROFILE)["warnings"] == []
    thin = {**DEMO_PROFILE, "weight_kg": 45, "height_cm": 180}
    assert any("underweight" in w for w in build_targets(thin)["warnings"])


def test_full_ui_flow_and_log_order():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=30).run()
    at.switch_page("ui/pages/2_Profile.py").run()
    at.button[1].click().run()
    at.switch_page("ui/pages/3_Nutrition_Targets.py").run()
    assert not at.exception
    run_btn = [b for b in at.button if "Run Planner" in b.label][0]
    run_btn.click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["targets"]["calories"] == 1836
    assert at.session_state["execution_plan"]["constraints"]["diet"] == "vegetarian"
    actions = [(e["agent_name"], e["action"]) for e in at.session_state["agent_log"]]
    assert actions == [
        ("Orchestrator", "profile_saved"), ("Orchestrator", "workflow_started"),
        ("Planner Agent", "create_execution_plan"), ("Nutrition Agent", "calculate_targets"),
        ("Orchestrator", "planning_pipeline_complete"),
    ]
    assert set(at.session_state["workflow_stages"][s] for s in ("Planner", "Nutrition")) == {"done"}
    # log page renders
    at.switch_page("ui/pages/8_Agent_Log.py").run()
    assert not at.exception
    # changing the profile invalidates results
    at.switch_page("ui/pages/2_Profile.py").run()
    at.selectbox(key="pf_goal") if False else None
    at.radio(key="pf_goal").set_value("Muscle gain")
    at.button[1].click().run()
    assert at.session_state["targets"] is None

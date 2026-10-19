import itertools
from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from agents.planner import build_constraints
from core.meal_engine import filter_foods, generate_meal_plan, parse_foods, parse_templates
from core.nutrition import DEMO_PROFILE, compute_targets
from core.report import validation_report_dict
from core.revision import MAX_REVISIONS, _day_from_plan, _rank, revise_plan, revise_until_valid
from core.validator import CHECKS, validate_plan
from core.meal_engine import _finalize_day

ROOT = Path(__file__).resolve().parent.parent
FOODS, TEMPLATES = parse_foods(), parse_templates()
BY_NAME = {r["food_name"]: r for r in FOODS.to_dict("records")}
SLOTS = ["Breakfast", "Lunch", "Snack", "Dinner"]


def setup(**over):
    prof = {**DEMO_PROFILE, **over}
    t, c = compute_targets(prof), build_constraints(prof)
    return generate_meal_plan(t, c, FOODS, TEMPLATES), t, c


def item(name, role="side"):
    f = BY_NAME[name]
    return {"food_id": f["food_id"], "food_name": name, "category": f["category"], "role": role, "servings": 1.0,
            "serving_label": f["serving_label"], "grams": f["serving_grams"], "prep_time_min": f["prep_time_min"], "fixed": False,
            "calories": f["calories"], "protein_g": f["protein_g"], "carbs_g": f["carbs_g"], "fat_g": f["fat_g"],
            "fiber_g": f["fiber_g"], "cost_inr": f["cost_inr"]}


@pytest.fixture(scope="module")
def valid():
    plan, t, c = setup()
    res = revise_until_valid(plan, t, c, FOODS)
    assert res["report"]["counts"]["fail"] == 0
    return res["plan"], t, c, plan


def check_status(report, cid):
    return next(ch for ch in report["checks"] if ch["id"] == cid)["status"]


# ---------------------------------------------------------------- structure & scoring
def test_check_weights_sum_to_100():
    assert sum(c[2] for c in CHECKS) == 100 and len(CHECKS) == 9


def test_valid_plan_report_shape(valid):
    plan, t, c, _ = valid
    rep = validate_plan(plan, t, c, FOODS)
    assert rep["overall_status"] in ("passed", "passed_with_warnings") and rep["score"] >= 90
    assert {ch["id"] for ch in rep["checks"]} == {x[0] for x in CHECKS}
    assert rep["counts"]["fail"] == 0 and rep["critical_failures"] == []
    for ch in rep["checks"]:
        assert ch["status"] in ("pass", "warning", "fail") and ch["message"]


# ---------------------------------------------------------------- every check detects its fault
def mut_calories(p): p["days"][0]["totals"]["calories"] *= 1.3
def mut_protein(p): p["days"][1]["totals"]["protein_g"] = 20
def mut_allergen(p): p["days"][0]["meals"]["Snack"]["items"].append(item("Roasted Peanuts"))
def mut_diet(p): p["days"][0]["meals"]["Lunch"]["items"].append(item("Chicken Curry"))
def mut_cuisine(p): p["days"][0]["meals"]["Breakfast"]["items"].append(item("Idli"))
def mut_budget(p): p["days"][0]["totals"]["cost_inr"] += 2000
def mut_meals(p): p["days"][1]["meals"]["Lunch"]["items"] = []
def mut_fiber(p): p["days"][2]["totals"]["fiber_g"] = 3


def mut_variety(p):
    for d in p["days"][:3]:
        first = d["meals"]["Breakfast"]["items"][0]
        first["food_name"], first["role"] = "Besan Chilla", "main"


FAULTS = [("calories", mut_calories), ("protein", mut_protein), ("allergens", mut_allergen), ("diet", mut_diet),
          ("cooking_cuisine", mut_cuisine), ("budget", mut_budget), ("meals", mut_meals), ("fiber", mut_fiber),
          ("variety", mut_variety)]


@pytest.mark.parametrize("cid,mutate", FAULTS)
def test_fault_detected(valid, cid, mutate):
    plan, t, c, _ = valid
    base = validate_plan(plan, t, c, FOODS)
    bad = deepcopy(plan)
    mutate(bad)
    rep = validate_plan(bad, t, c, FOODS)
    assert check_status(rep, cid) == "fail", rep["checks"]
    assert rep["overall_status"] == "revision_required"
    assert rep["score"] < base["score"]
    assert any(i["check"] == cid for i in rep["feedback"])
    for f in rep["feedback"]:
        assert f["problem"] and f["suggestion"]
    if cid in ("allergens", "diet", "meals", "cooking_cuisine", "budget"):
        assert cid in rep["critical_failures"]


def test_warning_levels(valid):
    plan, t, c, _ = valid
    bad = deepcopy(plan)
    cost = sum(d["totals"]["cost_inr"] for d in bad["days"])
    bad["days"][0]["totals"]["cost_inr"] += c["budget_weekly_inr"] * 1.03 - cost   # 3% over budget
    rep = validate_plan(bad, t, c, FOODS)
    assert check_status(rep, "budget") == "warning" and rep["counts"]["fail"] == 0
    assert rep["overall_status"] == "passed_with_warnings"
    snack = deepcopy(plan)
    snack["days"][3]["meals"]["Snack"]["items"] = []
    rep = validate_plan(snack, t, c, FOODS)
    assert check_status(rep, "meals") == "warning" and rep["counts"]["fail"] == 0


def test_prep_time_limit_uses_constraints(valid):
    plan, t, c, _ = valid
    strict = {**c, "max_prep_time_min": 15}
    rep = validate_plan(plan, t, strict, FOODS)
    assert check_status(rep, "cooking_cuisine") == "fail"


# ---------------------------------------------------------------- revision loop
def test_demo_detects_and_corrects(valid):
    fixed, t, c, original = valid
    first = validate_plan(original, t, c, FOODS)
    assert first["overall_status"] == "revision_required"
    assert {i["check"] for i in first["issues"]} == {"protein"}
    res = revise_until_valid(original, t, c, FOODS)
    assert res["revisions_used"] <= MAX_REVISIONS and res["revisions_used"] >= 1
    assert res["report"]["counts"]["fail"] == 0
    assert res["history"][0]["actions"], "revision actions must be recorded"
    eligible = set(filter_foods(FOODS, c)["food_name"])
    names = {i["food_name"] for d in res["plan"]["days"] for s in SLOTS for i in d["meals"][s]["items"]}
    assert names <= eligible
    assert res["history"][-1]["metrics"]["avg_protein_g"] >= res["history"][0]["metrics"]["avg_protein_g"]


def test_budget_violation_is_corrected():
    plan, t, c = setup(budget_weekly_inr=1000)
    assert plan["weekly"]["cost_inr"] > 1000
    res = revise_until_valid(plan, t, c, FOODS)
    cost = res["plan"]["weekly"]["cost_inr"]
    assert cost <= 1050                                  # at worst inside the 5% warning band
    assert cost < plan["weekly"]["cost_inr"] - 100       # a substantial saving was achieved
    assert check_status(res["report"], "budget") != "fail"
    assert res["revisions_used"] <= 2
    # a comfortably reachable budget is met outright
    plan2, t2, c2 = setup(budget_weekly_inr=1100)
    res2 = revise_until_valid(plan2, t2, c2, FOODS)
    assert res2["plan"]["weekly"]["cost_inr"] <= 1100 and check_status(res2["report"], "budget") == "pass"


def test_max_two_revisions_then_best_plan_with_warnings():
    plan, t, c = setup(budget_weekly_inr=250)         # impossible budget
    res = revise_until_valid(plan, t, c, FOODS)
    assert res["revisions_used"] == 2 and len(res["history"]) == 3
    assert res["report"]["overall_status"] == "revision_required"
    assert check_status(res["report"], "budget") == "fail"
    assert res["plan"]["weekly"]["cost_inr"] < plan["weekly"]["cost_inr"]
    assert _rank(res["report"]) >= _rank(res["initial_report"])
    assert res["plan"]["validation_status"] == "revision_required"


@pytest.mark.parametrize("cid,mutate", [("allergens", mut_allergen), ("diet", mut_diet), ("cooking_cuisine", mut_cuisine),
                                        ("meals", mut_meals), ("variety", mut_variety)])
def test_loop_repairs_injected_faults(valid, cid, mutate):
    plan, t, c, _ = valid
    bad = deepcopy(plan)
    mutate(bad)
    res = revise_until_valid(bad, t, c, FOODS)
    assert check_status(res["report"], cid) != "fail", (cid, res["report"]["checks"])
    assert res["revisions_used"] <= 2


def test_calorie_fault_repaired(valid):
    plan, t, c, _ = valid
    foods_by_id = {r["food_id"]: r for r in FOODS.to_dict("records")}
    bad = deepcopy(plan)
    day = _day_from_plan(bad["days"][0], foods_by_id)
    for items in day.values():
        for i in items:
            i["servings"] = min(i["servings"] * 1.5, 4)
    bad["days"][0] = {**bad["days"][0], **_finalize_day(day)}
    assert check_status(validate_plan(bad, t, c, FOODS), "calories") == "fail"
    res = revise_until_valid(bad, t, c, FOODS)
    assert check_status(res["report"], "calories") == "pass"


def test_revise_plan_keeps_structure(valid):
    plan, t, c, _ = valid
    rep = validate_plan(plan, t, c, FOODS)
    new_plan, actions = revise_plan(plan, rep["issues"], t, c, FOODS, 1)
    assert len(new_plan["days"]) == 7 and all(list(d["meals"]) == SLOTS for d in new_plan["days"])
    assert new_plan["revision"] == 1 and actions == []


# ---------------------------------------------------------------- matrix
DIETS = ["Vegetarian", "Eggetarian", "Non-vegetarian", "Vegan", "Jain"]
CUISINES = ["North Indian", "South Indian", "Mixed Indian"]
ALLERGIES = [[], ["peanut"], ["dairy", "gluten"], ["soy", "tree_nuts"]]


def test_revision_matrix_never_worse_and_never_unsafe():
    valid_n = total = 0
    for diet, cuisine, al in itertools.product(DIETS, CUISINES, ALLERGIES):
        prof = {**DEMO_PROFILE, "diet": diet, "cuisine": cuisine, "allergies": al}
        t, c = compute_targets(prof), build_constraints(prof)
        plan = generate_meal_plan(t, c, FOODS, TEMPLATES)
        res = revise_until_valid(plan, t, c, FOODS)
        total += 1
        assert res["revisions_used"] <= MAX_REVISIONS
        assert res["report"]["critical_failures"] == [], (diet, cuisine, al)
        assert _rank(res["report"]) >= _rank(res["initial_report"]), (diet, cuisine, al)
        valid_n += res["report"]["counts"]["fail"] == 0
    assert valid_n / total >= 0.70, f"only {valid_n}/{total} profiles end with zero failed checks"


# ---------------------------------------------------------------- report + UI
def test_report_json_serialisable(valid):
    import json
    plan, t, c, original = valid
    res = revise_until_valid(original, t, c, FOODS)
    val = {"report": res["report"], "history": res["history"], "revisions_used": res["revisions_used"],
           "best_attempt": res["best_attempt"], "initial_metrics": res["history"][0]["metrics"],
           "final_metrics": res["history"][-1]["metrics"]}
    out = validation_report_dict(val, t, DEMO_PROFILE, res["plan"])
    json.dumps(out)
    assert out["revisions_used"] == res["revisions_used"] and len(out["checks"]) == 9


def test_validation_ui_flow_and_log():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=90).run()
    at.switch_page("ui/pages/2_Profile.py").run()
    at.button[1].click().run()
    at.switch_page("ui/pages/4_Meal_Plan.py").run()
    [b for b in at.button if "Generate 7-day" in b.label][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    val = at.session_state["validation"]
    assert val is not None and val["revisions_used"] >= 1 and val["report"]["counts"]["fail"] == 0
    assert at.session_state["workflow_stages"]["Validator"] in ("done", "warning")
    log = [(e["agent_name"], e["action"].split(" (")[0], e["status"]) for e in at.session_state["agent_log"]]
    assert ("Validator Agent", "validate_plan", "revision_required") in log
    assert ("Meal Planner Agent", "revise_meal_plan", "success") in log
    assert any(a == "Validator Agent" and act == "revalidate_plan" for a, act, _ in log)
    run = at.session_state["eval_runs"][-1]
    assert run["valid_first_pass"] is False and run["valid_final"] is True and run["revisions"] == val["revisions_used"]
    for page in ("5_Validation", "4_Meal_Plan", "1_Overview", "8_Agent_Log"):
        at.switch_page(f"ui/pages/{page}.py").run()
        assert not at.exception, (page, [e.value for e in at.exception])
    at.switch_page("ui/pages/5_Validation.py").run()
    [b for b in at.button if "Re-run validation" in b.label][0].click().run()
    assert not at.exception
    assert at.session_state["validation"]["revisions_used"] == 0   # already-fixed plan passes straight away

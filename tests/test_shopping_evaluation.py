import itertools
import json
import math

import pytest

from agents.planner import build_constraints
from core import evaluation as ev
from core.adaptation import apply_feedback, parse_feedback, parse_rules
from core.meal_engine import generate_meal_plan, parse_foods, parse_templates
from core.nutrition import DEMO_PROFILE, compute_targets
from core.report import buy_quantity, parse_ingredient_map, shopping_list
from core.revision import revise_until_valid

FOODS, TEMPLATES, RULES, IMAP = parse_foods(), parse_templates(), parse_rules(), parse_ingredient_map()
GROUPS = {"Grains, flours & breads", "Pulses & legumes", "Soya & protein foods", "Dairy & alternatives", "Eggs, meat & fish",
          "Vegetables & greens", "Fruits", "Nuts, seeds & dry snacks", "Oils, spices & others"}


def final_plan(**over):
    prof = {**DEMO_PROFILE, **over}
    t, c = compute_targets(prof), build_constraints(prof)
    return revise_until_valid(generate_meal_plan(t, c, FOODS, TEMPLATES), t, c, FOODS)["plan"], t, c


# ---------------------------------------------------------------- shopping list
def test_every_food_has_an_ingredient_mapping():
    assert set(IMAP) == set(FOODS["food_name"])
    for name, comps in IMAP.items():
        assert comps and all(c["qty"] > 0 and c["unit"] in ("g", "ml", "eggs") and c["group"] in GROUPS for c in comps), name


def test_shopping_cost_is_conserved_and_quantities_sensible():
    plan, *_ = final_plan()
    sl = shopping_list(plan, IMAP)
    assert sl["total_cost_inr"] == pytest.approx(plan["weekly"]["cost_inr"], abs=0.5)
    assert sl["ingredient_cost_inr"] == pytest.approx(sl["total_cost_inr"], abs=0.5)
    assert sl["dishes"] and sum(r["cost_inr"] for r in sl["dishes"]) == pytest.approx(sl["total_cost_inr"], abs=1.0)
    assert all(r["quantity"] > 0 and r["cost_inr"] >= 0 and r["group"] in GROUPS and r["buy"] for r in sl["ingredients"])
    groups = [r["group"] for r in sl["ingredients"]]
    assert groups == sorted(groups, key=lambda g: list(GROUPS_ORDER).index(g))


GROUPS_ORDER = ["Grains, flours & breads", "Pulses & legumes", "Soya & protein foods", "Dairy & alternatives", "Eggs, meat & fish",
                "Vegetables & greens", "Fruits", "Nuts, seeds & dry snacks", "Oils, spices & others"]


@pytest.mark.parametrize("diet,cuisine", list(itertools.product(["Vegetarian", "Eggetarian", "Non-vegetarian", "Vegan", "Jain"], ["North Indian", "South Indian"])))
def test_shopping_conservation_across_profiles(diet, cuisine):
    plan, *_ = final_plan(diet=diet, cuisine=cuisine, allergies=[])
    sl = shopping_list(plan, IMAP)
    assert sl["ingredient_cost_inr"] == pytest.approx(plan["weekly"]["cost_inr"], abs=1.0)
    if diet == "Vegan":
        assert not any(r["group"] == "Eggs, meat & fish" or "paneer" in r["ingredient"].lower() or r["ingredient"] in ("Curd", "Milk", "Ghee")
                       for r in sl["ingredients"])


def test_day_subset_adds_up_to_whole_week():
    plan, *_ = final_plan()
    whole = shopping_list(plan, IMAP)["total_cost_inr"]
    parts = shopping_list(plan, IMAP, [1, 2, 3])["total_cost_inr"] + shopping_list(plan, IMAP, [4, 5, 6, 7])["total_cost_inr"]
    assert parts == pytest.approx(whole, abs=0.6)
    assert shopping_list(plan, IMAP, [2])["days"] == [2]


def test_skipped_and_eating_out_meals_are_not_groceries():
    plan, t, c = final_plan()
    whole = shopping_list(plan, IMAP)["total_cost_inr"]
    skipped, *_ = apply_feedback(plan, parse_feedback("I skipped lunch", FOODS, 3)["actions"], t, c, FOODS, RULES)
    sl = shopping_list(skipped, IMAP, [3])
    assert sl["total_cost_inr"] == pytest.approx(skipped["days"][2]["totals"]["cost_inr"], abs=0.5)
    out, *_ = apply_feedback(plan, parse_feedback("I ate out for dinner", FOODS, 3)["actions"], t, c, FOODS, RULES)
    sl = shopping_list(out, IMAP, [3])
    assert sl["outside_meals"] and sl["outside_meals"][0]["meal"] == "Dinner"
    assert sl["total_cost_inr"] == pytest.approx(out["days"][2]["totals"]["cost_inr"] - sl["outside_meals"][0]["cost_inr"], abs=0.5)
    assert all("Restaurant" not in r["dish"] for r in sl["dishes"])


@pytest.mark.parametrize("qty,unit,expected", [(7, "g", "10 g"), (95, "g", "100 g"), (368, "g", "400 g"), (1240, "g", "1.3 kg"),
                                               (1000, "g", "1.0 kg"), (250, "ml", "250 ml"), (2300, "ml", "2.3 L"), (3, "eggs", "3 eggs"), (2.2, "eggs", "3 eggs")])
def test_buy_quantity_rounding(qty, unit, expected):
    assert buy_quantity(qty, unit) == expected


def test_unmapped_food_falls_back_to_the_dish_itself():
    plan, *_ = final_plan()
    sl = shopping_list(plan, {}, None)         # empty map -> every dish becomes its own ingredient
    assert sl["ingredient_cost_inr"] == pytest.approx(plan["weekly"]["cost_inr"], abs=0.5) and sl["ingredients"]


# ---------------------------------------------------------------- evaluation
@pytest.fixture(scope="module")
def bench():
    return ev.run_benchmark(ev.benchmark_profiles("quick"), FOODS, TEMPLATES, RULES, True)


def test_benchmark_sizes():
    assert [len(ev.benchmark_profiles(s)) for s in ("demo", "quick", "full")] == [1, 12, 60]


def test_benchmark_reports_all_required_metrics(bench):
    s = bench["summary"]
    assert s["profiles"] == 12
    ta = s["target_accuracy"]
    assert 0 <= ta["mean_calorie_error_pct"] < 10 and 80 <= ta["mean_protein_pct_of_target"] <= 130
    assert ta["days_calories_within_10pct_final"] == 100.0
    assert s["constraint_compliance"]["hard_constraints_pct"] == 100.0 and s["constraint_compliance"]["no_critical_failures_pct"] == 100.0
    va = s["validation"]
    assert va["after_revision_pct"] >= va["first_pass_pct"] and va["after_revision_pct"] >= 60
    assert sum(va["status_counts"].values()) == 12
    rv = s["revisions"]
    assert 0 <= rv["mean"] <= 2 and sum(rv["distribution"].values()) == 12 and rv["issues_final"] <= rv["issues_initial"]
    assert rv["issue_fix_rate_pct"] > 50
    tm = s["timing_ms"]
    assert 0 < tm["mean_total"] <= tm["max_total"] < 2000
    assert s["budget"]["within_budget_final_pct"] >= 90
    ad = s["adaptation"]
    assert ad["runs"] == 72 and ad["crashes"] == 0 and set(ad["per_phrase"]) == set(ev.BENCH_PHRASES)


def test_benchmark_is_deterministic_apart_from_timing():
    def strip(s):
        return {k: v for k, v in s.items() if k != "timing_ms"}
    a = ev.run_benchmark(ev.benchmark_profiles("demo"), FOODS, TEMPLATES, RULES, False)["summary"]
    b = ev.run_benchmark(ev.benchmark_profiles("demo"), FOODS, TEMPLATES, RULES, False)["summary"]
    assert strip(a) == strip(b)
    assert a["adaptation"] is None


def test_demo_benchmark_matches_direct_computation():
    row = ev.evaluate_profile(DEMO_PROFILE, FOODS, TEMPLATES, RULES, False)
    plan, t, c = final_plan()
    assert row["weekly_cost_inr"] == plan["weekly"]["cost_inr"] and row["final_status"] == plan["validation_status"]
    assert row["valid_first_pass"] is False and row["valid_final"] is True and row["hard_constraints_ok"] is True


def test_session_summary():
    assert ev.summarize_session([]) is None
    runs = [{"generation_ms": 20.0, "avg_calorie_error_pct": 3.0, "avg_protein_pct_of_target": 98.0, "within_budget": True,
             "valid_first_pass": False, "valid_final": True, "revisions": 1, "critical_failures": 0},
            {"generation_ms": 30.0, "avg_calorie_error_pct": 5.0, "avg_protein_pct_of_target": 96.0, "within_budget": False,
             "valid_first_pass": False, "valid_final": False, "revisions": 2, "critical_failures": 1},
            {"generation_ms": 10.0, "avg_calorie_error_pct": 1.0, "avg_protein_pct_of_target": 100.0, "within_budget": True}]
    s = ev.summarize_session(runs)
    assert (s["plans"], s["validated"]) == (3, 2) and s["mean_generation_ms"] == 20.0
    assert s["after_revision_pct"] == 50.0 and s["first_pass_pct"] == 0.0 and s["mean_revisions"] == 1.5
    assert s["budget_pct"] == pytest.approx(66.7, abs=0.1) and s["constraint_pct"] == 50.0


def test_benchmark_json_is_serialisable(bench):
    json.dumps({"summary": bench["summary"], "rows": bench["rows"]}, default=str)
    assert len(ev.rows_to_df(bench["rows"])) == 12

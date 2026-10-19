"""Final acceptance tests - one section per criterion in the project brief."""
import io
import json
import re
import socket
from datetime import datetime
from pathlib import Path

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from core.meal_engine import filter_foods, load_foods, parse_foods
from agents.planner import build_constraints
from core.nutrition import DEMO_PROFILE

ROOT = Path(__file__).resolve().parent.parent
SLOTS = ["Breakfast", "Lunch", "Snack", "Dinner"]
LOG_KEYS = {"timestamp", "agent_name", "action", "input_summary", "output_summary", "status", "details"}


def new_app():
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=180).run()


def run_demo(at):
    [b for b in at.button if "demo scenario" in b.label][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


@pytest.fixture(scope="module")
def demo():
    return run_demo(new_app())


def names_in(plan):
    return {i["food_name"] for d in plan["days"] for s in SLOTS for i in d["meals"][s]["items"]}


# ------------------------------------------------ required deliverables
REQUIRED_FILES = ["app.py", "requirements.txt", "README.md", "data/foods.csv", "data/meal_templates.json", "data/substitution_rules.json",
                  "core/state.py", "core/nutrition.py", "core/meal_engine.py", "core/validator.py", "core/adaptation.py", "core/report.py",
                  "agents/planner.py", "agents/nutrition_agent.py", "agents/meal_planner.py", "agents/validator_agent.py", "agents/adaptation_agent.py",
                  "ui/theme.py", "ui/components.py", "ui/pages/1_Overview.py", "ui/pages/2_Profile.py", "ui/pages/3_Nutrition_Targets.py",
                  "ui/pages/4_Meal_Plan.py", "ui/pages/5_Validation.py", "ui/pages/6_Shopping_List.py", "ui/pages/7_Daily_Adaptation.py",
                  "ui/pages/8_Agent_Log.py", "tests/test_validation.py"]


def test_required_file_structure():
    missing = [f for f in REQUIRED_FILES if not (ROOT / f).exists()]
    assert not missing, missing


def test_data_requirements():
    foods = parse_foods()
    assert len(foods) == 100
    templates = json.load(open(ROOT / "data/meal_templates.json"))["templates"]
    assert len(templates) >= 12 and {t["diet"] for t in templates} == {"vegetarian", "eggetarian", "non-vegetarian"}
    rules = json.load(open(ROOT / "data/substitution_rules.json"))["rules"]
    assert len(rules) >= 20


def test_state_and_caching_requirements(demo):
    for key in ("profile", "targets", "execution_plan", "meal_plan", "validation", "adaptation_history", "agent_log"):
        assert key in demo.session_state, key
    assert hasattr(load_foods, "clear"), "food database must be loaded through st.cache_data"


# ------------------------------------------------ no paid APIs, no internet
def test_no_paid_api_or_cloud_dependencies():
    forbidden = re.compile(r"^\s*(?:import|from)\s+(openai|anthropic|requests|httpx|boto3|langchain|google\.|cohere|mistralai|sqlalchemy|psycopg|pymongo|docker)\b", re.M)
    urls = set()
    for f in list((ROOT / "core").glob("*.py")) + list((ROOT / "agents").glob("*.py")) + list((ROOT / "ui").rglob("*.py")) + [ROOT / "app.py"]:
        text = f.read_text(encoding="utf-8")
        assert not forbidden.search(text), f"{f.name} imports a paid/cloud/db library"
        urls |= set(re.findall(r"https?://[^\s\"')]+", text))
    assert urls <= {"http://localhost:11434"}, urls
    reqs = (ROOT / "requirements.txt").read_text().lower()
    assert all(bad not in reqs for bad in ("openai", "anthropic", "requests", "sqlalchemy"))


def test_whole_app_works_with_network_disabled(monkeypatch):
    def no_net(*a, **k):
        raise OSError("network disabled for test")
    monkeypatch.setattr(socket.socket, "connect", no_net)
    at = run_demo(new_app())
    at.switch_page("ui/pages/7_Daily_Adaptation.py").run()
    assert not at.exception
    at.text_area(key="ad_text").set_value("I don't like paneer")
    [b for b in at.button if "Adapt my plan" in b.label][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["adaptation_history"][-1]["explanation_source"] == "rules"
    for pg in ("3_Nutrition_Targets", "4_Meal_Plan", "5_Validation", "6_Shopping_List", "8_Agent_Log", "9_Evaluation", "1_Overview"):
        at.switch_page(f"ui/pages/{pg}.py").run()
        assert not at.exception, (pg, [e.value for e in at.exception])


# ------------------------------------------------ demonstration scenario (acceptance criteria 1-3, 10)
def test_demo_scenario_end_to_end(demo):
    ss = demo.session_state
    p, t, plan, val = ss["profile"], ss["targets"], ss["meal_plan"], ss["validation"]
    assert (p["age"], p["diet"], p["goal"], p["budget_weekly_inr"], p["allergies"], p["cuisine"], p["max_prep_time_min"]) == \
        (22, "Vegetarian", "Fat loss", 1500, ["peanut"], "North Indian", 30)
    assert t["calories"] == 1836 and t["protein_g"] == 112                                  # 1. calorie and protein targets
    c = build_constraints(p)
    eligible = set(filter_foods(parse_foods(), c)["food_name"])
    assert names_in(plan) <= eligible                                                         # 2. peanut/diet/cuisine/time filtered
    assert not any("peanut" in n.lower() for n in names_in(plan))
    assert len(plan["days"]) == 7 and all(plan["days"][i]["meals"][s]["items"] for i in range(7) for s in SLOTS)   # 3. 7-day plan
    first = val["history"][0]                                                                 # 4. protein too low was detected ...
    assert first["status"] == "revision_required" and any(i["check"] == "protein" for i in first["issues"])
    assert val["revisions_used"] in (1, 2) and val["report"]["counts"]["fail"] == 0           # 5. ... and the plan was revised
    assert plan["weekly"]["cost_inr"] <= 1500 and plan["weekly"]["within_budget"]


def test_demo_substitution_paneer_to_soya_or_chana():
    at = run_demo(new_app())
    at.switch_page("ui/pages/7_Daily_Adaptation.py").run()
    at.text_area(key="ad_text").set_value("I don't like paneer")
    [b for b in at.button if "Adapt my plan" in b.label][0].click().run()
    assert not at.exception
    changes = at.session_state["adaptation_history"][-1]["changes"]
    assert changes and {c["to"] for c in changes} & {"Soya Chunks Curry", "Tofu Bhurji", "Chana Dal", "Roasted Chana", "Roasted Soya Nuts"}
    assert not any("paneer" in n.lower() for n in names_in(at.session_state["meal_plan"]))


# ------------------------------------------------ constraints (criterion 2) on other profiles
@pytest.mark.parametrize("diet,cuisine,allergies", [("Vegan", "South Indian", ["soy"]), ("Jain", "North Indian", []),
                                                    ("Non-vegetarian", "Mixed Indian", ["fish", "shellfish"]), ("Eggetarian", "North Indian", ["peanut", "gluten"])])
def test_other_profiles_respect_constraints(diet, cuisine, allergies):
    at = new_app()
    at.switch_page("ui/pages/2_Profile.py").run()
    at.selectbox(key="pf_diet").set_value(diet)
    at.selectbox(key="pf_cuisine").set_value(cuisine)
    at.multiselect(key="pf_allergies").set_value(allergies)
    at.number_input(key="pf_budget").set_value(2500)
    at.button[1].click().run()
    assert at.session_state["profile"]["diet"] == diet
    at.switch_page("ui/pages/4_Meal_Plan.py").run()
    [b for b in at.button if "Generate 7-day" in b.label][0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    plan, ss = at.session_state["meal_plan"], at.session_state
    eligible = set(filter_foods(parse_foods(), ss["execution_plan"]["constraints"])["food_name"])
    assert names_in(plan) <= eligible
    assert ss["validation"]["report"]["critical_failures"] == []


# ------------------------------------------------ adaptation + explanations + log (criteria 4-6)
def test_adaptation_updates_plan_and_logs():
    at = run_demo(new_app())
    before_cost = at.session_state["meal_plan"]["weekly"]["cost_inr"]
    at.switch_page("ui/pages/7_Daily_Adaptation.py").run()
    at.text_area(key="ad_text").set_value("Make the plan cheaper")
    [b for b in at.button if "Adapt my plan" in b.label][0].click().run()
    assert not at.exception
    ss = at.session_state
    assert ss["meal_plan"]["weekly"]["cost_inr"] < before_cost and ss["meal_plan"]["adapted"]
    assert ss["last_adaptation"]["report"]["overall_status"] in ("passed", "passed_with_warnings", "revision_required")
    assert ss["adaptation_history"][-1]["explanation"]


def test_every_recommendation_has_an_explanation(demo):
    ss = demo.session_state
    assert set(ss["targets"]["reasoning"]) >= {"bmr", "tdee", "calories", "protein", "carbs", "fat", "fiber", "bmi"}
    assert all(v for v in ss["targets"]["reasoning"].values())
    assert ss["execution_plan"]["rationale"] and all(ss["execution_plan"]["rationale"])
    assert all(d["rationale"] for d in ss["meal_plan"]["days"])
    assert all(c["message"] for c in ss["validation"]["report"]["checks"])
    for h in ss["validation"]["history"]:
        assert all(a["description"] for a in h["actions"]) and all(i["problem"] and i["suggestion"] for i in h["issues"])
    assert all(s["reason"] for s in ss["meal_plan"]["substitutions"])


def test_agent_workflow_visible_in_log(demo):
    log = demo.session_state["agent_log"]
    agents_in_order = []
    for e in log:
        if not agents_in_order or agents_in_order[-1] != e["agent_name"]:
            agents_in_order.append(e["agent_name"])
    assert {"Orchestrator", "Planner Agent", "Nutrition Agent", "Meal Planner Agent", "Validator Agent"} <= set(agents_in_order)
    first = {a: min(i for i, e in enumerate(log) if e["agent_name"] == a) for a in set(agents_in_order)}
    assert first["Planner Agent"] < first["Nutrition Agent"] < first["Meal Planner Agent"] < first["Validator Agent"]
    actions = [e["action"] for e in log]
    assert any(a.startswith("revise_meal_plan") for a in actions) and any(a.startswith("revalidate_plan") for a in actions)


def test_agent_log_format(demo):
    for e in demo.session_state["agent_log"]:
        assert set(e) == LOG_KEYS
        assert e["status"] in {"success", "warning", "revision_required", "failed"}
        datetime.fromisoformat(e["timestamp"])
        assert e["agent_name"] and e["action"]


# ------------------------------------------------ exports
def test_exports_are_valid(demo):
    from core.report import (agent_log_csv, agent_log_json, load_ingredient_map, meal_plan_csv, shopping_csv, shopping_json, shopping_list,
                             validation_report_json)
    ss = demo.session_state
    plan = ss["meal_plan"]
    csv = pd.read_csv(io.StringIO(meal_plan_csv(plan)))
    assert set(csv["Day"]) == set(range(1, 8)) and set(csv["Meal"]) == {"Breakfast", "Lunch", "Evening snack", "Dinner"}
    assert csv["Cost (₹)"].sum() == pytest.approx(plan["weekly"]["cost_inr"], abs=1.0)
    sl = shopping_list(plan, load_ingredient_map())
    scsv = pd.read_csv(io.StringIO(shopping_csv(sl)))
    assert len(scsv) == len(sl["ingredients"]) and scsv["est_cost_inr"].sum() == pytest.approx(plan["weekly"]["cost_inr"], abs=2.0)
    sj = json.loads(shopping_json(sl))
    assert sj["total_cost_inr"] == pytest.approx(plan["weekly"]["cost_inr"], abs=0.5) and sj["ingredients"] and sj["note"]
    log = json.loads(agent_log_json(ss["agent_log"]))
    assert len(log) == len(ss["agent_log"]) and set(log[0]) == LOG_KEYS
    assert len(pd.read_csv(io.StringIO(agent_log_csv(ss["agent_log"])))) == len(log)
    vr = json.loads(validation_report_json(ss["validation"], ss["targets"], ss["profile"], plan))
    assert vr["score"] == ss["validation"]["report"]["score"] and len(vr["checks"]) == 9 and vr["revision_history"]


# ------------------------------------------------ UI
def test_pages_render_with_disclaimer_and_empty_states():
    at = new_app()
    assert not at.exception
    for pg in ("2_Profile", "3_Nutrition_Targets", "4_Meal_Plan", "5_Validation", "6_Shopping_List", "7_Daily_Adaptation", "8_Agent_Log", "9_Evaluation"):
        at.switch_page(f"ui/pages/{pg}.py").run()
        assert not at.exception, (pg, [e.value for e in at.exception])


def test_medical_disclaimer_on_plan_pages(demo):
    for pg in ("4_Meal_Plan", "2_Profile", "5_Validation", "6_Shopping_List"):
        demo.switch_page(f"ui/pages/{pg}.py").run()
        assert any("not a medical device" in m.value for m in demo.markdown), pg


def test_lazy_tabs_keep_heavy_charts_off_the_default_view():
    at = run_demo(new_app())
    at.switch_page("ui/pages/4_Meal_Plan.py").run()

    def charts():
        return sum(len(at.get(k)) for k in ("vega_lite_chart", "arrow_vega_lite_chart"))
    assert charts() == 0
    at.radio(key="mp_view").set_value("📈 Weekly charts").run()
    assert charts() >= 4 and not at.exception


def test_reset_session_clears_everything():
    at = run_demo(new_app())
    at.switch_page("ui/pages/1_Overview.py").run()
    [b for b in at.button if "Reset session" in b.label][0].click().run()
    assert at.session_state["profile"] is None and at.session_state["agent_log"] == [] and at.session_state["meal_plan"] is None


# ------------------------------------------------ performance guard (generous thresholds)
def test_engine_is_fast():
    import time
    from core.meal_engine import generate_meal_plan, parse_templates
    from core.nutrition import compute_targets
    from core.revision import revise_until_valid
    foods, templates = parse_foods(), parse_templates()
    t, c = compute_targets(DEMO_PROFILE), build_constraints(DEMO_PROFILE)
    t0 = time.perf_counter()
    for _ in range(5):
        revise_until_valid(generate_meal_plan(t, c, foods, templates), t, c, foods)
    assert (time.perf_counter() - t0) / 5 < 0.5, "plan + validate + revise should take well under 0.5 s"

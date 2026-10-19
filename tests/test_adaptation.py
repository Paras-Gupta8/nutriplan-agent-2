import io
import json
import random
import string
from copy import deepcopy
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import core.adaptation as A
from agents.planner import build_constraints
from core.meal_engine import filter_foods, generate_meal_plan, parse_foods, parse_templates
from core.nutrition import DEMO_PROFILE, compute_targets
from core.revision import revise_until_valid
from core.validator import validate_plan

ROOT = Path(__file__).resolve().parent.parent
FOODS, TEMPLATES, RULES = parse_foods(), parse_templates(), A.parse_rules()
T, C = compute_targets(DEMO_PROFILE), build_constraints(DEMO_PROFILE)
SLOTS = ["Breakfast", "Lunch", "Snack", "Dinner"]


@pytest.fixture(scope="module")
def base():
    return revise_until_valid(generate_meal_plan(T, C, FOODS, TEMPLATES), T, C, FOODS)["plan"]


def adapt(plan, text, day=3, slot=None, constraints=None):
    parsed = A.parse_feedback(text, FOODS, day, slot)
    c = constraints or C
    new, changes, msgs, nd = A.apply_feedback(plan, parsed["actions"], T, c, FOODS, RULES)
    return new, changes, msgs, nd, validate_plan(new, T, c, FOODS), parsed


def names_in(plan, days=None):
    return {i["food_name"] for d in plan["days"] if days is None or d["day"] in days for s in SLOTS for i in d["meals"][s]["items"]}


def totals(plan, d):
    return plan["days"][d - 1]["totals"]


# ---------------------------------------------------------------- rules file
def test_rules_file_integrity():
    names = set(FOODS["food_name"])
    assert len(RULES) >= 30 and len({r["id"] for r in RULES}) == len(RULES)
    for r in RULES:
        assert r["kind"] in {"swap", "cost", "time", "protein"} and r["reason"] and r["alternatives"]
        assert r["names"] or r["keywords"]
        assert set(r["names"]) <= names and set(r["alternatives"]) <= names, r["id"]


def test_spec_required_rules_present():
    def alts(kind, food):
        return [a for r in RULES if r["kind"] == kind and A._rule_hit(r, food) for a in r["alternatives"]]
    assert {"Soya Chunks Curry", "Tofu Bhurji", "Chana Dal"} <= set(alts("swap", "Palak Paneer"))     # paneer -> soya, tofu, chana
    assert "Soy Yogurt" in alts("swap", "Plain Curd (Dahi)")                                            # curd -> soy yogurt
    assert {"Foxtail Millet (Cooked)", "Brown Rice (Cooked)"} <= set(alts("swap", "White Rice (Cooked)"))  # rice -> millets, brown rice
    assert alts("cost", "Almonds") and alts("cost", "Matar Paneer")                                      # high-cost -> cheaper
    assert alts("time", "Chole Masala") and alts("time", "Brown Rice (Cooked)")                          # high-prep -> quicker


# ---------------------------------------------------------------- parsing
@pytest.mark.parametrize("text,intent,days,slot", [
    ("I skipped lunch", "skipped", [2], "Lunch"),
    ("I ate out today", "ate_out", [2], None),
    ("I ate out for dinner", "ate_out", [2], "Dinner"),
    ("Make the plan cheaper", "cheaper", list(range(1, 8)), None),
    ("Make today high protein", "protein", [2], None),
    ("I have only 20 minutes to cook", "quick", [2], None),
    ("I don't like paneer", "dislike", list(range(1, 8)), None),
    ("I missed breakfast on day 5", "skipped", [5], "Breakfast"),
    ("make tomorrow quicker", "quick", [3], None),
    ("Make everything cheaper for the whole week", "cheaper", list(range(1, 8)), None),
])
def test_classification(text, intent, days, slot):
    r = A.parse_feedback(text, FOODS, ui_day=2)
    assert not r["unrecognized"] and r["actions"][0]["intent"] == intent, text
    assert r["actions"][0]["days"] == days and r["actions"][0]["slot"] == slot


def test_parse_details_and_compound():
    q = A.parse_feedback("I have only 20 minutes to cook", FOODS, 1)["actions"][0]
    assert q["minutes"] == 20
    o = A.parse_feedback("I ate out 900 kcal for lunch on day 3", FOODS, 1)["actions"][0]
    assert (o["kcal"], o["slot"], o["days"]) == (900, "Lunch", [3])
    multi = A.parse_feedback("I skipped breakfast and lunch, make it cheaper", FOODS, 4)["actions"]
    assert [(a["intent"], a["slot"]) for a in multi] == [("skipped", "Breakfast"), ("skipped", "Lunch"), ("cheaper", None)]
    d = A.parse_feedback("I don't like paneer and dal", FOODS, 1)["actions"][0]
    assert any("Paneer" in f for f in d["foods"]) and any("Dal" in f for f in d["foods"])
    assert A.parse_feedback("I don't like soya", FOODS, 1)["actions"][0]["foods"] and \
        all("soy" in f.lower() for f in A.parse_feedback("I don't like soya", FOODS, 1)["actions"][0]["foods"])


def test_unrecognised_and_fuzz_never_crash(base):
    assert A.parse_feedback("hello there", FOODS, 1)["unrecognized"]
    assert A.parse_feedback("", FOODS, 1)["unrecognized"]
    rnd = random.Random(7)
    for _ in range(40):
        txt = "".join(rnd.choice(string.ascii_lowercase + "  ,.'") for _ in range(rnd.randint(1, 60)))
        parsed = A.parse_feedback(txt, FOODS, rnd.randint(1, 7))
        A.apply_feedback(base, parsed["actions"], T, C, FOODS, RULES)      # must not raise


# ---------------------------------------------------------------- handlers
def test_skipped_lunch(base):
    new, ch, msgs, _, rep, _ = adapt(base, "I skipped lunch")
    day = new["days"][2]
    assert day["meals"]["Lunch"]["items"] == [] and day["meals"]["Lunch"]["skipped"] and day["skipped_meals"] == ["Lunch"]
    lost = base["days"][2]["meals"]["Lunch"]["totals"]["calories"]
    gap = base["days"][2]["totals"]["calories"] - day["totals"]["calories"]
    assert 0 < gap < lost, "later meals should recover part of the skipped calories"
    assert gap <= 0.6 * lost
    assert new["days"][2]["meals"]["Lunch"]["skipped_items"], "original meal is kept for undo/display"
    for d in (1, 2, 4, 5, 6, 7):
        assert totals(new, d) == totals(base, d), "other days must be untouched"
    assert rep["counts"]["fail"] == 0
    assert next(c for c in rep["checks"] if c["id"] == "meals")["status"] == "pass"
    assert ch[0]["kind"] == "skipped" and ch[0]["portion_changes"]


def test_skipped_last_meal_cannot_be_recovered(base):
    new, ch, msgs, _, rep, _ = adapt(base, "I skipped dinner")
    assert new["days"][2]["meals"]["Dinner"]["skipped"]
    assert "cannot be recovered" in ch[0]["reason"]
    assert rep["counts"]["fail"] == 0
    assert next(c for c in rep["checks"] if c["id"] == "calories")["status"] in ("warning", "pass")


def test_ate_out_with_kcal_override(base):
    new, ch, msgs, _, rep, _ = adapt(base, "I ate out 600 kcal for lunch")
    item = new["days"][2]["meals"]["Lunch"]["items"][0]
    assert item["external"] and abs(item["calories"] - 600) < 1 and new["days"][2]["meals"]["Lunch"]["ate_out"]
    before_later = sum(base["days"][2]["meals"][s]["totals"]["calories"] for s in ("Snack", "Dinner"))
    after_later = sum(new["days"][2]["meals"][s]["totals"]["calories"] for s in ("Snack", "Dinner"))
    assert after_later != before_later, "later meals are rebalanced around the restaurant meal"
    assert abs(totals(new, 3)["calories"] - T["calories"]) / T["calories"] < 0.12
    assert rep["counts"]["fail"] == 0
    assert "estimat" in A.build_explanation(ch, msgs, A.scope_totals(base, [3]), A.scope_totals(new, [3])).lower()


def test_ate_out_dinner_is_a_warning_not_a_failure(base):
    new, *_, rep, _ = adapt(base, "I ate out for dinner")
    assert rep["counts"]["fail"] == 0
    assert next(c for c in rep["checks"] if c["id"] == "calories")["status"] == "warning"
    assert next(c for c in rep["checks"] if c["id"] == "cooking_cuisine")["status"] == "pass", "external meal is not a database food"


def test_dislike_paneer_whole_week(base):
    assert any("Paneer" in n for n in names_in(base)), "fixture plan should contain paneer"
    new, ch, msgs, nd, rep, _ = adapt(base, "I don't like paneer")
    assert not any("paneer" in n.lower() for n in names_in(new))
    assert {c["from"].split(" (")[0] for c in ch} <= {n.split(" (")[0] for n in names_in(base)}
    for c in ch:
        assert c["reason"] and c["to"]
    assert any(c["rule_id"] for c in ch), "at least one swap should come from the rules file"
    assert nd and all("paneer" in n.lower() for n in nd)
    assert rep["counts"]["fail"] == 0
    c2 = {**C, "excluded_foods": nd}
    eligible = set(filter_foods(FOODS, c2)["food_name"])
    assert names_in(new) <= eligible


def test_dislike_uses_rule_alternatives_in_order():
    plan, _, _ = generate_meal_plan(T, C, FOODS, TEMPLATES), None, None
    plan = deepcopy(plan)
    new, ch, *_ = adapt(plan, "I don't like paneer")
    allowed = {a for r in RULES if r["kind"] == "swap" for a in r["alternatives"]}
    rule_based = [c for c in ch if c["rule_id"]]
    assert rule_based and all(c["to"] in allowed for c in rule_based)


def test_dislike_unknown_food_changes_nothing(base):
    new, ch, msgs, nd, rep, _ = adapt(base, "I don't like unicorn")
    assert ch == [] and msgs and nd == []


def test_dislike_persists_into_regeneration():
    c2 = {**C, "excluded_foods": ["Palak Paneer", "Matar Paneer", "Paneer Bhurji", "Paneer Cubes (Raw)", "Paneer Tikka (Pan-Grilled)"]}
    for v in range(4):
        p = generate_meal_plan(T, c2, FOODS, TEMPLATES, variation=v)
        assert not any("paneer" in n.lower() for n in names_in(p))
        assert not validate_plan(p, T, c2, FOODS)["critical_failures"]


def test_cheaper_reduces_weekly_cost(base):
    new, ch, msgs, _, rep, _ = adapt(base, "Make the plan cheaper")
    b, a = base["weekly"]["cost_inr"], new["weekly"]["cost_inr"]
    assert a < b * 0.95, (b, a)
    assert rep["critical_failures"] == []
    assert new["weekly"]["avg_protein_g"] >= 0.9 * base["weekly"]["avg_protein_g"]
    assert all(c["delta"]["cost_inr"] < 0 for c in ch)


def test_high_protein_today(base):
    new, ch, msgs, _, rep, _ = adapt(base, "Make today high protein", day=4)
    assert totals(new, 4)["protein_g"] >= totals(base, 4)["protein_g"] + 8
    assert totals(new, 4)["calories"] <= 1.10 * T["calories"]
    for d in (1, 2, 3, 5, 6, 7):
        assert totals(new, d) == totals(base, d)
    slots = [(c["day"], c["slot"]) for c in ch]
    assert len(slots) == len(set(slots)), "at most one swap per meal"
    assert max(new["main_dish_counts"].values()) <= 2
    assert rep["counts"]["fail"] == 0


def test_high_protein_every_day_stays_valid(base):
    for d in range(1, 8):
        new, *_, rep, _ = adapt(base, "make today high protein", day=d)
        assert totals(new, d)["protein_g"] >= totals(base, d)["protein_g"] - 0.1
        assert abs(totals(new, d)["calories"] - T["calories"]) / T["calories"] <= 0.10
        assert rep["counts"]["fail"] == 0, (d, [c["id"] for c in rep["checks"] if c["status"] == "fail"])


def test_quick_meals_respect_limit(base):
    new, ch, msgs, _, rep, _ = adapt(base, "I have only 20 minutes to cook", day=3)
    prep = [i["prep_time_min"] for s in SLOTS for i in new["days"][2]["meals"][s]["items"]]
    assert max(prep) <= 20 and ch
    assert all(c["delta"]["prep_time_min"] < 0 for c in ch)
    for d in (1, 2, 4):
        assert totals(new, d) == totals(base, d)
    assert rep["counts"]["fail"] == 0


def test_multiple_intents_in_one_message(base):
    new, ch, msgs, _, rep, _ = adapt(base, "I skipped breakfast and make it cheaper", day=2)
    assert {c["kind"] for c in ch} >= {"skipped", "cheaper"}
    assert new["days"][1]["meals"]["Breakfast"]["skipped"]
    assert rep["critical_failures"] == []


def test_revalidation_keeps_user_events(base):
    new, *_ = adapt(base, "I skipped lunch and I ate out for dinner on day 5")
    res = revise_until_valid(new, T, C, FOODS)
    d3, d5 = res["plan"]["days"][2], res["plan"]["days"][4]
    assert d3["meals"]["Lunch"]["items"] == [] and d3["skipped_meals"] == ["Lunch"]
    assert d5["meals"]["Dinner"]["items"][0]["external"] and d5["meals"]["Dinner"].get("ate_out")


# ---------------------------------------------------------------- explanations / Ollama
class FakeResp(io.BytesIO):
    def __enter__(self): return self
    def __exit__(self, *a): return False


def fake_ollama(monkeypatch, mode, rewrite=None):
    def urlopen(req, timeout=None):
        url = req if isinstance(req, str) else req.full_url
        if mode == "down":
            raise OSError("connection refused")
        if url.endswith("/api/tags"):
            return FakeResp(json.dumps({"models": [{"name": "llama3"}]}).encode())
        return FakeResp(json.dumps({"response": rewrite}).encode())
    monkeypatch.setattr(A.urllib.request, "urlopen", urlopen)


TEXT = "Day 3 dinner: Palak Paneer was replaced with Tofu Bhurji because tofu is dairy-free (saves ₹12, +5 g protein)."


def test_ollama_unavailable_falls_back(monkeypatch):
    fake_ollama(monkeypatch, "down")
    assert A.ollama_models() == []
    assert A.friendly_explanation(TEXT, True) == (TEXT, "rules")
    assert A.friendly_explanation(TEXT, False) == (TEXT, "rules")


def test_ollama_rewrite_accepted_when_numbers_preserved(monkeypatch):
    nice = "Good news! On Day 3 your dinner swaps Palak Paneer for Tofu Bhurji - dairy-free, it saves ₹12 and adds 5 g protein."
    fake_ollama(monkeypatch, "up", nice)
    assert A.friendly_explanation(TEXT, True) == (nice, "ollama")


@pytest.mark.parametrize("bad", ["Day 3 dinner now saves ₹20 and adds 9 g protein.", "", "x" * 2000])
def test_ollama_rewrite_rejected_when_facts_change(monkeypatch, bad):
    fake_ollama(monkeypatch, "up", bad)
    assert A.friendly_explanation(TEXT, True) == (TEXT, "rules")


# ---------------------------------------------------------------- UI / orchestration
def boot():
    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()
    at.switch_page("ui/pages/2_Profile.py").run()
    at.button[1].click().run()
    at.switch_page("ui/pages/4_Meal_Plan.py").run()
    [b for b in at.button if "Generate 7-day" in b.label][0].click().run()
    assert not at.exception
    at.switch_page("ui/pages/7_Daily_Adaptation.py").run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def submit(at, text, day=3):
    at.text_area(key="ad_text").set_value(text)
    at.selectbox[0].set_value(day)
    [b for b in at.button if "Adapt my plan" in b.label][0].click().run()
    assert not at.exception, [e.value for e in at.exception]


def test_ui_skip_flow_log_and_undo():
    at = boot()
    before = deepcopy(at.session_state["meal_plan"])
    submit(at, "I skipped lunch")
    ss = at.session_state
    assert len(ss["adaptation_history"]) == 1 and ss["meal_plan"]["days"][2]["meals"]["Lunch"]["skipped"]
    assert ss["validation"]["source"] == "adaptation" and ss["workflow_stages"]["Adaptation"] in ("done", "warning")
    acts = [(e["agent_name"], e["action"]) for e in ss["agent_log"]]
    for a in [("Adaptation Agent", "classify_feedback"), ("Adaptation Agent", "apply_substitutions"),
              ("Validator Agent", "revalidate_plan (after adaptation)"), ("Adaptation Agent", "explain_changes"),
              ("Orchestrator", "adaptation_complete")]:
        assert a in acts, a
    assert acts.index(("Adaptation Agent", "classify_feedback")) < acts.index(("Validator Agent", "revalidate_plan (after adaptation)"))
    for pg in ("4_Meal_Plan", "5_Validation", "1_Overview", "8_Agent_Log", "7_Daily_Adaptation"):
        at.switch_page(f"ui/pages/{pg}.py").run()
        assert not at.exception, (pg, [e.value for e in at.exception])
    [b for b in at.button if "Undo last adaptation" in b.label][0].click().run()
    assert at.session_state["adaptation_history"] == []
    assert [d["totals"] for d in at.session_state["meal_plan"]["days"]] == [d["totals"] for d in before["days"]]


def test_ui_dislike_survives_regeneration():
    at = boot()
    submit(at, "I don't like paneer")
    assert at.session_state["prefs"]["dislikes"]
    at.switch_page("ui/pages/4_Meal_Plan.py").run()
    [b for b in at.button if "Regenerate" in b.label][0].click().run()
    assert not at.exception
    plan = at.session_state["meal_plan"]
    assert not any("paneer" in n.lower() for n in names_in(plan))
    assert at.session_state["execution_plan"]["constraints"]["excluded_foods"]


def test_ui_unrecognised_text_is_harmless():
    at = boot()
    before = at.session_state["meal_plan"]
    submit(at, "what a lovely day")
    assert at.session_state["adaptation_history"] == [] and at.session_state["meal_plan"] == before
    assert any("didn't recognise" in w.value for w in at.warning)


def test_ui_example_chip_fills_text():
    at = boot()
    [b for b in at.button if b.label == "Make the plan cheaper"][0].click().run()
    assert at.text_area(key="ad_text").value == "Make the plan cheaper"

"""Report builders: meal plan table, shopping list (with ingredient breakdown), and download helpers."""
from __future__ import annotations

import json
import math
from collections import Counter
from datetime import datetime

import pandas as pd
import streamlit as st

from core.meal_engine import DATA_DIR

MEAL_ORDER = ["Breakfast", "Lunch", "Snack", "Dinner"]


def meal_plan_to_df(plan: dict) -> pd.DataFrame:
    """Flat table: one row per food item."""
    rows = []
    for d in plan["days"]:
        for slot in MEAL_ORDER:
            for it in d["meals"][slot]["items"]:
                rows.append({
                    "Day": d["day"], "Meal": slot if slot != "Snack" else "Evening snack", "Food": it["food_name"],
                    "Servings": it["servings"], "Portion": f"{it['servings']:g} × {it['serving_label']}",
                    "Grams": it["grams"], "Calories": it["calories"], "Protein (g)": it["protein_g"],
                    "Carbs (g)": it["carbs_g"], "Fat (g)": it["fat_g"], "Fiber (g)": it["fiber_g"], "Cost (₹)": it["cost_inr"],
                })
    return pd.DataFrame(rows)


def day_totals_df(plan: dict) -> pd.DataFrame:
    return pd.DataFrame([{"Day": d["day"], "Plan": d["template_name"], "Calories": d["totals"]["calories"],
                          "Protein (g)": d["totals"]["protein_g"], "Carbs (g)": d["totals"]["carbs_g"],
                          "Fat (g)": d["totals"]["fat_g"], "Fiber (g)": d["totals"]["fiber_g"],
                          "Cost (₹)": d["totals"]["cost_inr"]} for d in plan["days"]])


def checks_table_df(report: dict) -> pd.DataFrame:
    icon = {"pass": "✅ Pass", "warning": "⚠️ Warning", "fail": "❌ Fail"}
    return pd.DataFrame([{"Check": c["name"], "Status": icon[c["status"]], "Result": c["message"],
                          "Points": f"{c['weight'] * c['credit']:.1f} / {c['weight']}"} for c in report["checks"]])


def validation_report_dict(validation: dict, targets: dict, profile: dict, plan: dict) -> dict:
    """JSON-serialisable validation report for download."""
    rep = validation["report"]
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "disclaimer": "Educational wellness tool - not medical advice.",
        "profile": {k: profile[k] for k in ("age", "sex", "goal", "diet", "allergies", "cuisine", "budget_weekly_inr", "max_prep_time_min")},
        "targets": {k: targets[k] for k in ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g")},
        "overall_status": rep["overall_label"], "score": rep["score"], "counts": rep["counts"],
        "revisions_used": validation["revisions_used"], "best_attempt": validation["best_attempt"],
        "checks": [{k: c[k] for k in ("id", "name", "status", "weight", "credit", "message", "issues", "details")} for c in rep["checks"]],
        "revision_history": [{k: h[k] for k in ("attempt", "label", "score", "counts", "issues", "actions")} for h in validation["history"]],
        "nutrition_comparison": {"initial_plan": {k: v for k, v in validation["initial_metrics"].items() if k != "per_day"},
                                 "final_plan": {k: v for k, v in validation["final_metrics"].items() if k != "per_day"}},
        "weekly_cost_inr": plan["weekly"]["cost_inr"],
    }


def validation_report_json(validation: dict, targets: dict, profile: dict, plan: dict) -> str:
    return json.dumps(validation_report_dict(validation, targets, profile, plan), indent=2, ensure_ascii=False)


# ------------------------------------------------------------------ shopping list
INGREDIENT_MAP_PATH = DATA_DIR / "ingredient_map.json"
GROUP_ORDER = ["Grains, flours & breads", "Pulses & legumes", "Soya & protein foods", "Dairy & alternatives", "Eggs, meat & fish",
               "Vegetables & greens", "Fruits", "Nuts, seeds & dry snacks", "Oils, spices & others"]
# price weight per gram of each group - used only to split a dish's cost across its ingredients
COST_WEIGHT = {"Oils, spices & others": 6, "Dairy & alternatives": 2, "Eggs, meat & fish": 3, "Nuts, seeds & dry snacks": 3, "Soya & protein foods": 2}
UNIT_GRAMS = {"g": 1.0, "ml": 1.0, "eggs": 50.0}
CATEGORY_GROUP = {"Grain": "Grains, flours & breads", "Breakfast Dish": "Grains, flours & breads", "Legume": "Pulses & legumes",
                  "Dairy": "Dairy & alternatives", "Dairy Alternative": "Soya & protein foods", "Fruit": "Fruits",
                  "Nuts & Seeds": "Nuts, seeds & dry snacks", "Vegetable": "Vegetables & greens", "Salad": "Vegetables & greens"}


def parse_ingredient_map(path=INGREDIENT_MAP_PATH) -> dict:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["foods"]


@st.cache_data(show_spinner=False)
def load_ingredient_map() -> dict:
    return parse_ingredient_map()


def buy_quantity(qty: float, unit: str) -> str:
    """Round a needed quantity up to something you can actually buy, e.g. 1,240 g -> 1.3 kg."""
    if unit == "eggs":
        return f"{math.ceil(qty - 1e-9)} eggs"
    step = 10 if qty < 100 else (50 if qty < 1000 else 100)
    x = math.ceil(qty / step - 1e-9) * step
    if x >= 1000:
        return f"{x / 1000:.1f} {'L' if unit == 'ml' else 'kg'}"
    return f"{x:g} {unit}"


def shopping_list(plan: dict, ingredient_map: dict, days: list[int] | None = None) -> dict:
    """Aggregate a plan into ingredients and dishes. Skipped meals and eating-out meals are not groceries and are excluded.

    Each dish's cost is split across its ingredients (quantity x price weight), so the ingredient total equals the dish total."""
    sel = [d for d in plan["days"] if days is None or d["day"] in days]
    ing: dict[tuple, dict] = {}
    dishes: dict[str, dict] = {}
    outside = []
    for d in sel:
        for slot in MEAL_ORDER:
            for it in d["meals"][slot]["items"]:
                if it.get("external"):
                    outside.append({"day": d["day"], "meal": slot, "calories": it["calories"], "cost_inr": it["cost_inr"]})
                    continue
                name, n = it["food_name"], it["servings"]
                dr = dishes.setdefault(name, {"dish": name, "servings": 0.0, "grams": 0.0, "cost_inr": 0.0, "meals": set()})
                dr["servings"] += n
                dr["grams"] += it["grams"]
                dr["cost_inr"] += it["cost_inr"]
                dr["meals"].add((d["day"], slot))
                comps = ingredient_map.get(name) or [{"name": name, "qty": it["grams"] / max(n, 1e-9), "unit": "g",
                                                       "group": CATEGORY_GROUP.get(it.get("category", ""), "Vegetables & greens")}]
                weights = [c["qty"] * n * UNIT_GRAMS[c["unit"]] * COST_WEIGHT.get(c["group"], 1) for c in comps]
                total_w = sum(weights) or 1.0
                for c, w in zip(comps, weights):
                    rec = ing.setdefault((c["name"], c["unit"]), {"ingredient": c["name"], "unit": c["unit"], "group": c["group"],
                                                                   "quantity": 0.0, "cost_inr": 0.0, "meals": set(), "dishes": Counter()})
                    rec["quantity"] += c["qty"] * n
                    rec["cost_inr"] += it["cost_inr"] * w / total_w
                    rec["meals"].add((d["day"], slot))
                    rec["dishes"][name] += 1
    rows = sorted(ing.values(), key=lambda r: (GROUP_ORDER.index(r["group"]) if r["group"] in GROUP_ORDER else 99, -r["cost_inr"]))
    ingredients = [{"group": r["group"], "ingredient": r["ingredient"], "quantity": round(r["quantity"], 1), "unit": r["unit"],
                    "buy": buy_quantity(r["quantity"], r["unit"]), "used_in_meals": len(r["meals"]),
                    "top_dishes": [k for k, _ in r["dishes"].most_common(3)], "cost_inr": round(r["cost_inr"], 1)} for r in rows]
    dish_rows = sorted(({"dish": r["dish"], "servings": round(r["servings"], 1), "grams": round(r["grams"]),
                         "meals": len(r["meals"]), "cost_inr": round(r["cost_inr"], 1)} for r in dishes.values()), key=lambda r: -r["cost_inr"])
    return {"generated_at": datetime.now().isoformat(timespec="seconds"), "days": [d["day"] for d in sel],
            "total_cost_inr": round(sum(r["cost_inr"] for r in dishes.values()), 1),
            "ingredient_cost_inr": round(sum(r["cost_inr"] for r in ing.values()), 1),
            "ingredients": ingredients, "dishes": dish_rows, "outside_meals": outside,
            "note": "Quantities are approximate raw/as-bought amounts and costs are estimates, not live grocery prices."}


def shopping_df(sl: dict) -> pd.DataFrame:
    return pd.DataFrame([{"Group": r["group"], "Ingredient": r["ingredient"], "Quantity needed": f"{r['quantity']:,.0f} {r['unit']}",
                          "Buy (approx.)": r["buy"], "Used in meals": r["used_in_meals"], "Est. cost (₹)": r["cost_inr"]}
                         for r in sl["ingredients"]])


def dish_df(sl: dict) -> pd.DataFrame:
    return pd.DataFrame([{"Dish": r["dish"], "Servings": r["servings"], "Grams": r["grams"], "Meals": r["meals"], "Est. cost (₹)": r["cost_inr"]}
                         for r in sl["dishes"]])


def shopping_csv(sl: dict) -> str:
    df = pd.DataFrame([{"group": r["group"], "ingredient": r["ingredient"], "quantity": r["quantity"], "unit": r["unit"],
                        "buy_approx": r["buy"], "used_in_meals": r["used_in_meals"], "est_cost_inr": r["cost_inr"]} for r in sl["ingredients"]])
    return df.to_csv(index=False)


def shopping_json(sl: dict) -> str:
    return json.dumps(sl, indent=2, ensure_ascii=False)


# ------------------------------------------------------------------ downloads
def meal_plan_csv(plan: dict) -> str:
    return meal_plan_to_df(plan).to_csv(index=False)


def agent_log_json(log: list[dict]) -> str:
    return json.dumps(log, indent=2, ensure_ascii=False)


def agent_log_csv(log: list[dict]) -> str:
    return pd.DataFrame(log, columns=["timestamp", "agent_name", "action", "input_summary", "output_summary", "status", "details"]).to_csv(index=False)

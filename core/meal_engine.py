"""Meal engine. Phase 1 contains the cached food-database loader and schema check.
Meal composition and portion scaling are added in Phase 3."""
from __future__ import annotations

import json
import zlib
from collections import Counter
from copy import deepcopy
from datetime import datetime
from pathlib import Path

import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FOODS_PATH = DATA_DIR / "foods.csv"
TEMPLATES_PATH = DATA_DIR / "meal_templates.json"

REQUIRED_COLUMNS = [
    "food_id", "food_name", "category", "meal_type", "serving_label", "serving_grams",
    "calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "cost_inr", "tags",
    "is_veg", "allergens", "cuisine", "prep_time_min",
]
LIST_COLUMNS = ["meal_type", "tags", "allergens"]  # stored as ';'-separated strings


def parse_foods(path: Path = FOODS_PATH) -> pd.DataFrame:
    """Read foods.csv, validate the schema, and split list-like columns."""
    df = pd.read_csv(path)
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError(f"foods.csv is missing columns: {missing}")
    df["is_veg"] = df["is_veg"].astype(str).str.lower().eq("true")
    for col in LIST_COLUMNS:
        df[col] = df[col].fillna("none").astype(str).apply(lambda s: [t.strip() for t in s.split(";") if t.strip()])
    return df


@st.cache_data(show_spinner=False)
def load_foods() -> pd.DataFrame:
    """Cached loader used by every page/agent."""
    return parse_foods()


def food_db_summary(df: pd.DataFrame) -> dict:
    return {
        "items": len(df),
        "vegetarian": int(df["is_veg"].sum()),
        "non_vegetarian": int((~df["is_veg"]).sum()),
        "cuisines": sorted(df["cuisine"].unique().tolist()),
        "categories": df["category"].nunique(),
    }


# ------------------------------------------------------------------ constraints & filtering
MEAL_SLOTS = ["Breakfast", "Lunch", "Snack", "Dinner"]

# How each dietary preference maps onto foods.csv columns.
DIET_RULES = {
    "Vegetarian":     {"allow_non_veg": False, "allow_egg": False, "implicit_excluded_allergens": [], "excluded_tags": [],
                       "description": "Vegetarian: no meat, fish or eggs (dairy allowed)."},
    "Eggetarian":     {"allow_non_veg": False, "allow_egg": True, "implicit_excluded_allergens": [], "excluded_tags": [],
                       "description": "Eggetarian: vegetarian foods plus eggs; no meat or fish."},
    "Non-vegetarian": {"allow_non_veg": True, "allow_egg": True, "implicit_excluded_allergens": [], "excluded_tags": [],
                       "description": "Non-vegetarian: all foods allowed."},
    "Vegan":          {"allow_non_veg": False, "allow_egg": False, "implicit_excluded_allergens": ["dairy", "egg"], "excluded_tags": [],
                       "description": "Vegan: no meat, fish, eggs or dairy (ghee, curd, paneer, milk excluded)."},
    "Jain":           {"allow_non_veg": False, "allow_egg": False, "implicit_excluded_allergens": [], "excluded_tags": ["jain_unsafe"],
                       "description": "Jain: vegetarian, and dishes built on onion, garlic or potato are excluded."},
}
# Pan-Indian foods suit every regional preference.
CUISINE_MAP = {
    "North Indian": ["North Indian", "Pan-Indian"],
    "South Indian": ["South Indian", "Pan-Indian"],
    "Mixed Indian": ["North Indian", "South Indian", "Pan-Indian"],
}


def _diet_mask(df: pd.DataFrame, c: dict) -> pd.Series:
    rules = c["diet_rules"]
    if rules["allow_non_veg"]:
        ok = pd.Series(True, index=df.index)
    else:
        ok = df["is_veg"].copy()
        if rules["allow_egg"]:  # egg dishes are flagged non-veg in foods.csv
            ok |= df["allergens"].apply(lambda a: "egg" in a)
    for tag in rules["excluded_tags"]:
        ok &= ~df["tags"].apply(lambda t, tag=tag: tag in t)
    return ok


def _allergen_mask(df: pd.DataFrame, c: dict) -> pd.Series:
    banned = set(c["excluded_allergens"])
    return ~df["allergens"].apply(lambda a: bool(banned & set(a)))


def _cuisine_mask(df: pd.DataFrame, c: dict) -> pd.Series:
    return df["cuisine"].isin(c["allowed_cuisines"])


def _prep_mask(df: pd.DataFrame, c: dict) -> pd.Series:
    return df["prep_time_min"] <= c["max_prep_time_min"]


def _dislike_mask(df: pd.DataFrame, c: dict) -> pd.Series:
    return ~df["food_name"].isin(c.get("excluded_foods", []))


_FILTER_STEPS = [
    ("Dietary preference", _diet_mask),
    ("Allergens", _allergen_mask),
    ("Cuisine", _cuisine_mask),
    ("Cooking time", _prep_mask),
    ("Disliked foods", _dislike_mask),
]


def filter_foods(df: pd.DataFrame, constraints: dict) -> pd.DataFrame:
    """Apply diet, allergen, cuisine and cooking-time constraints (the 'allergy & diet filter' tool)."""
    mask = pd.Series(True, index=df.index)
    for _, fn in _FILTER_STEPS:
        mask &= fn(df, constraints)
    return df[mask].copy()


def constraint_masks(df: pd.DataFrame, c: dict) -> dict[str, pd.Series]:
    """Per-constraint boolean masks (True = food is allowed), keyed diet/allergen/cuisine/prep."""
    return {"diet": _diet_mask(df, c), "allergen": _allergen_mask(df, c),
            "cuisine": _cuisine_mask(df, c), "prep": _prep_mask(df, c)}


def filter_funnel(df: pd.DataFrame, constraints: dict) -> list[dict]:
    """Sequentially apply each filter and report how many foods each one removed."""
    out, current = [], df
    for label, fn in _FILTER_STEPS:
        before = len(current)
        current = current[fn(current, constraints)]
        out.append({"filter": label, "removed": before - len(current), "remaining": len(current)})
    return out


def slot_pool_sizes(df: pd.DataFrame) -> dict[str, int]:
    """Number of eligible foods available for each meal slot."""
    return {s: int(df["meal_type"].apply(lambda m, s=s: s in m).sum()) for s in MEAL_SLOTS}


def exclusion_reason(food: dict, c: dict) -> str:
    """Human-readable reason why a single food row (dict) fails the constraints, or '' if it passes."""
    row = pd.DataFrame([food])
    if not _diet_mask(row, c).iloc[0]:
        if "jain_unsafe" in food["tags"] and c["diet"] == "jain":
            return "not Jain-friendly (onion/garlic/potato based)"
        return f"not allowed for a {c['diet']} diet"
    hit = sorted(set(c["excluded_allergens"]) & set(food["allergens"]))
    if hit:
        return f"contains {', '.join(h.replace('_', ' ') for h in hit)} (excluded allergen)"
    if food["cuisine"] not in c["allowed_cuisines"]:
        return f"{food['cuisine']} cuisine does not match '{c['cuisine']}'"
    if food["food_name"] in c.get("excluded_foods", []):
        return "you told the Adaptation Agent you dislike it"
    if food["prep_time_min"] > c["max_prep_time_min"]:
        return f"prep time {food['prep_time_min']} min exceeds the {c['max_prep_time_min']} min limit"
    return ""


# ------------------------------------------------------------------ templates
def parse_templates(path: Path = TEMPLATES_PATH) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)["templates"]


@st.cache_data(show_spinner=False)
def load_templates() -> list[dict]:
    return parse_templates()


# ------------------------------------------------------------------ meal composition
SLOT_SHARE = {"Breakfast": 0.25, "Lunch": 0.33, "Snack": 0.12, "Dinner": 0.30}
CAL_TOL = 0.03                      # target band when scaling portions
PROTEIN_STAGE_CAL_BAND = 0.04       # calorie band allowed while trading carbs for protein
REPEAT_PENALTY = 0.6                # score penalty per earlier use of the same template
DIET_LEVEL = {"vegetarian": 0, "vegan": 0, "jain": 0, "eggetarian": 1, "non-vegetarian": 2}
SERVING_CAP = {"Grain": 4.0, "Legume": 2.5, "Protein Dish": 2.5, "Curry": 2.5, "Dairy": 2.5, "Breakfast Dish": 2.5,
               "Vegetable": 2.0, "Snack Dish": 2.0, "Fruit": 2.0, "Nuts & Seeds": 1.5}
DEFAULT_CAP = 2.0
FIXED_CATEGORIES = {"Salad", "Condiment", "Fat"}


def _cap(category: str) -> float:
    return SERVING_CAP.get(category, DEFAULT_CAP)


def _round_half(x: float) -> float:
    return round(x * 2) / 2


def _make_item(food: dict, servings: float, role: str, fixed: bool) -> dict:
    return {
        "food_id": food["food_id"], "food_name": food["food_name"], "category": food["category"],
        "serving_label": food["serving_label"], "serving_grams": float(food["serving_grams"]),
        "cal1": float(food["calories"]), "p1": float(food["protein_g"]), "c1": float(food["carbs_g"]),
        "f1": float(food["fat_g"]), "fib1": float(food["fiber_g"]), "cost1": float(food["cost_inr"]),
        "prep_time_min": int(food["prep_time_min"]), "role": role,
        "fixed": bool(fixed or food["category"] in FIXED_CATEGORIES), "servings": float(servings), "external": False,
    }


def _sum(day: dict, key: str) -> float:
    return sum(i[key] * i["servings"] for items in day.values() for i in items)


def _scalable(day: dict) -> list[dict]:
    return [i for items in day.values() for i in items if not i["fixed"]]


def _replace_food(orig: dict, slot: str, rows: list[dict], used: set[str]) -> dict | None:
    """Pick the closest eligible replacement (same category first, then similar calories/protein)."""
    best = None
    for r in rows:
        if slot not in r["meal_type"] or r["food_name"] in used:
            continue
        dist = abs(r["calories"] - orig["calories"]) / max(orig["calories"], 1) \
            + 0.5 * abs(r["protein_g"] - orig["protein_g"]) / max(orig["protein_g"], 1)
        key = (r["category"] != orig["category"], dist, r["food_id"])
        if best is None or key < best[0]:
            best = (key, r)
    return best[1] if best else None


def _best_protein_food(slot: str, rows: list[dict], used: set[str]) -> dict | None:
    cands = [r for r in rows if slot in r["meal_type"] and r["food_name"] not in used and r["category"] not in FIXED_CATEGORIES]
    return max(cands, key=lambda r: (r["protein_g"] / max(r["calories"], 1), r["food_id"])) if cands else None


def _scale_slots(day: dict, target_cal: float) -> None:
    """Stage 1: scale each meal's portions toward its share of the daily calories."""
    for slot, items in day.items():
        fixed_cal = sum(i["cal1"] * i["servings"] for i in items if i["fixed"])
        scal = [i for i in items if not i["fixed"]]
        base = sum(i["cal1"] * i["servings"] for i in scal)
        if not scal or base <= 0:
            continue
        f = min(2.0, max(0.5, (target_cal * SLOT_SHARE[slot] - fixed_cal) / base))
        for i in scal:
            i["servings"] = min(_cap(i["category"]), max(0.5, _round_half(i["servings"] * f)))


def _adjust_calories(day: dict, target_cal: float) -> None:
    """Stage 2: half-serving moves that reduce the daily calorie error (protein-dense items preferred when adding)."""
    for _ in range(60):
        cal = _sum(day, "cal1")
        dev = (cal - target_cal) / target_cal
        if abs(dev) <= CAL_TOL:
            return
        step = 0.5 if dev < 0 else -0.5
        best = None
        for it in _scalable(day):
            new = it["servings"] + step
            if new < 0.5 or new > _cap(it["category"]):
                continue
            after = abs((cal + step * it["cal1"] - target_cal) / target_cal)
            if after >= abs(dev):
                continue
            dens = it["p1"] / max(it["cal1"], 1)
            key = (after - 0.02 * dens) if step > 0 else (after + 0.02 * dens)
            if best is None or key < best[0]:
                best = (key, it)
        if best is None:
            return
        best[1]["servings"] += step


def _improve_protein(day: dict, target_p: float, target_cal: float, band: float = PROTEIN_STAGE_CAL_BAND) -> None:
    """Stage 3: swap half-servings from low-protein to protein-dense items while staying within ±4% calories."""
    for _ in range(25):
        p, cal = _sum(day, "p1"), _sum(day, "cal1")
        if p >= target_p:
            return
        items = _scalable(day)
        best = None
        for inc in items:
            if inc["p1"] <= 0 or inc["servings"] + 0.5 > _cap(inc["category"]):
                continue
            for dec in [None, *items]:
                dp, dc = 0.5 * inc["p1"], 0.5 * inc["cal1"]
                if dec is not None:
                    if dec is inc or dec["servings"] - 0.5 < 0.5:
                        continue
                    dp -= 0.5 * dec["p1"]
                    dc -= 0.5 * dec["cal1"]
                if dp < 0.5 or abs((cal + dc - target_cal) / target_cal) > band:
                    continue
                key = (dp, -abs(cal + dc - target_cal))
                if best is None or key > best[0]:
                    best = (key, inc, dec)
        if best is None:
            return
        best[1]["servings"] += 0.5
        if best[2] is not None:
            best[2]["servings"] -= 0.5


def _resolve_template(tpl: dict, foods_by_name: dict, rows: list[dict], eligible_names: set, constraints: dict):
    """Turn a template into concrete items, substituting any item that fails the user's constraints."""
    day, subs = {}, []
    for slot, entries in tpl["slots"].items():
        items, used = [], set()
        for e in entries:
            food = foods_by_name[e["food"]]
            if e["food"] in eligible_names and e["food"] not in used:
                chosen = food
            else:
                chosen = _replace_food(food, slot, rows, used) if e["food"] not in eligible_names else None
                if chosen is not None:
                    subs.append({"slot": slot, "original": e["food"], "replacement": chosen["food_name"],
                                 "reason": exclusion_reason(food, constraints) or "duplicate in meal"})
                else:
                    subs.append({"slot": slot, "original": e["food"], "replacement": "(dropped)",
                                 "reason": exclusion_reason(food, constraints) or "no suitable replacement"})
                    continue
            used.add(chosen["food_name"])
            items.append(_make_item(chosen, e["servings"], e["role"], e["fixed"]))
        if not items:  # whole meal filtered out -> fill with the most protein-dense eligible food
            fill = _best_protein_food(slot, rows, used)
            if fill is not None:
                items.append(_make_item(fill, 1, "main", False))
                subs.append({"slot": slot, "original": "(entire meal)", "replacement": fill["food_name"],
                             "reason": "every template item was filtered out"})
        elif not any(i["role"] == "main" for i in items):
            items[0]["role"] = "main"
        day[slot] = items
    return day, subs


def _scale_day(day: dict, targets: dict) -> None:
    _scale_slots(day, targets["calories"])
    _adjust_calories(day, targets["calories"])
    _improve_protein(day, targets["protein_g"], targets["calories"])


def _finalize_day(day: dict) -> dict:
    """Convert internal per-serving items into the output structure with totals."""
    meals, keys = {}, ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g", "cost_inr")
    day_tot = dict.fromkeys(keys, 0.0)
    for slot in SLOT_SHARE:
        out_items, tot = [], dict.fromkeys(keys, 0.0)
        for i in day.get(slot, []):
            n = i["servings"]
            vals = {"calories": i["cal1"] * n, "protein_g": i["p1"] * n, "carbs_g": i["c1"] * n,
                    "fat_g": i["f1"] * n, "fiber_g": i["fib1"] * n, "cost_inr": i["cost1"] * n}
            for k in keys:
                tot[k] += vals[k]
            out_items.append({
                "food_id": i["food_id"], "food_name": i["food_name"], "category": i["category"], "role": i["role"],
                "servings": n, "serving_label": i["serving_label"], "grams": round(i["serving_grams"] * n),
                "prep_time_min": i["prep_time_min"], "fixed": i["fixed"], "external": i.get("external", False), **{k: round(v, 1) for k, v in vals.items()},
            })
        meals[slot] = {"items": out_items, "totals": {k: round(v, 1) for k, v in tot.items()}}
        for k in keys:
            day_tot[k] += tot[k]
    return {"meals": meals, "totals": {k: round(v, 1) for k, v in day_tot.items()}}


def _mains(day: dict) -> list[str]:
    return [i["food_name"] for items in day.values() for i in items if i["role"] == "main"]


def _day_score(day: dict, subs: list, targets: dict, daily_budget: float) -> float:
    cal, p, fib, cost = _sum(day, "cal1"), _sum(day, "p1"), _sum(day, "fib1"), _sum(day, "cost1")
    score = 2.0 * min(p / targets["protein_g"], 1.1) + 1.0 * min(fib / targets["fiber_g"], 1.1)
    score -= 3.0 * abs(cal - targets["calories"]) / targets["calories"]
    score -= 0.8 * cost / daily_budget + 2.0 * max(0.0, cost - daily_budget) / daily_budget
    return score - 0.35 * len(subs)


def summarize_plan(days: list[dict], targets: dict, constraints: dict) -> dict:
    """Weekly aggregates, main-dish counts and planner self-check notes for a list of finalized days."""
    weekly_cost = round(sum(x["totals"]["cost_inr"] for x in days), 1)
    n = len(days)
    weekly = {
        "cost_inr": weekly_cost, "budget_inr": constraints["budget_weekly_inr"],
        "within_budget": weekly_cost <= constraints["budget_weekly_inr"],
        "avg_calories": round(sum(x["totals"]["calories"] for x in days) / n),
        "avg_protein_g": round(sum(x["totals"]["protein_g"] for x in days) / n, 1),
        "avg_fiber_g": round(sum(x["totals"]["fiber_g"] for x in days) / n, 1),
    }
    warnings = []
    if not weekly["within_budget"]:
        warnings.append(f"Estimated weekly cost ₹{weekly_cost:,.0f} exceeds the ₹{weekly['budget_inr']:,} budget.")
    for x in days:
        t = x["totals"]
        if abs(t["calories"] - targets["calories"]) > 0.10 * targets["calories"]:
            warnings.append(f"Day {x['day']}: calories {t['calories']:.0f} are outside ±10% of target.")
        if t["protein_g"] < 0.9 * targets["protein_g"]:
            warnings.append(f"Day {x['day']}: protein {t['protein_g']:.0f} g is below 90% of target.")
    counts = Counter(i["food_name"] for x in days for m in x["meals"].values() for i in m["items"] if i["role"] == "main")
    return {"weekly": weekly, "main_dish_counts": dict(counts), "warnings": warnings}


def generate_meal_plan(targets: dict, constraints: dict, foods: pd.DataFrame, templates: list[dict], variation: int = 0) -> dict:
    """Deterministically build a 7-day plan from templates (no LLM).

    `variation` rotates tie-breaking so 'Regenerate' yields a different but equally valid plan."""
    eligible = filter_foods(foods, constraints)
    rows = eligible.to_dict("records")
    eligible_names = set(eligible["food_name"])
    foods_by_name = {r["food_name"]: r for r in foods.to_dict("records")}
    level = DIET_LEVEL[constraints["diet"]]
    daily_budget = constraints["daily_budget_inr"]

    usable = [t for t in templates
              if DIET_LEVEL[t["diet"]] <= level and t["cuisine"] in constraints["allowed_cuisines"]]
    warnings: list[str] = []
    if not usable or not rows:
        return {"days": [], "warnings": ["No usable templates or foods for these constraints."], "substitutions": [],
                "validation_status": "not_validated", "revision": 0}

    composed = {}
    for tpl in usable:
        day, subs = _resolve_template(tpl, foods_by_name, rows, eligible_names, constraints)
        _scale_day(day, targets)
        composed[tpl["id"]] = {"tpl": tpl, "day": day, "subs": subs, "mains": _mains(day),
                               "score": _day_score(day, subs, targets, daily_budget)}

    usage, main_counts, prev = Counter(), Counter(), None
    tiers = [(2, True, True), (3, True, True), (3, False, True), (99, False, False)]
    days, all_subs = [], []
    for d in range(1, 8):
        pick = None
        for cap, check_mains, no_prev in tiers:
            cands = []
            for tid, c in composed.items():
                if usage[tid] >= cap or (no_prev and tid == prev):
                    continue
                if check_mains and any(main_counts[m] >= 2 for m in c["mains"]):
                    continue
                jitter = (zlib.crc32(f"{tid}|{d}|{variation}".encode()) % 1000) / 1000 * (0.9 if variation else 0.0)
                cands.append((c["score"] - REPEAT_PENALTY * usage[tid] + jitter, tid))
            if cands:
                pick = max(cands)[1]
                if (cap, check_mains, no_prev) != tiers[0]:
                    warnings.append(f"Day {d}: limited template variety forced a relaxed repeat rule.")
                break
        c = composed[pick]
        usage[pick] += 1
        prev = pick
        main_counts.update(c["mains"])
        fin = _finalize_day(deepcopy(c["day"]))
        t = fin["totals"]
        dev = (t["calories"] - targets["calories"]) / targets["calories"]
        note = (f"Template '{c['tpl']['name']}' ({pick}) fit best: {t['calories']:.0f} kcal ({dev:+.0%} vs target), "
                f"{t['protein_g']:.0f} g protein ({t['protein_g'] / targets['protein_g']:.0%} of target), ₹{t['cost_inr']:.0f}.")
        if c["subs"]:
            note += f" {len(c['subs'])} item(s) were substituted to respect your constraints."
        days.append({"day": d, "template_id": pick, "template_name": c["tpl"]["name"], **fin, "rationale": note})
        all_subs += [{"day": d, **s} for s in c["subs"]]

    agg = summarize_plan(days, targets, constraints)
    warnings += agg["warnings"]

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "variation": variation, "revision": 0, "validation_status": "not_validated",
        "targets_used": {k: targets[k] for k in ("calories", "protein_g", "carbs_g", "fat_g", "fiber_g")},
        "days": days, "weekly": agg["weekly"], "main_dish_counts": agg["main_dish_counts"],
        "templates_considered": len(usable), "eligible_foods": len(rows),
        "substitutions": all_subs, "warnings": warnings,
    }

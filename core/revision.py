"""Deterministic plan revision driven by the Validator's structured feedback (no LLM)."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy

import pandas as pd

from core.meal_engine import (
    FIXED_CATEGORIES, SLOT_SHARE, _adjust_calories, _best_protein_food, _cap, _finalize_day, _improve_protein,
    _make_item, _mains, _replace_food, _round_half, _sum, filter_foods, summarize_plan,
)
from core.validator import validate_plan

MAX_REVISIONS = 2
REVISION_CAL_BAND = 0.06     # calorie band allowed while trading carbs for protein
PROTEIN_GOAL_RATIO = 1.0     # revision aims for the full protein target, validator only demands 90%


# Categories that can stand in for each other when swapping dishes (paneer curry <-> soya curry <-> dal).
CATEGORY_GROUP = {"Legume": "main_dish", "Curry": "main_dish", "Protein Dish": "main_dish",
                  "Dairy": "dairy", "Dairy Alternative": "dairy"}


def _group(category: str) -> str:
    return CATEGORY_GROUP.get(category, category)


# ------------------------------------------------------------------ plan <-> internal conversion
def _food_from_item(i: dict) -> dict:
    """Rebuild a per-serving food record from a plan item (used for items not in foods.csv, e.g. eating-out estimates)."""
    n = max(i["servings"], 1e-9)
    return {"food_id": i["food_id"], "food_name": i["food_name"], "category": i.get("category", "Other"),
            "serving_label": i["serving_label"], "serving_grams": i["grams"] / n, "calories": i["calories"] / n,
            "protein_g": i["protein_g"] / n, "carbs_g": i["carbs_g"] / n, "fat_g": i["fat_g"] / n,
            "fiber_g": i["fiber_g"] / n, "cost_inr": i["cost_inr"] / n, "prep_time_min": i.get("prep_time_min", 0)}


def _day_from_plan(day: dict, foods_by_id: dict) -> dict:
    out = {}
    for slot, meal in day["meals"].items():
        items = []
        for i in meal["items"]:
            f = foods_by_id.get(i["food_id"]) or _food_from_item(i)
            ext = i.get("external", False)
            it = _make_item(f, i["servings"], i["role"], i.get("fixed", False) or ext)
            it["external"] = ext
            items.append(it)
        out[slot] = items
    return out


MEAL_FLAGS = ("skipped", "skipped_items", "ate_out", "replaced_items")


def carry_meal_flags(old_day: dict, fin: dict) -> None:
    """Keep user-reported meal markers (skipped / ate out) when a day is rebuilt."""
    for slot, meal in old_day["meals"].items():
        for k in MEAL_FLAGS:
            if k in meal:
                fin["meals"][slot][k] = meal[k]


def _rescale(servings_from: dict, new_food: dict, key_cal: float) -> float:
    """Servings of new_food giving roughly the same calories as the item it replaces."""
    return min(_cap(new_food["category"]), max(0.5, _round_half(key_cal / max(new_food["calories"], 1))))


# ------------------------------------------------------------------ edit primitives
def _swap_for_protein(day: dict, rows: list[dict], used_slots_mains: set[str]) -> str | None:
    """Replace the side item whose swap gains the most protein at equal calories. Returns an action text."""
    best = None
    for slot, items in day.items():
        names = {i["food_name"] for i in items}
        for idx, it in enumerate(items):
            if it["fixed"] or it["role"] == "main" or it["category"] == "Grain":
                continue  # never swap staples (roti/rice) or the main dish
            dens = it["p1"] / max(it["cal1"], 1)
            cal_total = it["cal1"] * it["servings"]
            for r in rows:
                if slot not in r["meal_type"] or r["category"] in FIXED_CATEGORIES or r["food_name"] in names:
                    continue
                if slot != "Snack" and _group(r["category"]) != _group(it["category"]):
                    continue  # keep meals coherent: like-for-like outside the snack slot
                if r["protein_g"] / max(r["calories"], 1) < max(1.3 * dens, dens + 0.02):
                    continue
                serv = _rescale(None, r, cal_total)
                gain = r["protein_g"] * serv - it["p1"] * it["servings"]
                if gain < 4:
                    continue
                key = (gain, -r["cost_inr"] * serv)
                if best is None or key > best[0]:
                    best = (key, slot, idx, r, serv, gain)
    if best is None:
        return None
    _, slot, idx, r, serv, gain = best
    old = day[slot][idx]
    day[slot][idx] = _make_item(r, serv, old["role"], False)
    return f"{slot}: replaced {old['food_name']} with {r['food_name']} (+{gain:.0f} g protein at similar calories)"


def _add_fiber(day: dict, rows: list[dict]) -> str | None:
    best = None
    for slot in ("Lunch", "Dinner"):
        names = {i["food_name"] for i in day.get(slot, [])}
        for r in rows:
            if slot in r["meal_type"] and r["category"] in ("Salad", "Vegetable") and r["food_name"] not in names and r["fiber_g"] >= 2:
                key = (r["fiber_g"] / max(r["calories"], 1), -r["cost_inr"], r["food_id"])
                if best is None or key > best[0]:
                    best = (key, slot, r)
    if best is None:
        return None
    _, slot, r = best
    day[slot].append(_make_item(r, 1, "side", r["category"] in FIXED_CATEGORIES))
    return f"{slot}: added {r['food_name']} (+{r['fiber_g']:g} g fiber)"


def _replace_main(day: dict, slot: str, dish: str, rows: list[dict], counts: Counter) -> str | None:
    items = day[slot]
    idx = next((k for k, i in enumerate(items) if i["food_name"] == dish and i["role"] == "main"), None)
    if idx is None:
        return None
    old, names, best = items[idx], {i["food_name"] for i in items}, None
    for r in rows:
        if slot not in r["meal_type"] or _group(r["category"]) != _group(old["category"]) or r["category"] in FIXED_CATEGORIES:
            continue
        if r["food_name"] in names or counts[r["food_name"]] >= 2:
            continue
        dist = abs(r["calories"] - old["cal1"]) / max(old["cal1"], 1) + abs(r["protein_g"] - old["p1"]) / max(old["p1"], 1) \
            + 0.002 * r["cost_inr"]
        if best is None or (dist, r["food_id"]) < (best[0], best[1]["food_id"]):
            best = (dist, r)
    if best is None:
        return None
    r = best[1]
    items[idx] = _make_item(r, _rescale(None, r, old["cal1"] * old["servings"]), "main", False)
    counts[dish] -= 1
    counts[r["food_name"]] += 1
    return f"{slot}: replaced repeated main {dish} with {r['food_name']}"


def _reduce_cost(days: list[dict], rows: list[dict], budget: float, counts: Counter) -> tuple[list[tuple[int, str]], set[int]]:
    """Greedy cheapest-swap loop: always take the swap with the largest saving that keeps protein."""
    actions, touched = [], set()
    for _ in range(40):
        if sum(_sum(d, "cost1") for d in days) <= budget:
            break
        best = None
        for di, day in enumerate(days):
            for slot, items in day.items():
                names = {i["food_name"] for i in items}
                for idx, it in enumerate(items):
                    if it["fixed"]:
                        continue
                    cur = it["cost1"] * it["servings"]
                    for r in rows:
                        if slot not in r["meal_type"] or _group(r["category"]) != _group(it["category"]) or r["food_name"] in names:
                            continue
                        if r["protein_g"] < 0.8 * it["p1"] or r["cost_inr"] >= it["cost1"]:
                            continue
                        if it["role"] == "main" and counts[r["food_name"]] >= 2:
                            continue
                        serv = _rescale(None, r, it["cal1"] * it["servings"])
                        saving = cur - r["cost_inr"] * serv
                        if saving > 0.5 and (best is None or saving > best[0]):
                            best = (saving, di, slot, idx, r, serv)
        if best is None:
            break
        saving, di, slot, idx, r, serv = best
        old = days[di][slot][idx]
        if old["role"] == "main":
            counts[old["food_name"]] -= 1
            counts[r["food_name"]] += 1
        days[di][slot][idx] = _make_item(r, serv, old["role"], False)
        touched.add(di)
        actions.append((di + 1, f"{slot}: replaced {old['food_name']} with cheaper {r['food_name']} (saves ₹{saving:.0f})"))
    return actions, touched


# ------------------------------------------------------------------ main entry points
def revise_plan(plan: dict, issues: list[dict], targets: dict, constraints: dict, foods: pd.DataFrame, attempt: int = 1):
    """Apply fixes for the validator's issues. Returns (new_plan, actions)."""
    rows = filter_foods(foods, constraints).to_dict("records")
    foods_by_id = {r["food_id"]: r for r in foods.to_dict("records")}
    days = [_day_from_plan(d, foods_by_id) for d in plan["days"]]
    counts = Counter(m for d in days for m in _mains(d))
    actions: list[dict] = []
    touched: set[int] = set()

    def log(day_no, kind, text):
        actions.append({"attempt": attempt, "day": day_no, "type": kind, "description": text})
        if day_no:
            touched.add(day_no - 1)

    # 1) hard fixes: unsafe / non-compliant items, then missing meals
    for iss in [i for i in issues if i["action"] == "replace_item"]:
        day, slot = days[iss["day"] - 1], iss["slot"]
        pos = next((k for k, i in enumerate(day[slot]) if i["food_id"] == iss["food_id"]), None)
        if pos is None:
            continue
        old = day[slot][pos]
        used = {i["food_name"] for i in day[slot]}
        new = _replace_food(foods_by_id[old["food_id"]], slot, rows, used)
        if new is None:
            day[slot].pop(pos)
            log(iss["day"], "remove_item", f"Day {iss['day']} {slot}: removed {old['food_name']} (no compliant replacement)")
        else:
            if old["role"] == "main":
                counts[old["food_name"]] -= 1
                counts[new["food_name"]] += 1
            day[slot][pos] = _make_item(new, _rescale(None, new, old["cal1"] * old["servings"]), old["role"], False)
            log(iss["day"], "replace_item", f"Day {iss['day']} {slot}: replaced {old['food_name']} with {new['food_name']} ({iss['check']} fix)")
    for iss in [i for i in issues if i["action"] == "fill_missing_meal"]:
        day, slot = days[iss["day"] - 1], iss["slot"]
        if not day.get(slot):
            fill = _best_protein_food(slot, rows, set())
            if fill:
                day[slot] = [_make_item(fill, 1, "main", False)]
                counts[fill["food_name"]] += 1
                log(iss["day"], "fill_meal", f"Day {iss['day']}: added {fill['food_name']} as {slot.lower()}")

    # 2) variety
    for iss in [i for i in issues if i["action"] == "replace_repeated_main"]:
        text = _replace_main(days[iss["day"] - 1], iss["slot"], iss["food_name"], rows, counts)
        if text:
            log(iss["day"], "replace_repeat", f"Day {iss['day']} {text}")

    # 3) budget
    for iss in [i for i in issues if i["action"] == "reduce_cost"]:
        acts, t = _reduce_cost(days, rows, constraints["budget_weekly_inr"], counts)
        for d_no, text in acts:
            log(d_no, "reduce_cost", f"Day {d_no} {text}")
        touched |= t

    # 4) per-day nutrition issues
    flagged = {i["day"] - 1 for i in issues if i["action"] in ("rebalance_calories", "increase_protein", "increase_fiber")}
    touched |= flagged
    fiber_days = {i["day"] - 1 for i in issues if i["action"] == "increase_fiber"}
    for di in sorted(touched):
        day, d_no = days[di], di + 1
        if di in fiber_days or _sum(day, "fib1") < targets["fiber_g"]:
            for _ in range(2):
                if _sum(day, "fib1") >= targets["fiber_g"]:
                    break
                text = _add_fiber(day, rows)
                if text:
                    log(d_no, "increase_fiber", f"Day {d_no} {text}")
        _adjust_calories(day, targets["calories"])
        goal_p = PROTEIN_GOAL_RATIO * targets["protein_g"]
        _improve_protein(day, goal_p, targets["calories"], REVISION_CAL_BAND)
        for _ in range(3):
            if _sum(day, "p1") >= 0.95 * targets["protein_g"]:
                break
            text = _swap_for_protein(day, rows, set())
            if not text:
                break
            log(d_no, "increase_protein", f"Day {d_no} {text}")
            _adjust_calories(day, targets["calories"])
            _improve_protein(day, goal_p, targets["calories"], REVISION_CAL_BAND)
        if di in flagged or di in touched:
            log(d_no, "rescale", f"Day {d_no}: portions rebalanced to {_sum(day, 'cal1'):.0f} kcal / {_sum(day, 'p1'):.0f} g protein")

    new_days = []
    for old_day, day, di in zip(plan["days"], days, range(len(days))):
        fin = _finalize_day(day)
        carry_meal_flags(old_day, fin)
        entry = {**old_day, **fin}
        if di in touched:
            entry["revision_notes"] = [*old_day.get("revision_notes", []), *[a["description"] for a in actions if a["day"] == di + 1]]
        new_days.append(entry)
    new_plan = {**plan, "days": new_days, "revision": attempt, **summarize_plan(new_days, targets, constraints)}
    return new_plan, actions


def plan_metrics(plan: dict) -> dict:
    n = len(plan["days"])
    return {
        "avg_calories": round(sum(d["totals"]["calories"] for d in plan["days"]) / n),
        "avg_protein_g": round(sum(d["totals"]["protein_g"] for d in plan["days"]) / n, 1),
        "avg_carbs_g": round(sum(d["totals"]["carbs_g"] for d in plan["days"]) / n, 1),
        "avg_fat_g": round(sum(d["totals"]["fat_g"] for d in plan["days"]) / n, 1),
        "avg_fiber_g": round(sum(d["totals"]["fiber_g"] for d in plan["days"]) / n, 1),
        "weekly_cost_inr": round(sum(d["totals"]["cost_inr"] for d in plan["days"]), 1),
        "per_day": [{"day": d["day"], **{k: d["totals"][k] for k in ("calories", "protein_g", "fiber_g", "cost_inr")}} for d in plan["days"]],
    }


def _rank(report: dict) -> tuple:
    """Higher is better: fewer failed checks, then fewer critical failures, then score."""
    return (-report["counts"]["fail"], -len(report["critical_failures"]), report["score"])


def revise_until_valid(plan: dict, targets: dict, constraints: dict, foods: pd.DataFrame, max_revisions: int = MAX_REVISIONS,
                       validate_fn=None, revise_fn=None) -> dict:
    """Validator → (Meal Planner revision → Validator) × up to `max_revisions`.

    validate_fn(plan, attempt, final) -> report and revise_fn(plan, report, attempt) -> (plan, actions) let the agent layer
    add logging; defaults are the pure functions. Returns the best plan seen plus the full history."""
    validate_fn = validate_fn or (lambda p, a, final: validate_plan(p, targets, constraints, foods))
    revise_fn = revise_fn or (lambda p, rep, a: revise_plan(p, rep["issues"], targets, constraints, foods, a))

    history, current, initial_report = [], plan, None
    best_plan, best_report, best_attempt = None, None, 0
    for attempt in range(max_revisions + 1):
        final = attempt == max_revisions
        report = validate_fn(current, attempt, final)
        initial_report = initial_report or report
        history.append({"attempt": attempt, "status": report["overall_status"], "label": report["overall_label"],
                        "score": report["score"], "counts": report["counts"], "issues": report["feedback"],
                        "actions": [], "metrics": plan_metrics(current)})
        if best_report is None or _rank(report) > _rank(best_report):
            best_plan, best_report, best_attempt = current, report, attempt
        if not report["issues"] or final:   # nothing actionable left, or revisions exhausted
            break
        current, actions = revise_fn(current, report, attempt + 1)
        history[-1]["actions"] = actions   # actions taken in response to this attempt's feedback
    best_plan = {**best_plan, "validation_status": best_report["overall_status"]}
    return {"plan": best_plan, "report": best_report, "history": history, "revisions_used": len(history) - 1,
            "best_attempt": best_attempt, "initial_report": initial_report}
